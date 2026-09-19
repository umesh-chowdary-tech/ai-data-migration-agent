"""SQLite persistence: runs, live events, mappings, records, escalations, audit trail, learned rules.

Everything the agent does is written here first; the UI only ever reads from these tables.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT, created_at REAL, status TEXT, stage TEXT,
    files_json TEXT, error TEXT, finished_at REAL, ai_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, ts REAL, stage TEXT, level TEXT, category TEXT,
    message TEXT, data_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, file TEXT, source_column TEXT, target_field TEXT,
    confidence REAL, method TEXT, status TEXT, candidates_json TEXT, reason TEXT, samples_json TEXT,
    date_format TEXT
);
CREATE TABLE IF NOT EXISTS records (
    run_id INTEGER, key TEXT, status TEXT, data_json TEXT, sources_json TEXT,
    changes_json TEXT, issues_json TEXT, validation_attempts INTEGER DEFAULT 0,
    push_op TEXT, push_attempts INTEGER DEFAULT 0, last_error TEXT, updated_at REAL,
    PRIMARY KEY (run_id, key)
);
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, type TEXT, status TEXT, blocking INTEGER, title TEXT, question TEXT, reason TEXT,
    context_json TEXT, proposal_json TEXT, options_json TEXT, record_keys_json TEXT,
    resolution_json TEXT, resolved_by TEXT, created_at REAL, resolved_at REAL, dedupe_key TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, ts REAL, actor TEXT, action TEXT, entity TEXT,
    before_json TEXT, after_json TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_run ON audit(run_id, id);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, key TEXT, value_json TEXT, description TEXT, created_by TEXT,
    created_at REAL, source_run INTEGER, times_applied INTEGER DEFAULT 0,
    UNIQUE(kind, key)
);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
        if "ai_json" not in cols:  # added later: which AI answered during the run (or that none did)
            conn.execute("ALTER TABLE runs ADD COLUMN ai_json TEXT")


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()


def execute_count(sql: str, params: Iterable[Any] = ()) -> int:
    """Like execute, but returns the number of rows changed (for compare-and-set updates)."""
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def now() -> float:
    return time.time()


def reset_all() -> None:
    """Demo helper: wipe runs, events, rules - everything the agent has stored."""
    with _lock:
        conn = connect()
        try:
            for table in ("runs", "events", "mappings", "records", "escalations", "audit", "rules"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence")
            conn.commit()
        finally:
            conn.close()
