"""Orchestrates a migration run as a resumable state machine.

  ingest -> map (+ date formats) -> [pause if a column-level decision is needed]
         -> clean -> reconcile -> validate -> push -> done
Record-level escalations never stop the run: every other record keeps flowing to the target, and
a record re-enters validate -> push the moment its escalation is resolved.
"""
from __future__ import annotations

import collections
import datetime as dt
import itertools
import threading
import time
import traceback
from typing import Any, Callable

import pandas as pd

from .. import config, db
from ..schema import TargetSchema, load as load_schema, norm
from . import clean, escalations as esc, mapper, policy, rules
from . import validate as val
from .llm import AIResult, dig, get as get_llm
from .log import audit, emit, set_stage
from .profile import profile_column
from .records import FINAL_OK, Record
from .target import TargetClient, get_client

_locks: dict[int, threading.RLock] = collections.defaultdict(threading.RLock)
DATE_LABEL = {"DMY": "day-first (DD/MM/YYYY)", "MDY": "month-first (MM/DD/YYYY)"}


def run_lock(run_id: int) -> threading.RLock:
    return _locks[run_id]


def files_dir(run_id: int):
    return config.RUNS_DIR / str(run_id) / "files"


def in_background(run_id: int, fn: Callable[..., Any], *args) -> threading.Thread:
    def guarded():
        with run_lock(run_id):
            try:
                fn(run_id, *args)
            except Exception as exc:  # the UI must always learn about a crash
                db.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (traceback.format_exc(), run_id))
                emit(run_id, "error", "error", f"Agent stopped with an internal error: {exc}", pace=False)
    t = threading.Thread(target=guarded, daemon=True)
    t.start()
    return t


def start(run_id: int) -> threading.Thread:
    return in_background(run_id, run_all)


# ------------------------------------------------------------------------------------------------
# Stage 1-2: ingest + map
# ------------------------------------------------------------------------------------------------

def load_files(run_id: int) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(files_dir(run_id).iterdir()):
        suffix = path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path, dtype=object)
        else:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        df = df.loc[:, [c for c in df.columns if not c.lower().startswith("unnamed")]]
        frames[path.name] = df
    return frames


def _ai_state(run_id: int) -> dict:
    row = db.one("SELECT ai_json FROM runs WHERE id=?", (run_id,))
    return db.loads(row["ai_json"] if row else None, None) or {"used": {}, "failures": [], "calls": 0, "no_ai_calls": 0}


def track_ai(run_id: int, stage: str, purpose: str, res: AIResult) -> None:
    """Record on the run exactly which AI answered (or that none did), and say so in the live feed."""
    state = _ai_state(run_id)
    fresh = [f for f in res.failures if not f.get("skipped")]  # skipped = already known to be down
    for f in fresh:
        state["failures"].append({**f, "purpose": purpose, "ts": db.now()})
    if res.data is not None:
        key = f"{res.model} via {res.provider}"
        state["used"][key] = state["used"].get(key, 0) + 1
        state["calls"] += 1
        if fresh:
            emit(run_id, stage, "warning", f"AI fallback for {purpose}: {res.failure_text()} -> answered by "
                                           f"{res.answered_by}", category="ai_fallback", pace=False)
    else:
        state["no_ai_calls"] += 1
        why = res.failure_text() or get_llm().reason_unavailable()
        emit(run_id, stage, "warning", f"No AI involved for {purpose} - every AI provider failed ({why}). "
                                       "Deciding from deterministic evidence only.", category="ai_unavailable", pace=False)
    db.execute("UPDATE runs SET ai_json=? WHERE id=?", (db.dumps(state), run_id))


def run_all(run_id: int) -> None:
    schema, llm = load_schema(), get_llm()
    set_stage(run_id, "ingest", "running")
    if any(p.configured and p.status == "untested" for p in llm.providers):
        llm.check()
    status = llm.status()
    db.execute("UPDATE runs SET ai_json=? WHERE id=?",
               (db.dumps({"used": {}, "failures": [], "calls": 0, "no_ai_calls": 0, "mode_at_start": status["mode"],
                          "label_at_start": status["label"], "reason_at_start": status["reason"]}), run_id))
    emit(run_id, "ingest", "info", f"Starting migration run #{run_id}.")
    if status["mode"] == "ai":
        backups = [p.label for p in llm.providers[1:] if p.usable]
        emit(run_id, "ingest", "info", f"AI model: {llm.label}" + (f" (fallback ready: {', '.join(backups)})" if backups else ""),
             category="ai")
    elif status["mode"] == "fallback":
        emit(run_id, "ingest", "warning", f"Primary AI unavailable - using {llm.label} instead. {status['reason']}",
             category="ai_fallback")
    else:
        emit(run_id, "ingest", "warning", f"No AI involved in this run: {status['reason']}. The agent continues on "
                                          "deterministic rules only - no AI proposals or suggestions.",
             category="ai_unavailable")
    frames = load_files(run_id)
    if not frames:
        raise RuntimeError("No CSV/Excel files found for this run")
    for name, df in frames.items():
        emit(run_id, "ingest", "info", f"Read {name}: {len(df)} rows, {len(df.columns)} columns",
             data={"columns": list(df.columns)})
    set_stage(run_id, "map")
    map_columns(run_id, frames, schema)
    continue_run(run_id)


def map_columns(run_id: int, frames: dict[str, pd.DataFrame], schema: TargetSchema) -> None:
    llm = get_llm()
    db.execute("DELETE FROM mappings WHERE run_id=?", (run_id,))
    column_rules = rules.as_map("column_map")
    for file, df in frames.items():
        profiles = [profile_column(file, c, df[c], schema) for c in df.columns]
        views = None
        if llm.available:
            emit(run_id, "map", "info", f"Asking {llm.label} to propose a mapping for {file}...", pace=False)
            views, res = mapper.ask_llm(llm, file, profiles, schema)
            track_ai(run_id, "map", f"column mapping of {file}", res)
        decisions = mapper.map_file(file, profiles, schema, views, column_rules)
        auto = 0
        for d in decisions:
            label = schema.all_mappable[d.target].label if d.target else None
            status = {"auto": "mapped", "rule": "mapped" if d.target else "ignored", "escalate": "pending",
                      "unmapped": "ignored"}[d.method]
            mid = db.execute(
                "INSERT INTO mappings (run_id, file, source_column, target_field, confidence, method, status, "
                "candidates_json, reason, samples_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, file, d.column, d.target, round(d.confidence, 3), d.method, status,
                 db.dumps([c.as_dict() for c in d.candidates]), d.reason, db.dumps(d.samples)))
            entity = f"{file} · {d.column}"
            if d.method == "auto":
                auto += 1
                emit(run_id, "map", "auto", f"{file}: '{d.column}' -> {label} ({round(d.confidence * 100)}%)",
                     category="mapping", data={"reason": d.reason}, pace=False)
                audit(run_id, "agent", "mapped column", entity, after={"target": d.target, "confidence": round(d.confidence, 2)},
                      reason=d.reason)
            elif d.method == "rule":
                auto += 1
                rules.mark_applied("column_map", norm(d.column))
                emit(run_id, "map", "auto", f"{file}: '{d.column}' -> {label or 'not migrated'} (rule you taught me earlier)",
                     category="rule", pace=False)
                audit(run_id, "agent", "mapped column (learned rule)", entity, after={"target": d.target}, reason=d.reason)
            elif d.method == "unmapped":
                emit(run_id, "map", "info", f"{file}: '{d.column}' has no matching target field - it won't be migrated",
                     category="unmapped", pace=False)
                audit(run_id, "agent", "left column out", entity, reason=d.reason)
            else:
                _escalate_mapping(run_id, file, d, mid, schema)
        emit(run_id, "map", "info", f"{file}: {auto} of {len(decisions)} columns mapped automatically")


def _escalate_mapping(run_id: int, file: str, d: mapper.Decision, mapping_id: int, schema: TargetSchema) -> None:
    c = d.candidates
    top = c[0]
    options = [{"label": x.label, "value": x.field,
                "detail": f"{round(x.score * 100)}% - " + "; ".join(x.evidence)} for x in c[:3]]
    rest = [{"label": f.label, "value": f.name, "detail": ""} for f in schema.all_mappable.values()
            if f.name not in {o["value"] for o in options}]
    alt = f" or {c[1].label}" if len(c) > 1 else ""
    esc.create(
        run_id, "mapping", title=f"Where should column '{d.column}' go?",
        question=f"'{d.column}' in {file} could be {top.label}{alt}. Which target field is it?",
        reason=d.reason,
        context={"file": file, "column": d.column, "samples": d.samples, "mapping_id": mapping_id,
                 "candidates": [x.as_dict() for x in c[:3]]},
        proposal={"label": f"Map to {top.label}", "value": top.field, "confidence": round(top.score, 2)},
        correct={"kind": "choice", "options": options, "more": rest},
        reject_label="Don't migrate this column", blocking=True, dedupe_key=f"map|{file}|{d.column}", stage="map")


# ------------------------------------------------------------------------------------------------
# Date formats (still part of "map": they decide how a whole column is read)
# ------------------------------------------------------------------------------------------------

def _mapped(run_id: int) -> list[dict]:
    return db.query("SELECT * FROM mappings WHERE run_id=? AND status='mapped' AND target_field IS NOT NULL", (run_id,))


def infer_date_formats(run_id: int, frames: dict[str, pd.DataFrame], schema: TargetSchema) -> None:
    maps = _mapped(run_id)
    todo = [m for m in maps if schema.all_mappable[m["target_field"]].type == "date" and not m["date_format"]]
    pending = []
    for m in todo:
        values = list(frames[m["file"]][m["source_column"]])
        fmt, stats = clean.detect_date_format(values)
        if fmt is None:
            db.execute("UPDATE mappings SET date_format='iso' WHERE id=?", (m["id"],))
            m["date_format"] = "iso"
        elif fmt in ("DMY", "MDY"):
            db.execute("UPDATE mappings SET date_format=? WHERE id=?", (fmt, m["id"]))
            m["date_format"] = fmt
            n = stats["first_gt_12"] if fmt == "DMY" else stats["second_gt_12"]
            emit(run_id, "map", "auto", f"{m['file']}: '{m['source_column']}' is {DATE_LABEL[fmt]} - proven by "
                                        f"{n} value(s) that can only be read that way", category="date_format", pace=False)
            audit(run_id, "agent", "detected date format", f"{m['file']} · {m['source_column']}", after=fmt,
                  reason=f"{n} values have a {'day' if fmt == 'DMY' else 'month'}-position number > 12")
        else:
            pending.append((m, fmt, stats))

    for m, fmt, stats in pending:
        col, entity = m["source_column"], f"{m['file']} · {m['source_column']}"
        learned = rules.lookup("date_format", norm(col))
        if learned in ("DMY", "MDY"):
            db.execute("UPDATE mappings SET date_format=? WHERE id=?", (learned, m["id"]))
            rules.mark_applied("date_format", norm(col))
            emit(run_id, "map", "auto", f"{m['file']}: '{col}' read as {DATE_LABEL[learned]} (rule you taught me earlier)",
                 category="rule", pace=False)
            audit(run_id, "agent", "date format (learned rule)", entity, after=learned)
            continue
        dmy, mdy = _date_evidence(m, frames, maps)
        if fmt == "ambiguous" and ((dmy >= policy.DATE_EVIDENCE_MIN_MATCHES and mdy == 0)
                                   or (mdy >= policy.DATE_EVIDENCE_MIN_MATCHES and dmy == 0)):
            decided = "DMY" if dmy else "MDY"
            db.execute("UPDATE mappings SET date_format=? WHERE id=?", (decided, m["id"]))
            emit(run_id, "map", "auto", f"{m['file']}: '{col}' is {DATE_LABEL[decided]} - {max(dmy, mdy)} employees "
                                        "in other files have matching dates", category="date_format", pace=False)
            audit(run_id, "agent", "detected date format", entity, after=decided, reason="cross-file evidence")
            continue
        _escalate_date(run_id, m, fmt, stats, dmy, mdy, frames)


def _date_evidence(m: dict, frames: dict[str, pd.DataFrame], maps: list[dict]) -> tuple[int, int]:
    """Compare both readings of an ambiguous column with the same employees' dates in other files."""
    schema = load_schema()
    id_col = {x["file"]: x["source_column"] for x in maps if x["target_field"] == "employee_id"}
    if m["file"] not in id_col:
        return 0, 0
    reference: dict[str, dt.date] = {}
    for other in maps:
        if other["target_field"] != m["target_field"] or other["file"] == m["file"] or other["file"] not in id_col:
            continue
        if other["date_format"] not in ("DMY", "MDY", "iso"):
            continue
        df = frames[other["file"]]
        for _, row in df.iterrows():
            try:
                key = clean.clean_id(row[id_col[other["file"]]], schema)
                reference[key] = clean.parse_date(row[other["source_column"]],
                                                  None if other["date_format"] == "iso" else other["date_format"])
            except clean.CleanError:
                continue
    dmy = mdy = 0
    for _, row in frames[m["file"]].iterrows():
        mt = clean.NUMERIC_DATE.match(clean.text(row[m["source_column"]]))
        if not mt:
            continue
        try:
            key = clean.clean_id(row[id_col[m["file"]]], schema)
        except clean.CleanError:
            continue
        if key not in reference:
            continue
        a, b, y = int(mt.group(1)), int(mt.group(2)), int(mt.group(3))
        if a == b:
            continue
        try:
            dmy += dt.date(y, b, a) == reference[key]
        except ValueError:
            pass
        try:
            mdy += dt.date(y, a, b) == reference[key]
        except ValueError:
            pass
    return dmy, mdy


def _escalate_date(run_id, m, fmt, stats, dmy, mdy, frames) -> None:
    col, file = m["source_column"], m["file"]
    examples = []
    for v in frames[file][col]:
        mt = clean.NUMERIC_DATE.match(clean.text(v))
        if mt and len(examples) < 4:
            a, b, y = int(mt.group(1)), int(mt.group(2)), int(mt.group(3))
            try:
                examples.append({"raw": clean.text(v), "DMY": dt.date(y, b, a).strftime("%d %b %Y"),
                                 "MDY": dt.date(y, a, b).strftime("%d %b %Y")})
            except ValueError:
                pass
    proposal_fmt = "MDY" if mdy > dmy else "DMY"
    evidence = (f"{max(dmy, mdy)} employee(s) in other files match the {DATE_LABEL[proposal_fmt]} reading"
                if max(dmy, mdy) else "no overlapping employees in other files to compare against")
    if fmt == "conflict":
        reason = "The column mixes values that are only valid day-first with values only valid month-first"
    else:
        reason = (f"All {stats['numeric_values']} values read validly both ways (no day above 12), and {evidence} "
                  f"- I need at least {policy.DATE_EVIDENCE_MIN_MATCHES} agreeing matches to decide alone")
    esc.create(
        run_id, "date_format", title=f"Is '{col}' day-first or month-first?",
        question=f"Dates in '{col}' ({file}) can be read two ways, e.g. {examples[0]['raw'] if examples else ''}: "
                 f"{examples[0]['DMY'] if examples else ''} or {examples[0]['MDY'] if examples else ''}. Which did the client use?",
        reason=reason,
        context={"file": file, "column": col, "examples": examples, "stats": stats, "mapping_id": m["id"],
                 "evidence": {"DMY": dmy, "MDY": mdy, "needed": policy.DATE_EVIDENCE_MIN_MATCHES}},
        proposal={"label": f"Read as {DATE_LABEL[proposal_fmt]}", "value": proposal_fmt,
                  "confidence": 0.6 if max(dmy, mdy) else 0.5},
        correct=esc.choice([{"label": DATE_LABEL["DMY"].capitalize(), "value": "DMY"},
                            {"label": DATE_LABEL["MDY"].capitalize(), "value": "MDY"}]),
        reject_label="Don't migrate this column", blocking=True, dedupe_key=f"date|{file}|{col}", stage="map")


# ------------------------------------------------------------------------------------------------
# Continue after column-level decisions: clean -> reconcile -> validate -> push
# ------------------------------------------------------------------------------------------------

def continue_run(run_id: int) -> None:
    schema = load_schema()
    frames = load_files(run_id)
    infer_date_formats(run_id, frames, schema)  # before pausing, so all column-level questions arrive together
    if _pause_if_blocked(run_id):
        return
    set_stage(run_id, "clean", "running")
    partials = clean_rows(run_id, frames, schema)
    set_stage(run_id, "reconcile")
    reconcile(run_id, partials, schema)
    set_stage(run_id, "validate")
    validate_all(run_id, schema)
    set_stage(run_id, "push")
    push_ready(run_id)
    set_stage(run_id, "done")
    finalize(run_id, first_pass=True)


def _pause_if_blocked(run_id: int) -> bool:
    n = esc.open_blocking(run_id)
    if n:
        db.execute("UPDATE runs SET status='waiting_for_input' WHERE id=?", (run_id,))
        emit(run_id, "map", "info", f"Paused: {n} column-level decision(s) needed before I continue - every row "
                                    "depends on them. Everything else is ready.", pace=False)
    return bool(n)


def _jsonable(v: Any) -> Any:
    if clean.is_blank(v):
        return None
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()[:10] if isinstance(v, dt.datetime) and v.time() == dt.time() else v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v if isinstance(v, (str, int, float, bool)) else str(v)


def clean_rows(run_id: int, frames: dict[str, pd.DataFrame], schema: TargetSchema) -> list[dict]:
    maps = _mapped(run_id)
    value_rules = rules.as_map("value_map")
    partials = []
    for file, df in frames.items():
        fmaps = [m for m in maps if m["file"] == file]
        counts: collections.Counter = collections.Counter()
        for idx, row in df.iterrows():
            if all(clean.is_blank(row[c]) for c in df.columns):
                continue
            p = {"file": file, "row": int(idx) + 2, "raw": {c: _jsonable(row[c]) for c in df.columns},
                 "values": {}, "errors": [], "changes": []}
            for m in fmaps:
                f = schema.all_mappable[m["target_field"]]
                raw = row[m["source_column"]]
                if clean.is_placeholder(raw):
                    if not clean.is_blank(raw):
                        counts["placeholder"] += 1
                        p["changes"].append({"field": f.name, "from": clean.text(raw), "to": None,
                                             "reason": clean.CATEGORY_LABELS["placeholder"]})
                    continue
                rkey = f"{f.name}|{clean.vkey(raw)}"
                if not f.virtual and rkey in value_rules:
                    p["values"][f.name] = value_rules[rkey]
                    counts["rule"] += 1
                    rules.mark_applied("value_map", rkey)
                    p["changes"].append({"field": f.name, "from": clean.text(raw), "to": value_rules[rkey],
                                         "reason": "rule you taught me earlier"})
                    continue
                if f.type == "full_name":
                    first, last, how = clean.split_full_name(raw)
                    if first:
                        p["values"]["first_name"] = first
                    if last:
                        p["values"]["last_name"] = last
                    counts["name_split"] += 1
                    p["changes"].append({"field": "first_name / last_name", "from": clean.text(raw),
                                         "to": f"{first} / {last}",
                                         "reason": "split 'Last, First'" if how == "comma" else "split full name"})
                    continue
                try:
                    fmt = m["date_format"] if m["date_format"] in ("DMY", "MDY") else None
                    out, cat = clean.clean_value(f, raw, schema, fmt)
                    p["values"][f.name] = out
                    if cat:
                        counts[cat] += 1
                        p["changes"].append({"field": f.name, "from": clean.text(raw), "to": out,
                                             "reason": clean.CATEGORY_LABELS.get(cat, cat)})
                except clean.CleanError as e:
                    p["errors"].append({"field": f.name, "raw": clean.text(raw), "code": e.code,
                                        "message": e.message, "file": file})
            partials.append(p)
        for cat, n in counts.most_common():
            emit(run_id, "clean", "auto", f"{file}: {n} {clean.CATEGORY_LABELS.get(cat, cat)}", category=cat, pace=False)
        errs = sum(len(p["errors"]) for p in partials if p["file"] == file)
        emit(run_id, "clean", "info", f"{file}: cleaned {sum(counts.values())} values automatically"
                                      + (f", {errs} value(s) I couldn't clean confidently" if errs else ""))
    return partials


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def _summary(r: Record) -> dict:
    keys = ["employee_id", "first_name", "last_name", "email", "department", "date_of_joining", "date_of_birth", "phone"]
    return {"key": r.key, "name": r.name, "data": {k: r.data.get(k) for k in keys if r.data.get(k) is not None},
            "files": sorted({row["file"] for row in r.rows})}


def reconcile(run_id: int, partials: list[dict], schema: TargetSchema) -> None:
    db.execute("DELETE FROM records WHERE run_id=?", (run_id,))
    groups: dict[str, list[dict]] = {}
    for p in partials:
        key = p["values"].get("employee_id") or f"{p['file']}#row{p['row']}"
        groups.setdefault(key, []).append(p)
    priority = rules.as_map("source_priority")
    record_rules = rules.as_map("record_value")
    records: list[Record] = []
    exact_dupes, multi_file, resolved_conflicts = [], 0, []
    for key, parts in groups.items():
        unique: list[dict] = []
        for p in parts:
            if any(q["file"] == p["file"] and q["raw"] == p["raw"] for q in unique):
                exact_dupes.append(f"{key} ({p['file']} row {p['row']})")
                continue
            unique.append(p)
        rec = Record(run_id, key)
        rec.rows = [{"file": p["file"], "row": p["row"], "raw": p["raw"]} for p in parts]
        multi_file += len({p["file"] for p in unique}) > 1
        for p in unique:
            for ch in p["changes"]:
                rec.changes.append({**ch, "by": "agent", "ts": db.now(), "file": p["file"]})
        for fname in schema.fields:
            distinct: list[dict] = []
            for p in unique:
                if fname in p["values"]:
                    v = p["values"][fname]
                    hit = next((d for d in distinct if _same(d["value"], v)), None)
                    if hit:
                        hit["files"].append(p["file"])
                    else:
                        distinct.append({"value": v, "files": [p["file"]]})
            if len(distinct) == 1:
                rec.data[fname] = distinct[0]["value"]
                rec.field_sources[fname] = ", ".join(sorted(set(distinct[0]["files"])))
            elif distinct:
                pf = priority.get(fname)
                win = next((d for d in distinct if pf in d["files"]), None) if pf else None
                valid = [d for d in distinct if not val.field_issues(fname, d["value"], schema)]
                if win:
                    reason = f"sources disagreed; your rule says {pf} wins"
                    rules.mark_applied("source_priority", fname)
                elif len(valid) == 1:
                    win = valid[0]
                    bad = [d for d in distinct if d is not win]
                    reason = (f"sources disagreed; only the value from {', '.join(win['files'])} is valid "
                              f"({'; '.join(i.message for d in bad for i in val.field_issues(fname, d['value'], schema))})")
                    resolved_conflicts.append(f"{key} {fname}")
                if win:
                    rec.data[fname] = win["value"]
                    rec.field_sources[fname] = ", ".join(win["files"])
                    rec.changes.append({"field": fname, "from": [d["value"] for d in distinct], "to": win["value"],
                                        "reason": reason, "by": "agent", "ts": db.now()})
                else:
                    rec.issues.append({"code": "conflict", "field": fname, "options": distinct})
            errs = [e for p in unique for e in p["errors"] if e["field"] == fname]
            if errs:
                if fname in rec.data or any(i.get("field") == fname for i in rec.issues):
                    rec.changes.append({"field": fname, "from": errs[0]["raw"], "to": rec.data.get(fname),
                                        "reason": f"ignored unparseable value from {errs[0]['file']} - another file has a valid one",
                                        "by": "agent", "ts": db.now()})
                else:
                    rec.issues.append({**errs[0]})
        if rules.lookup("skip_record", key):
            rec.status = "skipped"
            rec.last_error = "Skipped by a rule you set earlier"
            rules.mark_applied("skip_record", key)
        for fname in schema.fields:
            rk = f"{key}|{fname}"
            if rk in record_rules:
                rec.set(fname, record_rules[rk], "rule you taught me earlier")
                rules.mark_applied("record_value", rk)
        records.append(rec)

    if exact_dupes:
        emit(run_id, "reconcile", "auto", f"Merged {len(exact_dupes)} exact duplicate row(s): {', '.join(exact_dupes)}",
             category="duplicate_exact", pace=False)
        audit(run_id, "agent", "merged exact duplicates", ", ".join(exact_dupes),
              reason="same employee ID and identical values in the same file")
    if resolved_conflicts:
        emit(run_id, "reconcile", "auto", f"Resolved {len(resolved_conflicts)} source conflict(s) where only one file had "
                                          f"a valid value: {', '.join(resolved_conflicts)}", category="conflict_valid",
             pace=False)
    emit(run_id, "reconcile", "info", f"Combined {len(partials)} rows from {len({p['file'] for p in partials})} files "
                                      f"into {len(records)} employees ({multi_file} found in more than one file)")

    _find_fuzzy_duplicates(run_id, records)
    for r in records:
        if r.issues and r.status != "skipped":
            r.status = "needs_review"
        r.save()
    _escalate_record_issues(run_id, records, schema)


def _find_fuzzy_duplicates(run_id: int, records: list[Record]) -> None:
    live = [r for r in records if r.status != "skipped"]
    by_name: dict[tuple, list[Record]] = collections.defaultdict(list)
    by_email: dict[str, list[Record]] = collections.defaultdict(list)
    for r in live:
        if r.data.get("first_name") and r.data.get("last_name"):
            by_name[(r.data["first_name"].lower(), r.data["last_name"].lower())].append(r)
        if r.data.get("email"):
            by_email[r.data["email"]].append(r)
    pairs: dict[tuple, list[str]] = {}
    for group in by_name.values():
        for a, b in itertools.combinations(sorted(group, key=lambda r: r.key), 2):
            dob_a, dob_b = a.data.get("date_of_birth"), b.data.get("date_of_birth")
            if dob_a and dob_b and dob_a == dob_b:
                pairs.setdefault((a.key, b.key), []).extend(["same name", "same date of birth"])
            elif dob_a and dob_b:
                emit(run_id, "reconcile", "auto", f"{a.key} and {b.key} are both '{a.name}' but have different birth "
                                                  "dates - treated as two different people", category="duplicate_distinct",
                     pace=False)
                audit(run_id, "agent", "kept apart", f"{a.key}, {b.key}", reason="same name, different date of birth")
    for group in by_email.values():
        for a, b in itertools.combinations(sorted(group, key=lambda r: r.key), 2):
            pairs.setdefault((a.key, b.key), []).append("same work email")
    idx = {r.key: r for r in records}
    for (ka, kb), signals in pairs.items():
        decided = rules.lookup("duplicate", f"{ka}|{kb}")
        if decided == "merge":
            _merge(run_id, idx[ka], idx[kb], "agent", "rule you taught me earlier")
            rules.mark_applied("duplicate", f"{ka}|{kb}")
            continue
        if decided == "keep_both":
            rules.mark_applied("duplicate", f"{ka}|{kb}")
            emit(run_id, "reconcile", "auto", f"{ka} and {kb} kept as different people (rule you taught me earlier)",
                 category="rule", pace=False)
            continue
        for me, other in ((ka, kb), (kb, ka)):
            idx[me].issues.append({"code": "duplicate", "with": other, "signals": signals})


def _merge(run_id: int, primary: Record, secondary: Record, actor: str, reason: str) -> None:
    for k, v in secondary.data.items():
        if v is not None and primary.data.get(k) in (None, "") and k != "employee_id":
            primary.set(k, v, f"filled from duplicate {secondary.key}", by=actor, source=secondary.key)
    primary.rows.extend(secondary.rows)
    primary.issues = [i for i in primary.issues if not (i.get("code") == "duplicate" and i.get("with") == secondary.key)]
    secondary.status = "merged"
    secondary.issues = []
    secondary.last_error = f"Merged into {primary.key}"
    primary.save()
    secondary.save()
    audit(run_id, actor, "merged duplicate", f"{secondary.key} -> {primary.key}", reason=reason)
    emit(run_id, "reconcile", "human" if actor != "agent" else "auto",
         f"Merged {secondary.key} into {primary.key} ({primary.name})", category="duplicate_merge", pace=False)


def _llm_enum_suggestions(run_id: int, groups: dict[tuple, list[Record]], schema: TargetSchema) -> dict[tuple, dict]:
    llm = get_llm()
    if not llm.available or not groups:
        return {}
    import json
    items = [{"field": f, "allowed": schema.fields[f].values, "value": raw} for (f, raw) in groups]
    res = llm.complete_json("You help clean HR data. For each value, suggest the closest allowed category, or null "
                            "if none is defensible. Confidence 0-1.",
                            json.dumps({"items": items, "output_format": {"suggestions": [
                                {"field": "", "value": "", "suggestion": "<allowed value or null>", "confidence": 0.0,
                                 "reason": ""}]}}))
    track_ai(run_id, "reconcile", "suggestions for unrecognised values", res)
    out = res.data
    result = {}
    for s in dig(out, "suggestions") or []:
        try:
            key = (s["field"], str(s["value"]).lower())
            if key in groups and s.get("suggestion") in schema.fields[s["field"]].values:
                result[key] = s
        except (KeyError, TypeError):
            continue
    return result


def _escalate_record_issues(run_id: int, records: list[Record], schema: TargetSchema) -> None:
    idx = {r.key: r for r in records}
    enum_groups: dict[tuple, list[Record]] = collections.defaultdict(list)
    raw_of: dict[tuple, str] = {}
    done_pairs = set()
    for r in records:
        if r.status in ("skipped", "merged"):
            continue
        for issue in r.issues:
            if issue.get("code") == "unknown_value":
                k = (issue["field"], issue["raw"].lower())
                enum_groups[k].append(r)
                raw_of[k] = issue["raw"]
    suggestions = _llm_enum_suggestions(run_id, enum_groups, schema)
    for (fname, rawl), recs in enum_groups.items():
        f = schema.fields[fname]
        raw = raw_of[(fname, rawl)]
        s = suggestions.get((fname, rawl))
        proposal = ({"label": f"Use '{s['suggestion']}'", "value": s["suggestion"],
                     "confidence": round(float(s.get("confidence") or 0), 2),
                     "details": [f"AI suggestion: {s.get('reason', '')}"]} if s else None)
        esc.create(
            run_id, "unknown_value", title=f"'{raw}' isn't a known {f.label.lower()}",
            question=f"{len(recs)} employee(s) have {f.label} = '{raw}'. Which {f.label.lower()} should they get?",
            reason="It isn't an allowed value, a known synonym or a near-typo - choosing a category is a business "
                   "decision, so I won't guess" + (" (the AI's guess is shown as a suggestion only)" if s else ""),
            context={"field": fname, "label": f.label, "raw": raw, "allowed": f.values,
                     "records": [{"key": r.key, "name": r.name, "job_title": r.data.get("job_title")} for r in recs]},
            proposal=proposal, correct=esc.choice([{"label": v, "value": v} for v in f.values]),
            reject_label=f"Don't migrate these {len(recs)} employee(s)", record_keys=[r.key for r in recs],
            dedupe_key=f"enum|{fname}|{rawl}", stage="reconcile")
    for r in records:
        if r.status in ("skipped", "merged"):
            continue
        for issue in r.issues:
            code = issue.get("code")
            if code == "unknown_value":
                continue
            if code == "conflict":
                f = schema.fields[issue["field"]]
                opts = [{"label": f"{o['value']}  (from {', '.join(o['files'])})", "value": o["value"],
                         "files": o["files"]} for o in issue["options"]]
                esc.create(
                    run_id, "conflict", title=f"{r.name} ({r.key}): files disagree on {f.label.lower()}",
                    question=f"Which {f.label.lower()} is correct for {r.name}?",
                    reason="Two source systems give different values and only the client knows which one is the "
                           "source of truth for this field",
                    context={"record": _summary(r), "field": issue["field"], "label": f.label, "options": opts},
                    proposal={"label": f"Use {opts[0]['label']}", "value": opts[0]["value"], "confidence": 0.5},
                    correct=esc.choice(opts), reject_label="Don't migrate this employee", record_keys=[r.key],
                    dedupe_key=f"conflict|{r.key}|{issue['field']}", stage="reconcile")
            elif code == "duplicate":
                pair = tuple(sorted((r.key, issue["with"])))
                if pair in done_pairs:
                    continue
                done_pairs.add(pair)
                a, b = idx[pair[0]], idx[pair[1]]
                fields = sorted({*a.data, *b.data}, key=lambda k: list(schema.fields).index(k) if k in schema.fields else 99)
                compare = [{"field": k, "label": schema.fields[k].label if k in schema.fields else k,
                            "a": a.data.get(k), "b": b.data.get(k), "same": _same(a.data.get(k), b.data.get(k))}
                           for k in fields]
                esc.create(
                    run_id, "duplicate", title=f"Are {a.key} and {b.key} the same person?",
                    question=f"{a.name} ({a.key}) and {b.name} ({b.key}) have {' and '.join(issue['signals'])}.",
                    reason="Different employee IDs, but matching identity details - could be a re-hire or a "
                           "data-entry duplicate. Merging two real people is hard to undo, so I'm asking",
                    context={"records": [_summary(a), _summary(b)], "signals": issue["signals"], "compare": compare},
                    proposal={"label": f"Merge {b.key} into {a.key} (same person)", "value": "merge", "confidence": 0.6},
                    correct=esc.choice([{"label": f"Same person - merge into {a.key}", "value": "merge"},
                                        {"label": "Different people - keep both", "value": "keep_both"}]),
                    reject_label=f"Don't migrate {b.key}", record_keys=[a.key, b.key],
                    dedupe_key=f"dup|{a.key}|{b.key}", stage="reconcile")
            else:
                f = schema.fields[issue["field"]]
                proposal = (None if f.required else
                            {"label": f"Leave {f.label.lower()} empty", "value": {issue["field"]: None}, "confidence": 0.5})
                esc.create(
                    run_id, "invalid_value", title=f"{r.name} ({r.key}): {f.label.lower()} '{issue['raw']}' can't be cleaned",
                    question=f"What should {r.name}'s {f.label.lower()} be?",
                    reason=f"{issue['message']} - it isn't a placeholder like N/A, so dropping it silently would lose data",
                    context={"record": _summary(r), "field": issue["field"], "label": f.label, "raw": issue["raw"],
                             "file": issue.get("file")},
                    proposal=proposal,
                    correct=esc.field_form([_form_field(f, issue["raw"])]),
                    reject_label="Don't migrate this employee", record_keys=[r.key],
                    dedupe_key=f"value|{r.key}|{issue['field']}", stage="reconcile")


def _form_field(f, value, suggestion: dict | None = None) -> dict:
    return {"name": f.name, "label": f.label, "type": f.type, "value": value,
            "options": f.values or None, "required": f.required,
            "suggestion": (suggestion or {}).get("value"), "why": (suggestion or {}).get("why")}


# ------------------------------------------------------------------------------------------------
# Validate (validate -> one safe repair -> validate again -> escalate)
# ------------------------------------------------------------------------------------------------

def validate_all(run_id: int, schema: TargetSchema) -> None:
    recs = Record.load_all(run_id)
    tally: collections.Counter = collections.Counter()
    for r in recs:
        if r.status == "pending":
            tally[validate_record(run_id, r, recs, schema)] += 1
    emit(run_id, "validate", "info", f"Validation: {tally['ready']} passed first time, {tally['repaired']} passed after an "
                                     f"automatic repair, {tally['escalated']} failed twice and need you")


def validate_record(run_id: int, r: Record, all_recs: list[Record], schema: TargetSchema) -> str:
    issues = val.validate(r.data, schema)
    r.validation_attempts = 1
    r.issues = []
    if not issues:
        r.status = "ready"
        r.save()
        return "ready"
    notes = val.repair(r, issues, schema)
    issues2 = val.validate(r.data, schema)
    r.validation_attempts = 2
    if not issues2:
        r.status = "ready"
        r.save()
        emit(run_id, "validate", "auto", f"{r.key} {r.name}: failed validation once, repaired - {'; '.join(notes)}",
             category="repair", pace=False)
        audit(run_id, "agent", "auto-repaired", r.key, before=[i.as_dict() for i in issues], after=notes,
              reason="failed validation once; safe repair applied and re-validated")
        return "repaired"
    r.status = "needs_review"
    r.issues = [i.as_dict() for i in issues2]
    r.save()
    sugg = val.suggest(r, issues2, schema, all_recs)
    failing = list(dict.fromkeys(i.field for i in issues2))
    form = [_form_field(schema.fields[fn], r.data.get(fn), sugg.get(fn)) for fn in failing]
    form += [_form_field(schema.fields[fn], r.data.get(fn), s) for fn, s in sugg.items() if fn not in failing]
    proposal = None
    if all(fn in sugg for fn in failing):
        proposal = {"label": "Apply the suggested fix" + ("es" if len(sugg) > 1 else ""),
                    "value": {fn: s["value"] for fn, s in sugg.items()},
                    "confidence": round(min(s["confidence"] for s in sugg.values()), 2),
                    "details": [f"{schema.fields[fn].label}: {s['value']} - {s['why']}" for fn, s in sugg.items()]}
    missing_only = all(i.code == "missing" for i in issues2)
    esc.create(
        run_id, "validation", title=f"{r.name} ({r.key}): " + "; ".join(i.message for i in issues2),
        question=f"How should {r.name}'s record be fixed?",
        reason=("Required information is missing from every source file - I won't invent it" if missing_only else
                "Failed validation, my safe repair attempt didn't fix it, and it failed again")
               + (". Repair tried: " + "; ".join(notes) if notes else ""),
        context={"record": _summary(r), "issues": r.issues, "repair_notes": notes},
        proposal=proposal, correct=esc.field_form(form), reject_label="Don't migrate this employee",
        record_keys=[r.key], dedupe_key=f"val|{r.key}|{','.join(sorted(i.code + ':' + i.field for i in issues2))}",
        stage="validate")
    return "escalated"


# ------------------------------------------------------------------------------------------------
# Push (delta-aware, dependency-ordered, retried) + rollback
# ------------------------------------------------------------------------------------------------

def _payload(r: Record, schema: TargetSchema) -> dict:
    return {k: v for k, v in r.data.items() if k in schema.fields and v not in (None, "")}


def _diff(before: dict | None, after: dict, schema: TargetSchema) -> list[str]:
    before = before or {}
    return [k for k in schema.fields if not _same(before.get(k) if before.get(k) != "" else None, after.get(k))]


def push_ready(run_id: int, keys: list[str] | None = None) -> None:
    schema, client = load_schema(), get_client()
    snap = client.list()
    target_map = {x["employee_id"]: x for x in snap.body} if snap.ok and isinstance(snap.body, list) else None
    if target_map is None:
        emit(run_id, "push", "warning", f"Couldn't read the target system ({snap.message}); checking records one by one",
             pace=False)
    recs = Record.load_all(run_id)
    by_key = {r.key: r for r in recs}
    email_to_key = {r.data.get("email"): r.key for r in recs if r.data.get("email") and r.status not in ("skipped", "merged")}
    todo = [r for r in recs if r.status == "ready" and (keys is None or r.key in keys)]
    if not todo:
        return

    def depth(r: Record, seen: frozenset = frozenset()) -> int:
        mk = email_to_key.get(r.data.get("manager_email"))
        if not mk or mk == r.key or mk in seen:
            return 0
        return 1 + depth(by_key[mk], seen | {r.key})

    stats: collections.Counter = collections.Counter()
    if keys is None:
        emit(run_id, "push", "info", f"Pushing {len(todo)} validated records to the target (managers first, "
                                     "unchanged records skipped)")
    for r in sorted(todo, key=lambda x: (depth(x), x.key)):
        if r.status == "ready":
            _push_one(run_id, r, schema, client, target_map, by_key, email_to_key, stats)
    parts = [f"{stats[k]} {k}" for k in ("create", "update", "unchanged", "waiting", "failed") if stats[k]]
    if keys is None or len(todo) > 1:
        emit(run_id, "push", "success" if not stats["failed"] else "warning",
             "Push finished: " + (", ".join(parts).replace("create", "created").replace("update", "updated") or "nothing to do"))


def _push_one(run_id, r: Record, schema, client: TargetClient, target_map, by_key, email_to_key, stats) -> None:
    payload = _payload(r, schema)
    if target_map is not None:
        existing = target_map.get(r.key)
    else:
        got = client.get(r.key)
        existing = got.body if got.ok else None
    if existing and not _diff(existing, payload, schema):
        r.status, r.push_op, r.last_error = "unchanged", "unchanged", None
        r.save()
        stats["unchanged"] += 1
        audit(run_id, "agent", "unchanged", r.key, reason="identical record already in target - nothing to send (delta)")
        return
    mgr = payload.get("manager_email")
    if mgr and mgr != payload.get("email") and target_map is not None:
        mk = email_to_key.get(mgr)
        in_target = any(v.get("email") == mgr for v in target_map.values())
        if not in_target and mk and by_key[mk].status not in FINAL_OK:
            r.status, r.last_error = "waiting", f"Waiting for manager {mk} to be created first"
            r.save()
            stats["waiting"] += 1
            emit(run_id, "push", "info", f"{r.key}: waiting - manager {mk} isn't in the target yet", pace=False)
            return
    op = "update" if existing else "create"
    changed = _diff(existing, payload, schema) if existing else []
    res = None
    for attempt in range(1, policy.PUSH_MAX_ATTEMPTS + 1):
        r.push_attempts += 1
        res = client.update(r.key, payload) if op == "update" else client.create(payload)
        if res.ok:
            r.status, r.push_op, r.last_error = "pushed", op, None
            r.save()
            what = "created" if op == "create" else f"updated ({', '.join(changed)})"
            audit(run_id, "agent", f"push_{op}", r.key, before=existing, after=payload,
                  reason=f"{what} in target" + (f" on attempt {attempt}" if attempt > 1 else ""))
            emit(run_id, "push", "success", f"{r.key} {r.name}: {what}", category=op, pace=False)
            if target_map is not None:
                target_map[r.key] = payload
            stats[op] += 1
            if config.PUSH_DELAY:
                time.sleep(config.PUSH_DELAY)
            for w in [x for x in by_key.values() if x.status == "waiting" and x.data.get("manager_email") == payload.get("email")]:
                w.status, w.last_error = "ready", None
                stats["waiting"] -= 1
                _push_one(run_id, w, schema, client, target_map, by_key, email_to_key, stats)
            return
        if res.transient and attempt < policy.PUSH_MAX_ATTEMPTS:
            emit(run_id, "push", "auto", f"{r.key}: target returned {res.status or 'a network error'} ({res.message}) "
                                         f"- retrying ({attempt + 1}/{policy.PUSH_MAX_ATTEMPTS})", category="retry", pace=False)
            audit(run_id, "agent", "push_retry", r.key, after={"status": res.status, "error": res.message},
                  reason="transient error - retried with backoff")
            time.sleep(policy.PUSH_BACKOFF_SECONDS * attempt)
            continue
        break
    r.status, r.push_op, r.last_error = "failed", op, f"{res.status or 'network'}: {res.message}"
    r.save()
    stats["failed"] += 1
    audit(run_id, "agent", "push_failed", r.key, after=res.body, reason=r.last_error)
    if res.transient:
        emit(run_id, "push", "error", f"{r.key}: target still unavailable after {policy.PUSH_MAX_ATTEMPTS} attempts - "
                                      "marked failed, use Retry", category="push_failed", pace=False)
    else:
        emit(run_id, "push", "warning", f"{r.key}: target rejected the record - {res.message}", category="push_failed",
             pace=False)
        _escalate_push_failure(run_id, r, res, schema)


def _escalate_push_failure(run_id: int, r: Record, res, schema: TargetSchema) -> None:
    code = res.error_code
    body = res.body if isinstance(res.body, dict) else {}
    context = {"record": _summary(r), "http_status": res.status, "error": code, "message": res.message}
    if code == "manager_not_found":
        f = schema.fields["manager_email"]
        proposal = {"label": "Clear the manager and push again", "value": {"manager_email": None}, "confidence": 0.6,
                    "details": ["The manager can be assigned later in the new platform"]}
        form = [_form_field(f, r.data.get("manager_email"))]
        reason = ("The target only accepts managers who already exist there, and this manager isn't in any source "
                  "file either - probably someone who has left")
    elif code in ("duplicate_email", "duplicate_id"):
        existing = body.get("existing") or {}
        context["existing"] = existing
        who = f"{existing.get('first_name', '')} {existing.get('last_name', '')}".strip()
        proposal = {"label": f"Skip - already exists in the target as {body.get('existing_id')}"
                             + (f" ({who})" if who else ""), "value": "skip", "confidence": 0.7}
        form = [_form_field(schema.fields["email"], r.data.get("email"))]
        reason = "The target already has a record with this identity - pushing again would create a second one"
    else:
        proposal = None
        fields = body.get("fields") or [body.get("field")] if (body.get("fields") or body.get("field")) else []
        form = [_form_field(schema.fields[fn], r.data.get(fn)) for fn in fields if fn in schema.fields]
        reason = "The target rejected the record and retrying won't change that"
    esc.create(
        run_id, "push_failure", title=f"{r.name} ({r.key}): target rejected it - {res.message}",
        question=f"How should {r.name} be pushed?", reason=reason, context=context, proposal=proposal,
        correct=esc.field_form(form) if form else None, reject_label="Don't migrate this employee",
        record_keys=[r.key], dedupe_key=f"push|{r.key}|{code}", stage="push")


def retry_failed(run_id: int, keys: list[str] | None = None) -> None:
    ready = []
    for r in Record.load_all(run_id):
        if r.status in ("failed", "waiting") and (keys is None or r.key in keys) and not esc.open_for_record(run_id, r.key):
            r.status, r.last_error = "ready", None
            r.save()
            ready.append(r.key)
    emit(run_id, "push", "human", f"Retrying {len(ready)} record(s)", pace=False)
    if ready:
        push_ready(run_id, ready)
    finalize(run_id)


def rollback(run_id: int, keys: list[str] | None = None, actor: str = "consultant") -> None:
    client = get_client()
    entries = db.query("SELECT * FROM audit WHERE run_id=? AND action IN ('push_create','push_update') ORDER BY id",
                       (run_id,))
    first: dict[str, dict] = {}
    for e in entries:
        first.setdefault(e["entity"], e)
    todo = [k for k in reversed(list(first)) if keys is None or k in keys]
    emit(run_id, "rollback", "human", f"{actor} started a rollback of {len(todo)} record(s)", pace=False)
    done = 0
    for key in todo:
        rec = Record.load(run_id, key)
        if not rec or rec.status == "rolled_back":
            continue
        e = first[key]
        before = db.loads(e["before_json"])
        res = None
        for attempt in range(1, policy.PUSH_MAX_ATTEMPTS + 1):
            res = client.delete(key) if e["action"] == "push_create" else client.update(key, before)
            if res.ok or (e["action"] == "push_create" and res.status == 404) or not res.transient:
                break
            time.sleep(policy.PUSH_BACKOFF_SECONDS * attempt)
        if res.ok or (e["action"] == "push_create" and res.status == 404):
            current = _payload(rec, load_schema())
            rec.status, rec.last_error = "rolled_back", None
            rec.save()
            done += 1
            audit(run_id, actor, "rolled_back", key, before=current, after=before,
                  reason="deleted from target (it was created by this run)" if e["action"] == "push_create"
                  else "restored the target's previous version")
            emit(run_id, "rollback", "success", f"{key}: " + ("removed from target" if e["action"] == "push_create"
                                                              else "previous version restored"), pace=False)
        else:
            emit(run_id, "rollback", "error", f"{key}: rollback failed - {res.message}", pace=False)
    emit(run_id, "rollback", "success", f"Rollback finished: {done} record(s) reverted")
    if keys is None:
        db.execute("UPDATE runs SET status='rolled_back', stage='done' WHERE id=?", (run_id,))
    else:
        finalize(run_id)


# ------------------------------------------------------------------------------------------------
# After a human decision on a record-level escalation
# ------------------------------------------------------------------------------------------------

def process_records(run_id: int, keys: list[str]) -> None:
    schema = load_schema()
    all_recs = Record.load_all(run_id)
    by_key = {r.key: r for r in all_recs}
    ready = []
    for key in keys:
        r = by_key.get(key)
        if not r or r.status in ("skipped", "merged", "pushed", "unchanged", "rolled_back"):
            continue
        if esc.open_for_record(run_id, key):
            r.status = "needs_review"
            r.save()
            continue
        if validate_record(run_id, r, all_recs, schema) in ("ready", "repaired"):
            ready.append(key)
    if ready:
        push_ready(run_id, ready)
    finalize(run_id)


def finalize(run_id: int, first_pass: bool = False) -> None:
    run = db.one("SELECT status FROM runs WHERE id=?", (run_id,))
    if not run or run["status"] in ("rolled_back", "failed"):
        return
    recs = Record.load_all(run_id)
    counts = collections.Counter(r.status for r in recs)
    open_n = esc.unresolved_count(run_id)
    if open_n:
        status = "waiting_for_input" if esc.open_blocking(run_id) else "needs_review"
    elif counts["failed"] or counts["waiting"] or counts["ready"] or counts["pending"]:
        status = "attention"
    else:
        status = "completed"
    db.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?",
               (status, db.now() if status == "completed" else None, run_id))
    done = counts["pushed"] + counts["unchanged"]
    if first_pass:
        msg = (f"First pass complete: {done} of {len(recs) - counts['merged']} employees are in the target. "
               + (f"{open_n} item(s) need your decision - each record is pushed as soon as you resolve it."
                  if open_n else "Nothing needs your attention."))
        emit(run_id, "done", "success" if not open_n else "info", msg)
    elif status == "completed" and run["status"] != "completed":
        emit(run_id, "done", "success", f"Migration complete: {done} employees in the target "
                                        f"({counts['skipped']} skipped, {counts['merged']} merged duplicates)")
