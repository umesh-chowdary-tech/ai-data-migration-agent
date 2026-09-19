"""Learned rules: human resolutions the agent re-applies automatically next time.

kinds:
  column_map      key = normalised header             value = target field | None (don't migrate)
  date_format     key = normalised header             value = "DMY" | "MDY"
  value_map       key = "<field>|<raw value>"         value = clean value
  record_value    key = "<record key>|<field>"        value = value to force on that record
  source_priority key = field                         value = source file name that wins conflicts
  duplicate       key = "<keyA>|<keyB>" (sorted)      value = "merge" | "keep_both"
  skip_record     key = record key                    value = reason
"""
from __future__ import annotations

from typing import Any

from .. import db

_MISSING = object()


def get_all(kind: str | None = None) -> list[dict]:
    rows = db.query("SELECT * FROM rules" + (" WHERE kind=?" if kind else "") + " ORDER BY id",
                    (kind,) if kind else ())
    for r in rows:
        r["value"] = db.loads(r.pop("value_json"))
    return rows


def as_map(kind: str) -> dict[str, Any]:
    return {r["key"]: r["value"] for r in get_all(kind)}


def lookup(kind: str, key: str, default: Any = _MISSING) -> Any:
    row = db.one("SELECT value_json FROM rules WHERE kind=? AND key=?", (kind, key))
    if row is None:
        return None if default is _MISSING else default
    return db.loads(row["value_json"])


def has(kind: str, key: str) -> bool:
    return db.one("SELECT 1 FROM rules WHERE kind=? AND key=?", (kind, key)) is not None


def save(kind: str, key: str, value: Any, description: str, created_by: str, run_id: int) -> None:
    db.execute(
        "INSERT INTO rules (kind, key, value_json, description, created_by, created_at, source_run) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind, key) DO UPDATE SET value_json=excluded.value_json, "
        "description=excluded.description, created_by=excluded.created_by, created_at=excluded.created_at",
        (kind, key, db.dumps(value), description, created_by, db.now(), run_id),
    )


def mark_applied(kind: str, key: str) -> None:
    db.execute("UPDATE rules SET times_applied = times_applied + 1 WHERE kind=? AND key=?", (kind, key))


def delete(rule_id: int) -> None:
    db.execute("DELETE FROM rules WHERE id=?", (rule_id,))
