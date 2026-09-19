"""Escalations: the only way the agent asks a human for something.

Every card carries enough context to decide in one glance:
  question / reason        what it's asking and why it didn't decide alone
  context                  the evidence (samples, side-by-side records, candidate scores)
  proposal                 the agent's best answer  -> "Approve"
  correct                  how the human can give a different answer (choice list or field form) -> "Correct"
  reject_label             what "Reject" means for this case (skip record / don't migrate column)
"""
from __future__ import annotations

from typing import Any

from .. import db
from .log import audit, emit

TYPE_LABELS = {
    "mapping": "Column mapping",
    "date_format": "Date format",
    "unknown_value": "Unrecognised value",
    "invalid_value": "Value can't be cleaned",
    "conflict": "Sources disagree",
    "duplicate": "Possible duplicate",
    "validation": "Failed validation twice",
    "push_failure": "Target rejected record",
}


# 'processing' = the human has decided but the effect isn't applied yet. The pipeline must still treat it as
# unresolved, otherwise it could resume (or push a record) before the decision has actually landed.
UNRESOLVED = "('open','processing')"


def create(run_id: int, type_: str, *, title: str, question: str, reason: str, context: dict,
           proposal: dict | None, correct: dict | None, reject_label: str, record_keys: list[str] | None = None,
           blocking: bool = False, dedupe_key: str | None = None, stage: str = "review") -> int | None:
    if dedupe_key and db.one(f"SELECT id FROM escalations WHERE run_id=? AND dedupe_key=? AND status IN {UNRESOLVED}",
                             (run_id, dedupe_key)):
        return None
    esc_id = db.execute(
        "INSERT INTO escalations (run_id, type, status, blocking, title, question, reason, context_json, "
        "proposal_json, options_json, record_keys_json, created_at, dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, type_, "open", int(blocking), title, question, reason, db.dumps(context), db.dumps(proposal),
         db.dumps({"correct": correct, "reject_label": reject_label}), db.dumps(record_keys or []), db.now(),
         dedupe_key),
    )
    emit(run_id, stage, "escalation", f"Needs your decision: {title}", category=type_,
         data={"escalation_id": esc_id, "reason": reason})
    audit(run_id, "agent", "escalated", ", ".join(record_keys or []) or context.get("column", title),
          after={"question": question, "proposal": (proposal or {}).get("label")}, reason=reason)
    return esc_id


def get(esc_id: int) -> dict | None:
    row = db.one("SELECT * FROM escalations WHERE id=?", (esc_id,))
    return serialize(row) if row else None


def serialize(row: dict) -> dict:
    opts = db.loads(row["options_json"], {}) or {}
    return {
        "id": row["id"], "run_id": row["run_id"], "type": row["type"], "type_label": TYPE_LABELS.get(row["type"], row["type"]),
        "status": row["status"], "blocking": bool(row["blocking"]), "title": row["title"], "question": row["question"],
        "reason": row["reason"], "context": db.loads(row["context_json"], {}), "proposal": db.loads(row["proposal_json"]),
        "correct": opts.get("correct"), "reject_label": opts.get("reject_label"),
        "record_keys": db.loads(row["record_keys_json"], []), "resolution": db.loads(row["resolution_json"]),
        "resolved_by": row["resolved_by"], "created_at": row["created_at"], "resolved_at": row["resolved_at"],
    }


def list_for_run(run_id: int, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM escalations WHERE run_id=?" + (" AND status=?" if status else "") + " ORDER BY blocking DESC, id"
    return [serialize(r) for r in db.query(sql, (run_id, status) if status else (run_id,))]


def open_for_record(run_id: int, key: str) -> list[dict]:
    rows = db.query(f"SELECT * FROM escalations WHERE run_id=? AND status IN {UNRESOLVED}", (run_id,))
    return [serialize(r) for r in rows if key in (db.loads(r["record_keys_json"], []) or [])]


def open_blocking(run_id: int) -> int:
    row = db.one(f"SELECT COUNT(*) AS n FROM escalations WHERE run_id=? AND status IN {UNRESOLVED} AND blocking=1",
                 (run_id,))
    return row["n"] if row else 0


def unresolved_count(run_id: int) -> int:
    row = db.one(f"SELECT COUNT(*) AS n FROM escalations WHERE run_id=? AND status IN {UNRESOLVED}", (run_id,))
    return row["n"] if row else 0


def claim(esc_id: int, resolution: dict, actor: str) -> bool:
    """open -> processing, atomically. False if someone else already decided this item."""
    return db.execute_count(
        "UPDATE escalations SET status='processing', resolution_json=?, resolved_by=? WHERE id=? AND status='open'",
        (db.dumps(resolution), actor, esc_id)) == 1


def reopen(esc_id: int) -> None:
    db.execute("UPDATE escalations SET status='open', resolution_json=NULL, resolved_by=NULL WHERE id=?", (esc_id,))


def mark_resolved(esc_id: int, status: str, resolution: dict, actor: str) -> None:
    db.execute("UPDATE escalations SET status=?, resolution_json=?, resolved_by=?, resolved_at=? WHERE id=?",
               (status, db.dumps(resolution), actor, db.now(), esc_id))


def field_form(fields: list[dict]) -> dict:
    return {"kind": "fields", "fields": fields}


def choice(options: list[dict[str, Any]]) -> dict:
    return {"kind": "choice", "options": options}
