"""End-to-end: v1 export -> escalations -> human decisions -> target; then v2 re-export (delta + learned rules)."""
import collections
import shutil

from backend import config, db
from backend.agent import escalations as esc
from backend.agent import pipeline, resolve
from backend.agent.records import Record


def new_run(sample: str) -> int:
    run_id = db.execute("INSERT INTO runs (label, created_at, status, stage, files_json) VALUES (?,?,?,?,?)",
                        (sample, db.now(), "queued", "ingest", "[]"))
    folder = pipeline.files_dir(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    for p in (config.SAMPLES_DIR / sample).iterdir():
        shutil.copy(p, folder / p.name)
    pipeline.run_all(run_id)
    return run_id


def open_by_type(run_id):
    out = collections.defaultdict(list)
    for e in esc.list_for_run(run_id, "open"):
        out[e["type"]].append(e)
    return out


def status_counts(run_id):
    return collections.Counter(r.status for r in Record.load_all(run_id))


def run_status(run_id):
    return db.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"]


def resolve_all_record_level(run_id):
    for e in esc.list_for_run(run_id, "open"):
        if e["type"] == "invalid_value":
            resolve.resolve(e["id"], "correct", {e["context"]["field"]: "9876501234"}, "", True, "Tester")
        elif e["type"] == "unknown_value":
            resolve.resolve(e["id"], "correct", "Operations", "", True, "Tester")
        elif e["type"] == "validation" and not e["proposal"]:
            resolve.resolve(e["id"], "correct", {"department": "Finance"}, "", True, "Tester")
        else:
            resolve.resolve(e["id"], "approve", None, "", True, "Tester")


def test_full_migration_then_delta(isolated):
    target = isolated

    # ---- v1: column-level escalations pause the run --------------------------------------------
    run1 = new_run("v1")
    blocking = open_by_type(run1)
    assert run_status(run1) == "waiting_for_input"
    assert set(blocking) == {"mapping", "date_format"}
    assert [e["context"]["column"] for e in blocking["mapping"]] == ["Contact Number"]
    dates = blocking["date_format"]
    assert [e["context"]["column"] for e in dates] == ["Joining Date"]
    assert dates[0]["proposal"]["value"] == "MDY"  # EMP0052 in payroll supports month-first

    resolve.resolve(blocking["mapping"][0]["id"], "correct", "phone", "", True, "Tester")
    assert run_status(run1) == "waiting_for_input"  # one column-level decision still open
    resolve.resolve(dates[0]["id"], "approve", None, "", True, "Tester")

    # ---- record-level escalations: only the planted ambiguities --------------------------------
    types = open_by_type(run1)
    summary = {t: sorted(k for e in es for k in e["record_keys"]) for t, es in types.items()}
    print("\nv1 escalations:", summary)
    assert summary["unknown_value"] == ["EMP0023", "EMP0031"]
    assert summary["invalid_value"] == ["EMP0017"]
    assert summary["conflict"] == ["EMP0015"]
    assert summary["duplicate"] == ["EMP0050", "EMP0056"]
    assert summary["validation"] == ["EMP0029", "EMP0051", "EMP0057", "EMP0059"]
    assert summary["push_failure"] == ["EMP0038", "EMP0042"]
    assert run_status(run1) == "needs_review"
    counts = status_counts(run1)
    assert counts["merged"] == 0 and counts["pushed"] >= 48

    # auto-handled cases must NOT have been escalated
    all_keys = {k for es in types.values() for e in es for k in e["record_keys"]}
    assert "EMP0033" not in all_keys  # "acmecorp,com" repaired on 2nd attempt
    assert "EMP0040" not in all_keys and "EMP0044" not in all_keys  # same name, different DOB
    assert "EMP0007" not in all_keys  # exact duplicate row merged

    resolve_all_record_level(run1)
    counts = status_counts(run1)
    print("v1 final:", dict(counts))
    assert not esc.list_for_run(run1, "open")
    assert run_status(run1) == "completed"
    assert counts == {"pushed": 58, "merged": 1, "skipped": 1}
    in_target = {e["employee_id"]: e for e in target.get("/employees").json()}
    assert len(in_target) == 59  # 58 + pre-seeded EMP9001
    assert in_target["EMP0029"]["email"] == "rahul.verma@acmecorp.com"
    assert in_target["EMP0057"]["email"] == "anika.kapoor@acmecorp.com"
    assert in_target["EMP0017"]["phone"] == "+919876501234"
    assert "manager_email" not in in_target["EMP0042"]

    # ---- v2 re-export: learned rules + delta ----------------------------------------------------
    run2 = new_run("v2")
    types2 = open_by_type(run2)
    print("v2 escalations:", {t: [k for e in es for k in e["record_keys"]] for t, es in types2.items()})
    assert set(types2) == {"unknown_value"}
    assert types2["unknown_value"][0]["context"]["raw"] == "Data Science"
    ops = collections.Counter(r.push_op for r in Record.load_all(run2) if r.status in ("pushed", "unchanged"))
    print("v2 push ops:", dict(ops))
    assert ops["update"] == 3 and ops["create"] == 1  # EMP0010/0020/0033 changed, EMP0061 new
    resolve.resolve(types2["unknown_value"][0]["id"], "correct", "Engineering", "", True, "Tester")
    assert run_status(run2) == "completed"

    # ---- rollback the delta run restores previous versions and removes creates -----------------
    pipeline.rollback(run2, None, "Tester")
    in_target = {e["employee_id"]: e for e in target.get("/employees").json()}
    assert "EMP0061" not in in_target and "EMP0062" not in in_target
    assert in_target["EMP0010"]["department"] == Record.load(run1, "EMP0010").data["department"]
    assert in_target["EMP0033"]["phone"] == Record.load(run1, "EMP0033").data["phone"]
    assert run_status(run2) == "rolled_back"
