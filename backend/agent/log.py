"""Live activity events (streamed to the UI) and the audit trail (what changed, by whom, why)."""
from __future__ import annotations

import time
from typing import Any

from .. import config, db

# level: info | auto (agent fixed/decided something itself) | escalation | human | success | warning | error


def emit(run_id: int, stage: str, level: str, message: str, *, category: str | None = None,
         data: Any = None, pace: bool = True) -> None:
    db.execute(
        "INSERT INTO events (run_id, ts, stage, level, category, message, data_json) VALUES (?,?,?,?,?,?,?)",
        (run_id, db.now(), stage, level, category, message, db.dumps(data) if data is not None else None),
    )
    if pace and config.STEP_DELAY:
        time.sleep(config.STEP_DELAY)


def audit(run_id: int, actor: str, action: str, entity: str, *, before: Any = None, after: Any = None,
          reason: str = "") -> None:
    db.execute(
        "INSERT INTO audit (run_id, ts, actor, action, entity, before_json, after_json, reason) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, db.now(), actor, action, entity,
         db.dumps(before) if before is not None else None,
         db.dumps(after) if after is not None else None, reason),
    )


def set_stage(run_id: int, stage: str, status: str | None = None) -> None:
    if status:
        db.execute("UPDATE runs SET stage=?, status=? WHERE id=?", (stage, status, run_id))
    else:
        db.execute("UPDATE runs SET stage=? WHERE id=?", (stage, run_id))
