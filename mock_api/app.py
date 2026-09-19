"""Stub of the client's NEW HR platform API - the system the agent pushes into.

Behaves like a real target would:
  * 201/200 on success, 404 on unknown id
  * 409 if the employee_id or email already exists  (EMP9001 "Ananya Iyer" is pre-seeded)
  * 422 if required fields are missing or manager_email doesn't belong to an existing employee
  * 503 transient failures: deterministic "first attempt fails" for some ids, plus a manual outage switch

Run standalone:  uvicorn mock_api.app:app --port 8001   (the main server also mounts it at /target-api)
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DB_PATH = pathlib.Path(os.getenv("TARGET_DB", pathlib.Path(__file__).resolve().parents[1] / "var" / "target.db"))
FLAKY = os.getenv("MOCK_FLAKY", "1") != "0"
REQUIRED = ["employee_id", "first_name", "last_name", "email", "department", "date_of_joining", "status"]
SEED = [{
    "employee_id": "EMP9001", "first_name": "Ananya", "last_name": "Iyer", "email": "ananya.iyer@acmecorp.com",
    "department": "Finance", "date_of_joining": "2021-01-04", "status": "Active", "job_title": "Finance Manager",
}]

app = FastAPI(title="Mock target HR platform")
_lock = threading.Lock()
_attempts: dict[str, int] = {}
_outage_until = 0.0


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS employees (id TEXT PRIMARY KEY, email TEXT, data TEXT, updated_at REAL)")
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    for rec in SEED:
        conn.execute("INSERT OR IGNORE INTO employees VALUES (?,?,?,?)",
                     (rec["employee_id"], rec["email"], json.dumps(rec), time.time()))
    conn.commit()


with _lock:
    _c = _conn()
    if _c.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        _seed(_c)
    _c.close()


def _all(conn) -> dict[str, dict]:
    return {r[0]: json.loads(r[1]) for r in conn.execute("SELECT id, data FROM employees ORDER BY id")}


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


def _transient(emp_id: str) -> JSONResponse | None:
    if time.time() < _outage_until:
        return _error(503, "service_unavailable", "Target platform is temporarily unavailable (simulated outage)")
    if FLAKY:
        n = _attempts.get(emp_id, 0) + 1
        _attempts[emp_id] = n
        digits = "".join(ch for ch in emp_id if ch.isdigit()) or "0"
        if int(digits) % 9 == 0 and n == 1:  # every 9th employee fails its very first call
            return _error(503, "service_unavailable", "Upstream timeout, please retry")
    return None


def _check(conn, rec: dict, emp_id: str) -> JSONResponse | None:
    missing = [f for f in REQUIRED if not rec.get(f)]
    if missing:
        return _error(422, "missing_fields", f"Missing required fields: {', '.join(missing)}", fields=missing)
    others = {k: v for k, v in _all(conn).items() if k != emp_id}
    clash = next((k for k, v in others.items() if v.get("email", "").lower() == rec["email"].lower()), None)
    if clash:
        return _error(409, "duplicate_email", f"Email {rec['email']} already belongs to {clash}",
                      existing_id=clash, existing=others[clash])
    mgr = rec.get("manager_email")
    if mgr and mgr.lower() != rec["email"].lower() and not any(v.get("email", "").lower() == mgr.lower() for v in others.values()):
        return _error(422, "manager_not_found", f"manager_email {mgr} does not match any employee in the platform",
                      field="manager_email")
    return None


@app.get("/employees")
def list_employees():
    with _lock:
        conn = _conn()
        try:
            return list(_all(conn).values())
        finally:
            conn.close()


@app.get("/employees/{emp_id}")
def get_employee(emp_id: str):
    with _lock:
        conn = _conn()
        try:
            rec = _all(conn).get(emp_id)
        finally:
            conn.close()
    return rec if rec else _error(404, "not_found", f"{emp_id} not found")


@app.post("/employees")
async def create_employee(request: Request):
    rec = await request.json()
    emp_id = str(rec.get("employee_id", ""))
    with _lock:
        if (err := _transient(emp_id)) is not None:
            return err
        conn = _conn()
        try:
            if emp_id in _all(conn):
                return _error(409, "duplicate_id", f"{emp_id} already exists", existing_id=emp_id)
            if (err := _check(conn, rec, emp_id)) is not None:
                return err
            conn.execute("INSERT INTO employees VALUES (?,?,?,?)", (emp_id, rec["email"], json.dumps(rec), time.time()))
            conn.commit()
        finally:
            conn.close()
    return JSONResponse(status_code=201, content=rec)


@app.put("/employees/{emp_id}")
async def update_employee(emp_id: str, request: Request):
    rec = await request.json()
    with _lock:
        if (err := _transient(emp_id)) is not None:
            return err
        conn = _conn()
        try:
            if emp_id not in _all(conn):
                return _error(404, "not_found", f"{emp_id} not found")
            if (err := _check(conn, rec, emp_id)) is not None:
                return err
            conn.execute("UPDATE employees SET email=?, data=?, updated_at=? WHERE id=?",
                         (rec["email"], json.dumps(rec), time.time(), emp_id))
            conn.commit()
        finally:
            conn.close()
    return rec


@app.delete("/employees/{emp_id}")
def delete_employee(emp_id: str):
    with _lock:
        if time.time() < _outage_until:
            return _error(503, "service_unavailable", "Target platform is temporarily unavailable (simulated outage)")
        conn = _conn()
        try:
            cur = conn.execute("DELETE FROM employees WHERE id=?", (emp_id,))
            conn.commit()
        finally:
            conn.close()
    return Response(status_code=204) if cur.rowcount else _error(404, "not_found", f"{emp_id} not found")


@app.post("/admin/reset")
def reset():
    global _outage_until
    with _lock:
        conn = _conn()
        try:
            conn.execute("DELETE FROM employees")
            _seed(conn)
        finally:
            conn.close()
        _attempts.clear()
        _outage_until = 0.0
    return {"ok": True}


@app.post("/admin/outage")
def outage(seconds: float = 20):
    """Simulate the target going down so retry / failure handling can be demonstrated."""
    global _outage_until
    _outage_until = time.time() + seconds if seconds > 0 else 0.0
    return {"outage_until": _outage_until}


@app.get("/admin/status")
def status():
    return {"outage": time.time() < _outage_until, "outage_seconds_left": max(0.0, _outage_until - time.time())}
