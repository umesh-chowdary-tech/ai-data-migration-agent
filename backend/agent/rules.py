"""Learned rules: decisions the agent re-applies automatically next time.

kinds:
  column_map      key = normalised header             value = target field | None (don't migrate)
  date_format     key = normalised header             value = "DMY" | "MDY"
  value_map       key = "<field>|<raw value>"         value = clean value
  record_value    key = "<record key>|<field>"        value = value to force on that record
  source_priority key = field                         value = source file name that wins conflicts
  duplicate       key = "<keyA>|<keyB>" (sorted)      value = "merge" | "keep_both"
  skip_record     key = record key                    value = reason
  issue_policy    key = "<field>|<problem code>"      value = "clear"  (see problems.py)

Every rule is a standing permission for the agent to decide alone, so every create / edit / delete is recorded in
rule_events with who, when, why - and, for manual changes, the review that preceded it.
"""
from __future__ import annotations

from typing import Any

from .. import db

_MISSING = object()

# Rules that describe a pattern in the client's data may be written by hand. Rules about one specific employee only
# come from a decision taken while looking at that employee's records (they can be edited or deleted afterwards).
PATTERN_KINDS = ("column_map", "date_format", "value_map", "source_priority", "issue_policy")
RECORD_KINDS = ("record_value", "duplicate", "skip_record")


def _row(r: dict) -> dict:
    r["value"] = db.loads(r.pop("value_json"))
    return r


def get_all(kind: str | None = None) -> list[dict]:
    rows = db.query("SELECT * FROM rules" + (" WHERE kind=?" if kind else "") + " ORDER BY id",
                    (kind,) if kind else ())
    return [_row(r) for r in rows]


def get(rule_id: int) -> dict | None:
    r = db.one("SELECT * FROM rules WHERE id=?", (rule_id,))
    return _row(r) if r else None


def find(kind: str, key: str) -> dict | None:
    r = db.one("SELECT * FROM rules WHERE kind=? AND key=?", (kind, key))
    return _row(r) if r else None


def as_map(kind: str) -> dict[str, Any]:
    return {r["key"]: r["value"] for r in get_all(kind)}


def lookup(kind: str, key: str, default: Any = _MISSING) -> Any:
    row = db.one("SELECT value_json FROM rules WHERE kind=? AND key=?", (kind, key))
    if row is None:
        return None if default is _MISSING else default
    return db.loads(row["value_json"])


def has(kind: str, key: str) -> bool:
    return db.one("SELECT 1 FROM rules WHERE kind=? AND key=?", (kind, key)) is not None


def _event(rule_id: int | None, kind: str, key: str, actor: str, action: str, before: Any, after: Any,
           reason: str | None, review: Any = None) -> None:
    db.execute(
        "INSERT INTO rule_events (rule_id, kind, key, ts, actor, action, before_json, after_json, reason, review_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rule_id, kind, key, db.now(), actor, action, db.dumps(before), db.dumps(after), reason,
         db.dumps(review) if review is not None else None))


def save(kind: str, key: str, value: Any, description: str, created_by: str, run_id: int | None, *,
         origin: str = "decision", reason: str | None = None, review: Any = None) -> int:
    existing = find(kind, key)
    db.execute(
        "INSERT INTO rules (kind, key, value_json, description, created_by, created_at, source_run, origin, reason) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(kind, key) DO UPDATE SET value_json=excluded.value_json, "
        "description=excluded.description, updated_by=excluded.created_by, updated_at=excluded.created_at, "
        "reason=excluded.reason",
        (kind, key, db.dumps(value), description, created_by, db.now(), run_id, origin, reason),
    )
    rule = find(kind, key)
    _event(rule["id"], kind, key, created_by, "edited" if existing else "created",
           existing["value"] if existing else None, value,
           reason or (f"decided on a card in run #{run_id}" if origin == "decision" else None), review)
    return rule["id"]


def mark_applied(kind: str, key: str) -> None:
    db.execute("UPDATE rules SET times_applied = times_applied + 1 WHERE kind=? AND key=?", (kind, key))


def delete(rule_id: int, actor: str = "consultant", reason: str | None = None) -> None:
    rule = get(rule_id)
    if rule:
        _event(rule_id, rule["kind"], rule["key"], actor, "deleted", rule["value"], None, reason)
    db.execute("DELETE FROM rules WHERE id=?", (rule_id,))


def history(rule_id: int) -> list[dict]:
    rows = db.query("SELECT * FROM rule_events WHERE rule_id=? ORDER BY id", (rule_id,))
    for r in rows:
        r["before"], r["after"] = db.loads(r.pop("before_json")), db.loads(r.pop("after_json"))
        r["review"] = db.loads(r.pop("review_json"))
    return rows
