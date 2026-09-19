"""Working copy of each employee record as it moves through the pipeline (persisted in SQLite)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import db

# pending -> needs_review (open escalation) -> ready -> pushed | unchanged | failed | waiting
# terminal side exits: skipped (human said don't migrate), merged (absorbed into a duplicate), rolled_back
FINAL_OK = {"pushed", "unchanged"}


@dataclass
class Record:
    run_id: int
    key: str
    status: str = "pending"
    data: dict[str, Any] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)            # [{file, row, raw}]
    field_sources: dict[str, str] = field(default_factory=dict)
    changes: list[dict] = field(default_factory=list)          # [{field, from, to, reason, by, ts}]
    issues: list[dict] = field(default_factory=list)
    validation_attempts: int = 0
    push_op: str | None = None
    push_attempts: int = 0
    last_error: str | None = None

    @property
    def name(self) -> str:
        return " ".join(x for x in (self.data.get("first_name"), self.data.get("last_name")) if x) or self.key

    def set(self, fld: str, value: Any, reason: str, by: str = "agent", source: str | None = None) -> None:
        before = self.data.get(fld)
        if before == value:
            return
        self.data[fld] = value
        self.changes.append({"field": fld, "from": before, "to": value, "reason": reason, "by": by, "ts": db.now()})
        if source or by != "agent":
            self.field_sources[fld] = source or f"set by {by}"

    def save(self) -> None:
        db.execute(
            "INSERT INTO records (run_id, key, status, data_json, sources_json, changes_json, issues_json, "
            "validation_attempts, push_op, push_attempts, last_error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, key) DO UPDATE SET status=excluded.status, data_json=excluded.data_json, "
            "sources_json=excluded.sources_json, changes_json=excluded.changes_json, issues_json=excluded.issues_json, "
            "validation_attempts=excluded.validation_attempts, push_op=excluded.push_op, "
            "push_attempts=excluded.push_attempts, last_error=excluded.last_error, updated_at=excluded.updated_at",
            (self.run_id, self.key, self.status, db.dumps(self.data),
             db.dumps({"rows": self.rows, "fields": self.field_sources}), db.dumps(self.changes),
             db.dumps(self.issues), self.validation_attempts, self.push_op, self.push_attempts, self.last_error,
             db.now()),
        )

    @classmethod
    def _from_row(cls, r: dict) -> "Record":
        src = db.loads(r["sources_json"], {}) or {}
        return cls(run_id=r["run_id"], key=r["key"], status=r["status"], data=db.loads(r["data_json"], {}),
                   rows=src.get("rows", []), field_sources=src.get("fields", {}),
                   changes=db.loads(r["changes_json"], []), issues=db.loads(r["issues_json"], []),
                   validation_attempts=r["validation_attempts"] or 0, push_op=r["push_op"],
                   push_attempts=r["push_attempts"] or 0, last_error=r["last_error"])

    @classmethod
    def load(cls, run_id: int, key: str) -> "Record | None":
        r = db.one("SELECT * FROM records WHERE run_id=? AND key=?", (run_id, key))
        return cls._from_row(r) if r else None

    @classmethod
    def load_all(cls, run_id: int, status: str | None = None) -> list["Record"]:
        sql = "SELECT * FROM records WHERE run_id=?" + (" AND status=?" if status else "") + " ORDER BY key"
        return [cls._from_row(r) for r in db.query(sql, (run_id, status) if status else (run_id,))]

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "status": self.status, "data": self.data, "rows": self.rows,
                "field_sources": self.field_sources, "changes": self.changes, "issues": self.issues,
                "validation_attempts": self.validation_attempts, "push_op": self.push_op,
                "push_attempts": self.push_attempts, "last_error": self.last_error}
