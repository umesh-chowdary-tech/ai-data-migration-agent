"""Golden-data evaluation.

For each dataset (canonical + randomised held-out sets) this runs the real agent end to end against the mock target,
answers every question it asks like a consultant who knows the truth (the "oracle"), and then scores:

  * escalation precision / recall  - did it ask about exactly the genuinely ambiguous cases?
  * autonomous decisions           - how many columns / dates / values it decided alone, and how many were WRONG
  * data correctness               - field-by-field comparison of the target system with the ground truth,
                                     separating errors a human made from "silent" errors the agent made alone
  * delta + learning (v2)          - only changed records sent, no repeated questions
  * rollback                       - target restored exactly to its pre-run state

Run through evals/run.py (which isolates the databases). Direct use:
    python -m evals.golden --datasets data/samples var/heldout/heldout-1 --out var/eval/rules.json [--ai]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIELDS = ["employee_id", "first_name", "last_name", "email", "personal_email", "phone", "emergency_contact_phone",
          "department", "job_title", "date_of_joining", "date_of_birth", "employment_type", "manager_email",
          "annual_salary", "location", "status"]


def isolate(ai: bool) -> pathlib.Path:
    """Point every database at a throwaway folder BEFORE the backend is imported."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="migration-eval-"))
    os.environ.update({"AGENT_DB": str(tmp / "agent.db"), "TARGET_DB": str(tmp / "target.db"),
                       "RUNS_DIR": str(tmp / "runs"), "AGENT_STEP_DELAY": "0", "AGENT_PUSH_DELAY": "0"})
    if not ai:
        os.environ["LLM_PROVIDERS"] = "none"
    return tmp


# ------------------------------------------------------------------------------------------------
# How an escalation is identified, so it can be matched against the ground truth
# ------------------------------------------------------------------------------------------------

def esc_key(e: dict) -> str:
    c, keys = e["context"] or {}, e["record_keys"]
    if e["type"] in ("mapping", "date_format"):
        return f"{c.get('file')}|{c.get('column')}"
    if e["type"] == "unknown_value":
        return f"{c.get('field')}|{str(c.get('raw', '')).lower()}"
    if e["type"] in ("invalid_value", "conflict"):
        return f"{keys[0]}|{c.get('field')}"
    if e["type"] == "duplicate":
        return "|".join(sorted(keys))
    return keys[0] if keys else ""


class Oracle:
    """A consultant who knows the ground truth. Approves the agent's suggestion when it is right, corrects it
    when it is wrong, rejects when the truth says 'don't migrate'."""

    def __init__(self, truth: dict):
        self.t = truth
        self.suggestions = {"right": 0, "wrong": 0, "none": 0}
        self.unanswerable: list[str] = []

    def _decide(self, e: dict, answer, allow_approve: bool = True):
        proposal = (e["proposal"] or {}).get("value", object())
        if not e["proposal"]:
            self.suggestions["none"] += 1
        elif proposal == answer:
            self.suggestions["right"] += 1
        else:
            self.suggestions["wrong"] += 1
        if allow_approve and e["proposal"] and proposal == answer:
            return "approve", None
        return "correct", answer

    def answer(self, e: dict):
        t, emp, key = e["type"], self.t["employees"], esc_key(e)
        c = e["context"] or {}
        if t == "mapping":
            target = self.t["mappings"].get(key)
            return ("reject", None) if target is None else self._decide(e, target)
        if t == "date_format":
            fmt = self.t["date_formats"].get(key)
            return self._decide(e, fmt) if fmt in ("DMY", "MDY") else ("reject", None)
        rk = e["record_keys"]
        if t == "unknown_value":
            wanted = collections.Counter(emp[k][c["field"]] for k in rk if k in emp).most_common(1)
            value = wanted[0][0] if wanted else None
            return self._decide(e, value) if value in c.get("allowed", []) else ("reject", None)
        if t == "duplicate":
            verdict = self.t["duplicates"].get(key, "keep_both")
            return self._decide(e, verdict)
        record = emp.get(rk[0]) if rk else None
        if t == "conflict":
            if record is None:
                return "reject", None
            truth = record[c["field"]]
            if truth in [o["value"] for o in c.get("options", [])]:
                return self._decide(e, truth)
            self.unanswerable.append(f"{key}: truth {truth!r} not among the options")
            return "reject", None
        if t in ("invalid_value", "validation", "push_failure"):
            if record is None:  # the truth says this employee must not be migrated
                return ("approve", None) if (e["proposal"] or {}).get("value") == "skip" else ("reject", None)
            names = [f["name"] for f in (e["correct"] or {}).get("fields", [])]
            if isinstance((e["proposal"] or {}).get("value"), dict):
                names += [n for n in e["proposal"]["value"] if n not in names]
            value = {n: record.get(n) for n in names}
            if any(v is None and n in ("first_name", "last_name", "email", "department", "date_of_joining")
                   for n, v in value.items()):
                self.unanswerable.append(f"{key}: truth has no value for a required field")
                return "reject", None
            return self._decide(e, value)
        return "reject", None


# ------------------------------------------------------------------------------------------------
# One dataset
# ------------------------------------------------------------------------------------------------

def run_dataset(folder: pathlib.Path, env) -> dict:
    db, esc, pipeline, resolve, Record, client, config = env
    import shutil

    result = {"name": "canonical" if folder.name == "samples" else folder.name}
    db.reset_all()
    client.post("/admin/reset")

    def new_run(version: int) -> int:
        src = folder / f"v{version}"
        run_id = db.execute("INSERT INTO runs (label, created_at, status, stage, files_json) VALUES (?,?,?,?,?)",
                            (f"eval {result['name']} v{version}", db.now(), "queued", "ingest", "[]"))
        dest = pipeline.files_dir(run_id)
        dest.mkdir(parents=True, exist_ok=True)
        for p in src.iterdir():
            if p.suffix in (".csv", ".xlsx"):
                shutil.copy(p, dest / p.name)
        pipeline.run_all(run_id)
        return run_id

    def settle(run_id: int, oracle: Oracle) -> int:
        decisions = 0
        for _ in range(40):
            open_items = esc.list_for_run(run_id, "open")
            if not open_items:
                break
            for e in open_items:
                action, value = oracle.answer(e)
                try:
                    resolve.resolve(e["id"], action, value, "oracle", True, "Oracle")
                except resolve.InputError as err:
                    oracle.unanswerable.append(f"{esc_key(e)}: {err}")
                    resolve.resolve(e["id"], "reject", None, "oracle fallback", True, "Oracle")
                decisions += 1
        return decisions

    def score(run_id: int, truth: dict, oracle: Oracle, decisions: int, started: float) -> dict:
        items = esc.list_for_run(run_id)
        asked = [(e["type"], esc_key(e), e["title"]) for e in items]
        expected = {(x["type"], x["key"]) for x in truth["expected_escalations"]}
        seen: set = set()
        tp, fp = [], []
        for typ, key, title in asked:
            if (typ, key) in expected and (typ, key) not in seen:
                tp.append(f"{typ}: {key}")
                seen.add((typ, key))
            else:
                fp.append(f"{typ}: {key} - {title}")
        fn = [f"{t}: {k}" for t, k in sorted(expected - seen)]

        # traps that must be handled without asking
        involved = {k for e in items for k in e["record_keys"]} | {esc_key(e) for e in items}
        traps = [{"kind": tr["kind"], "ok": not (set(tr["keys"]) & involved)} for tr in truth["auto_traps"]]

        # autonomous column + date decisions
        maps = db.query("SELECT * FROM mappings WHERE run_id=?", (run_id,))
        cols = {"total": len(maps), "auto": 0, "auto_wrong": [], "asked": 0}
        for m in maps:
            key = f"{m['file']}|{m['source_column']}"
            want = truth["mappings"].get(key)
            got = m["target_field"]
            if m["method"] in ("auto", "rule", "unmapped"):
                cols["auto"] += 1
                if got != want:
                    cols["auto_wrong"].append(f"{key}: mapped to {got}, should be {want}")
            else:
                cols["asked"] += 1
            want_fmt = truth["date_formats"].get(key)
            if want_fmt in ("DMY", "MDY") and m["date_format"] not in (want_fmt, None) and m["target_field"]:
                asked_fmt = any(e["type"] == "date_format" and esc_key(e) == key for e in items)
                if not asked_fmt:
                    cols["auto_wrong"].append(f"{key}: read as {m['date_format']}, should be {want_fmt}")

        # the target system vs the truth, field by field
        target = {r["employee_id"]: r for r in client.get("/employees").json()}
        records = {r.key: r for r in Record.load_all(run_id)}
        total = correct = 0
        wrong_human, wrong_silent, missing = [], [], []
        for key, exp in truth["employees"].items():
            act = target.get(key)
            if act is None:
                missing.append(key)
                continue
            rec = records.get(key)
            touched = {c["field"] for c in (rec.changes if rec else []) if c.get("by") not in ("agent", None)}
            for f in FIELDS:
                ev, av = exp.get(f), act.get(f)
                total += 1
                if ev == av or (ev in (None, "") and av in (None, "")):
                    correct += 1
                else:
                    (wrong_human if f in touched else wrong_silent).append(f"{key}.{f}: expected {ev!r}, got {av!r}")
        extra = sorted(k for k in target if k not in truth["employees"] and k != "EMP9001")
        retries = db.one("SELECT COUNT(*) AS n FROM audit WHERE run_id=? AND action='push_retry'", (run_id,))["n"]
        auto_events = db.one("SELECT COUNT(*) AS n FROM events WHERE run_id=? AND level='auto'", (run_id,))["n"]
        auto_fixes = sum(1 for r in records.values() for c in r.changes if c.get("by") == "agent")
        ai = db.loads(db.one("SELECT ai_json FROM runs WHERE id=?", (run_id,))["ai_json"], {}) or {}
        return {
            "employees": len(truth["employees"]), "questions": len(asked), "decisions": decisions,
            "tp": len(tp), "fp": fp, "fn": fn,
            "precision": round(len(tp) / len(asked), 3) if asked else 1.0,
            "recall": round(len(tp) / len(expected), 3) if expected else 1.0,
            "traps": traps, "columns": cols,
            "fields_total": total, "fields_correct": correct,
            "wrong_silent": wrong_silent, "wrong_human": wrong_human, "missing": missing, "extra": extra,
            "suggestions": oracle.suggestions, "unanswerable": oracle.unanswerable,
            "auto_actions": auto_events + auto_fixes, "ai_calls": ai.get("calls", 0), "ai_used": ai.get("used", {}),
            "ai_failures": len(ai.get("failures", [])), "push_retries": retries,
            "seconds": round(time.time() - started, 1),
        }

    # v1: first extract
    t0 = time.time()
    truth1 = json.loads((folder / "v1" / "ground_truth.json").read_text(encoding="utf-8"))
    oracle1 = Oracle(truth1)
    run1 = new_run(1)
    result["v1"] = score(run1, truth1, oracle1, settle(run1, oracle1), t0)
    snapshot = {r["employee_id"]: r for r in client.get("/employees").json()}

    # v2: the re-export a month later - delta + learned rules
    t0 = time.time()
    truth2 = json.loads((folder / "v2" / "ground_truth.json").read_text(encoding="utf-8"))
    oracle2 = Oracle(truth2)
    run2 = new_run(2)
    v2 = score(run2, truth2, oracle2, settle(run2, oracle2), t0)
    ops = collections.defaultdict(list)
    for r in Record.load_all(run2):
        if r.status in ("pushed", "unchanged"):
            ops[r.push_op].append(r.key)
    want = truth2["expected_delta"]
    v2["delta"] = {
        "update_expected": want["update"], "update_actual": sorted(ops["update"]),
        "create_expected": want["create"], "create_actual": sorted(ops["create"]),
        "unchanged": len(ops["unchanged"]),
        "ok": sorted(ops["update"]) == want["update"] and sorted(ops["create"]) == want["create"],
    }
    v1_keys = {esc_key(e) for e in esc.list_for_run(run1)}
    v2["repeat_questions"] = sorted(esc_key(e) for e in esc.list_for_run(run2) if esc_key(e) in v1_keys)
    result["v2"] = v2

    # rollback the delta run: the target must be byte-for-byte what it was after v1
    pipeline.rollback(run2, None, "Oracle")
    after = {r["employee_id"]: r for r in client.get("/employees").json()}
    diffs = sorted(k for k in set(snapshot) | set(after) if snapshot.get(k) != after.get(k))
    result["rollback"] = {"ok": not diffs, "differences": diffs}
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ai", action="store_true", help="use the AI providers configured in .env")
    args = ap.parse_args()
    isolate(args.ai)
    sys.path.insert(0, str(ROOT))

    from fastapi.testclient import TestClient

    from backend import config, db
    from backend.agent import escalations as esc
    from backend.agent import pipeline, policy, resolve, target
    from backend.agent.llm import get as get_llm
    from backend.agent.records import Record
    from mock_api.app import app as mock_app

    db.init()
    policy.PUSH_BACKOFF_SECONDS = 0
    client = TestClient(mock_app)
    target.factory = lambda: target.TargetClient(client)
    pipeline.in_background = lambda run_id, fn, *a: fn(run_id, *a)  # deterministic: no background threads
    llm = get_llm()
    if args.ai:
        llm.check()
    env = (db, esc, pipeline, resolve, Record, client, config)
    results = {"mode": "ai" if args.ai else "rules-only", "ai_status": llm.status(), "datasets": []}
    for d in args.datasets:
        folder = pathlib.Path(d)
        print(f"[{results['mode']}] evaluating {folder} ...", flush=True)
        results["datasets"].append(run_dataset(folder, env))
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
