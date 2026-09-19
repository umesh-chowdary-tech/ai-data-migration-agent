"""Test harness: isolated SQLite files, no LLM, no pacing, target = in-process mock API."""
import os
import pathlib
import shutil
import tempfile

TMP = pathlib.Path(tempfile.mkdtemp(prefix="migration-agent-tests-"))
os.environ.update({
    "AGENT_DB": str(TMP / "agent.db"), "TARGET_DB": str(TMP / "target.db"), "RUNS_DIR": str(TMP / "runs"),
    "LLM_PROVIDERS": "none", "AGENT_STEP_DELAY": "0", "AGENT_PUSH_DELAY": "0",  # never call real AI in tests
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import db  # noqa: E402
from backend.agent import pipeline, target  # noqa: E402
from mock_api.app import app as mock_app  # noqa: E402

import backend.agent.policy as policy  # noqa: E402

policy.PUSH_BACKOFF_SECONDS = 0


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    db.init()
    db.reset_all()
    shutil.rmtree(TMP / "runs", ignore_errors=True)  # run ids restart at 1, so old upload folders must go too
    client = TestClient(mock_app)
    client.post("/admin/reset")
    monkeypatch.setattr(target, "factory", lambda: target.TargetClient(client))
    # run background work inline so the test is deterministic
    monkeypatch.setattr(pipeline, "in_background", lambda run_id, fn, *a: fn(run_id, *a))
    yield client


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TMP, ignore_errors=True)
