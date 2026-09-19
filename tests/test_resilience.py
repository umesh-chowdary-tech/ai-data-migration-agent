"""The target system misbehaving: flaky 503s are retried automatically; a full outage fails records safely
(nothing half-written) and a later Retry finishes the job."""
import collections

from backend import db
from backend.agent import pipeline, resolve
from backend.agent.records import Record
from tests.test_end_to_end import new_run, open_by_type


def answer_column_questions(run):
    blocking = open_by_type(run)
    resolve.resolve(blocking["mapping"][0]["id"], "correct", "phone", "", False, "Tester")
    resolve.resolve(blocking["date_format"][0]["id"], "approve", None, "", False, "Tester")


def test_transient_503s_are_retried_automatically(isolated):
    run = new_run("v1")
    answer_column_questions(run)
    retries = db.query("SELECT entity FROM audit WHERE run_id=? AND action='push_retry'", (run,))
    assert retries, "the mock target fails every 9th employee's first call"
    for r in retries:
        assert Record.load(run, r["entity"]).status == "pushed"   # every retried record got through


def test_target_outage_fails_safely_and_retry_recovers(isolated):
    target = isolated
    run = new_run("v1")
    target.post("/admin/outage", params={"seconds": 600})
    answer_column_questions(run)
    counts = collections.Counter(r.status for r in Record.load_all(run))
    # managers failed after 3 attempts; their reports correctly wait instead of being pushed without a manager
    assert counts["failed"] > 0 and counts["failed"] + counts["waiting"] >= 40 and counts["pushed"] == 0
    assert [e["employee_id"] for e in target.get("/employees").json()] == ["EMP9001"]  # nothing half-written
    target.post("/admin/outage", params={"seconds": 0})
    pipeline.retry_failed(run)
    recs = Record.load_all(run)
    counts = collections.Counter(r.status for r in recs)
    assert counts["waiting"] == 0 and counts["pushed"] >= 40
    # what still fails are the target's real rejections (409/422) - each one now waits for a human decision
    still_failed = {r.key for r in recs if r.status == "failed"}
    assert still_failed == set(open_by_type(run)["push_failure"][0]["record_keys"]) | \
        set(open_by_type(run)["push_failure"][1]["record_keys"]) == {"EMP0038", "EMP0042"}
