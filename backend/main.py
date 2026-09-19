"""HTTP API + live event stream for the supervision UI. Also mounts the mock target API at /target-api."""
from __future__ import annotations

import asyncio
import collections
import csv
import io
import json
import re
import shutil
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mock_api.app import app as mock_app

from . import config, db
from .agent import escalations as esc
from .agent import pipeline, policy, resolve, rules
from .agent.llm import get as get_llm, reload_from_env as reload_llm
from .agent.records import Record
from .agent.target import get_client
from .schema import load as load_schema

db.init()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Find out right away whether the AI providers work, so the UI can say so before the first run.
    threading.Thread(target=lambda: get_llm().check(), daemon=True).start()
    yield


app = FastAPI(title="Data Migration Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/target-api", mock_app)

SAMPLES = {
    "v1": "Sample client export (first extract)",
    "v2": "Sample client re-export (a month later - delta demo)",
}


# ---------------------------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "llm": get_llm().status(), "target": config.TARGET_API_URL}


@app.get("/api/ai/status")
def ai_status():
    return get_llm().status()


@app.post("/api/ai/check")
def ai_check():
    """Re-read .env and re-test every AI provider now (ignores cool-downs)."""
    return reload_llm().check()


@app.get("/api/policy")
def get_policy():
    return {"policy": policy.POLICY, "thresholds": {
        "map_auto_min": policy.MAP_AUTO_MIN, "map_min_gap": policy.MAP_MIN_GAP,
        "map_ignore_below": policy.MAP_IGNORE_BELOW, "date_evidence_min": policy.DATE_EVIDENCE_MIN_MATCHES,
        "validation_attempts": policy.VALIDATION_MAX_ATTEMPTS, "push_attempts": policy.PUSH_MAX_ATTEMPTS}}


@app.get("/api/schema")
def get_schema():
    s = load_schema()
    return {"entity": s.entity, "primary_key": s.primary_key, "fields": [
        {"name": f.name, "label": f.label, "type": f.type, "required": f.required, "values": f.values,
         "description": f.description} for f in s.fields.values()]}


@app.get("/api/samples")
def samples():
    out = []
    for key, label in SAMPLES.items():
        folder = config.SAMPLES_DIR / key
        if folder.exists():
            out.append({"id": key, "label": label, "files": sorted(p.name for p in folder.iterdir())})
    return out


# ---------------------------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------------------------

def _run_summary(run_id: int) -> dict:
    counts = collections.Counter(r["status"] for r in db.query("SELECT status FROM records WHERE run_id=?", (run_id,)))
    esc_rows = db.query("SELECT status, blocking FROM escalations WHERE run_id=?", (run_id,))
    auto = db.one("SELECT COUNT(*) AS n FROM events WHERE run_id=? AND level='auto'", (run_id,))["n"]
    changes = db.query("SELECT changes_json FROM records WHERE run_id=?", (run_id,))
    auto_fixes = sum(1 for r in changes for c in (db.loads(r["changes_json"], []) or []) if c.get("by") == "agent")
    ops = collections.Counter(r["push_op"] for r in db.query("SELECT push_op FROM records WHERE run_id=? AND status='pushed'", (run_id,)))
    open_n = sum(1 for e in esc_rows if e["status"] == "open")
    return {
        "records": sum(counts.values()) - counts["merged"], "by_status": dict(counts),
        "created": ops["create"], "updated": ops["update"], "unchanged": counts["unchanged"],
        "failed": counts["failed"], "skipped": counts["skipped"], "merged": counts["merged"],
        "needs_review": counts["needs_review"], "rolled_back": counts["rolled_back"],
        "escalations_open": open_n, "escalations_total": len(esc_rows),
        "escalations_blocking": sum(1 for e in esc_rows if e["status"] == "open" and e["blocking"]),
        "auto_decisions": auto, "auto_fixes": auto_fixes,
    }


def _run_out(row: dict) -> dict:
    return {**{k: row[k] for k in ("id", "label", "created_at", "status", "stage", "error", "finished_at")},
            "files": db.loads(row["files_json"], []), "summary": _run_summary(row["id"]),
            "ai": db.loads(row.get("ai_json"), None)}


@app.get("/api/runs")
def list_runs():
    return [_run_out(r) for r in db.query("SELECT * FROM runs ORDER BY id DESC")]


ALLOWED_SUFFIXES = (".csv", ".xlsx", ".xls")


def safe_filename(raw: str | None) -> str:
    """Strip any directory part and odd characters, so an upload can never be written outside the run folder."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name or not name.lower().endswith(ALLOWED_SUFFIXES):
        raise HTTPException(400, f"{raw!r}: only CSV and Excel files are supported")
    return name


@app.post("/api/runs")
async def create_run(sample: str | None = Form(None), files: list[UploadFile] | None = File(None)):
    if not sample and not files:
        raise HTTPException(400, "Choose a sample dataset or upload at least one CSV/Excel file")
    if sample and sample not in SAMPLES:  # never treat user input as a path
        raise HTTPException(404, "Unknown sample")
    if files and len(files) > config.MAX_UPLOAD_FILES:
        raise HTTPException(400, f"At most {config.MAX_UPLOAD_FILES} files per run")
    # Validate every upload fully before anything is created.
    uploads: list[tuple[str, bytes]] = []
    for up in files or []:
        name = safe_filename(up.filename)
        data = await up.read(config.MAX_UPLOAD_BYTES + 1)
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{name} is larger than {config.MAX_UPLOAD_BYTES // 2**20} MB")
        uploads.append((name, data))

    run_id = db.execute("INSERT INTO runs (label, created_at, status, stage, files_json) VALUES (?,?,?,?,?)",
                        (SAMPLES.get(sample or "", "Uploaded files"), db.now(), "queued", "ingest", "[]"))
    folder = pipeline.files_dir(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    if sample:
        for p in sorted((config.SAMPLES_DIR / sample).iterdir()):
            if p.suffix.lower() in ALLOWED_SUFFIXES:  # sample folders also hold the evaluation ground truth
                shutil.copy(p, folder / p.name)
                names.append(p.name)
    for name, data in uploads:
        (folder / name).write_bytes(data)
        names.append(name)
    db.execute("UPDATE runs SET files_json=? WHERE id=?", (db.dumps(names), run_id))
    pipeline.start(run_id)
    return {"id": run_id}


def _get_run(run_id: int) -> dict:
    row = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(404, "Run not found")
    return row


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    return _run_out(_get_run(run_id))


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: int, after: int = 0):
    rows = db.query("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, after))
    return [_event_out(r) for r in rows]


def _event_out(r: dict) -> dict:
    return {"id": r["id"], "ts": r["ts"], "stage": r["stage"], "level": r["level"], "category": r["category"],
            "message": r["message"], "data": db.loads(r["data_json"])}


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: int, request: Request):
    """Server-sent events: every new activity event, plus a heartbeat so proxies keep the line open."""
    last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

    async def gen():
        nonlocal last
        idle = 0
        while not await request.is_disconnected():
            rows = db.query("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", (run_id, last))
            for r in rows:
                last = r["id"]
                yield f"id: {r['id']}\nevent: activity\ndata: {json.dumps(_event_out(r), default=str)}\n\n"
            idle = 0 if rows else idle + 1
            if idle and idle % 30 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/escalations")
def get_escalations(run_id: int, status: str | None = None):
    return esc.list_for_run(run_id, status)


@app.get("/api/runs/{run_id}/mappings")
def get_mappings(run_id: int):
    s = load_schema()
    rows = db.query("SELECT * FROM mappings WHERE run_id=? ORDER BY id", (run_id,))
    return [{"id": r["id"], "file": r["file"], "source_column": r["source_column"], "target_field": r["target_field"],
             "target_label": s.all_mappable[r["target_field"]].label if r["target_field"] else None,
             "confidence": r["confidence"], "method": r["method"], "status": r["status"], "reason": r["reason"],
             "date_format": r["date_format"], "samples": db.loads(r["samples_json"], []),
             "candidates": db.loads(r["candidates_json"], [])} for r in rows]


@app.get("/api/runs/{run_id}/records")
def get_records(run_id: int):
    return [r.as_dict() for r in Record.load_all(run_id)]


@app.get("/api/runs/{run_id}/audit")
def get_audit(run_id: int):
    rows = db.query("SELECT * FROM audit WHERE run_id=? ORDER BY id", (run_id,))
    return [{"id": r["id"], "ts": r["ts"], "actor": r["actor"], "action": r["action"], "entity": r["entity"],
             "before": db.loads(r["before_json"]), "after": db.loads(r["after_json"]), "reason": r["reason"]}
            for r in rows]


def csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formulas: client data flows into the audit trail and must not execute in Excel."""
    s = "" if value is None else value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/api/runs/{run_id}/audit.csv")
def export_audit(run_id: int):
    _get_run(run_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time_utc", "actor", "action", "entity", "before", "after", "reason"])
    for r in get_audit(run_id):
        writer.writerow([csv_safe(x) for x in (
            datetime.fromtimestamp(r["ts"], timezone.utc).isoformat(timespec="seconds"),
            r["actor"], r["action"], r["entity"], r["before"], r["after"], r["reason"])])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="migration-run-{run_id}-audit.csv"'})


class ResolveIn(BaseModel):
    action: str
    value: Any = None
    note: str = ""
    remember: bool = True
    actor: str = "Consultant"


@app.post("/api/escalations/{esc_id}/resolve")
def resolve_escalation(esc_id: int, body: ResolveIn):
    try:
        return resolve.resolve(esc_id, body.action, body.value, body.note.strip(), body.remember,
                               body.actor.strip() or "Consultant")
    except resolve.InputError as exc:
        raise HTTPException(400, str(exc))


class KeysIn(BaseModel):
    keys: list[str] | None = None
    actor: str = "Consultant"


@app.post("/api/runs/{run_id}/retry")
def retry(run_id: int, body: KeysIn):
    _get_run(run_id)
    pipeline.in_background(run_id, pipeline.retry_failed, body.keys)
    return {"ok": True}


@app.post("/api/runs/{run_id}/rollback")
def rollback(run_id: int, body: KeysIn):
    _get_run(run_id)
    pipeline.in_background(run_id, pipeline.rollback, body.keys, body.actor.strip() or "Consultant")
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# learned rules, target view, demo reset
# ---------------------------------------------------------------------------------------------

@app.get("/api/rules")
def list_rules():
    return rules.get_all()


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int):
    rules.delete(rule_id)
    return {"ok": True}


@app.get("/api/target/employees")
def target_employees():
    res = get_client().list()
    if not res.ok:
        raise HTTPException(502, f"Target unavailable: {res.message}")
    return res.body


@app.post("/api/target/outage")
def target_outage(seconds: float = 20):
    return get_client().client.post("/admin/outage", params={"seconds": seconds}).json()


@app.get("/api/target/status")
def target_status():
    return get_client().client.get("/admin/status").json()


@app.post("/api/reset")
def reset_everything():
    """Demo helper: clear runs, learned rules and the target system (re-seeded)."""
    db.reset_all()
    shutil.rmtree(config.RUNS_DIR, ignore_errors=True)
    get_client().client.post("/admin/reset")
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# built frontend (npm run build) served from the same origin
# ---------------------------------------------------------------------------------------------

if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = config.FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(config.FRONTEND_DIST / "index.html")
