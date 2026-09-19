"""Decisions made in quick succession, applied by real background threads, must not race each other."""
import time

from backend import db
from backend.agent import escalations as esc
from backend.agent import pipeline, resolve
from backend.agent.pipeline import in_background as REAL_IN_BACKGROUND
from backend.agent.records import Record
from tests.test_end_to_end import new_run, open_by_type


def wait_idle(run_id: int, timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        busy = db.one("SELECT COUNT(*) AS n FROM escalations WHERE run_id=? AND status='processing'", (run_id,))["n"]
        if not busy and db.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"] != "running":
            if pipeline.run_lock(run_id).acquire(timeout=0.1):
                pipeline.run_lock(run_id).release()
                return
        time.sleep(0.1)
    raise TimeoutError("run did not settle")


def test_back_to_back_column_decisions(isolated, monkeypatch):
    run = new_run("v1")
    monkeypatch.setattr(pipeline, "in_background", REAL_IN_BACKGROUND)
    blocking = open_by_type(run)
    # both answered before either background job has applied anything
    resolve.resolve(blocking["mapping"][0]["id"], "correct", "phone", "", True, "Tester")
    resolve.resolve(blocking["date_format"][0]["id"], "approve", None, "", True, "Tester")
    wait_idle(run)
    all_esc = esc.list_for_run(run)
    date_cards = [e for e in all_esc if e["type"] == "date_format"]
    assert len(date_cards) == 1, "the date question must not be asked twice"
    assert not any(e["blocking"] and e["status"] == "open" for e in all_esc)
    assert db.one("SELECT stage FROM runs WHERE id=?", (run,))["stage"] == "done"


def test_second_fix_lands_before_push(isolated, monkeypatch):
    """A record with two open questions is only pushed after BOTH decisions are applied."""
    run = new_run("v1")
    for e in esc.list_for_run(run, "open"):
        resolve.resolve(e["id"], "approve" if e["proposal"] else "correct",
                        None if e["proposal"] else "phone", "", False, "Tester")
    monkeypatch.setattr(pipeline, "in_background", REAL_IN_BACKGROUND)
    # EMP0023 and EMP0031 share one card; resolve it together with EMP0017's phone card, back to back
    cards = {tuple(e["record_keys"]): e for e in esc.list_for_run(run, "open")}
    resolve.resolve(cards[("EMP0023", "EMP0031")]["id"], "correct", "Operations", "", False, "Tester")
    resolve.resolve(cards[("EMP0017",)]["id"], "correct", {"phone": "9876501234"}, "", False, "Tester")
    wait_idle(run)
    assert Record.load(run, "EMP0017").status == "pushed"
    assert Record.load(run, "EMP0017").data["phone"] == "+919876501234"
    assert Record.load(run, "EMP0023").data["department"] == "Operations"
