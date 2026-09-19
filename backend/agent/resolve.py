"""Applying a human decision (approve / correct / reject) to an escalation, then letting the agent carry on.

Input is checked synchronously (so the UI can show "that's not a valid phone number" immediately);
the effect is applied in the background under the run lock, and the pipeline resumes by itself.
"""
from __future__ import annotations

from typing import Any

from ..schema import load as load_schema, norm
from . import clean, escalations as esc, pipeline, rules
from .log import audit, emit
from .records import Record

ACTIONS = ("approve", "correct", "reject")


class InputError(ValueError):
    pass


def resolve(esc_id: int, action: str, value: Any, note: str, remember: bool, actor: str) -> dict:
    e = esc.get(esc_id)
    if not e:
        raise InputError("Escalation not found")
    if e["status"] != "open":
        raise InputError("This item was already resolved")
    if action not in ACTIONS:
        raise InputError(f"Unknown action '{action}'")
    if action == "approve":
        if not e["proposal"]:
            raise InputError("There is no suggestion to approve - use Correct")
        value = e["proposal"]["value"]
    elif action == "reject":
        value = None
    value = _check_input(e, action, value)
    resolution = {"action": action, "value": value, "note": note, "remember": remember}
    if not esc.claim(esc_id, resolution, actor):   # open -> processing; loses if someone else got there first
        raise InputError("This item was already resolved")
    pipeline.in_background(e["run_id"], _apply, e, action, value, note, remember, actor)
    return esc.get(esc_id)


def _check_input(e: dict, action: str, value: Any) -> Any:
    schema = load_schema()
    t = e["type"]
    if action == "reject":
        return None
    if t == "mapping":
        if value not in schema.all_mappable:
            raise InputError("Pick one of the target fields")
    elif t == "date_format":
        if value not in ("DMY", "MDY"):
            raise InputError("Pick day-first or month-first")
    elif t == "unknown_value":
        if value not in schema.fields[e["context"]["field"]].values:
            raise InputError("Pick one of the allowed values")
    elif t == "duplicate":
        if value not in ("merge", "keep_both"):
            raise InputError("Choose merge or keep both")
    elif t == "conflict":
        # Only the values the source files actually disagreed on may be chosen - never an arbitrary new value.
        if value not in [o["value"] for o in e["context"].get("options", [])]:
            raise InputError("Pick one of the values found in the source files")
        return value
    elif t in ("invalid_value", "validation", "push_failure"):
        proposed = (e["proposal"] or {}).get("value")
        if value in ("skip", "retry"):
            if value != proposed:
                raise InputError("That action isn't available for this item")
            return value
        if not isinstance(value, dict) or not value:
            raise InputError("Fill in at least one field")
        # A correction may only touch the fields this card offered - not, say, salary via a phone fix.
        correct = e["correct"] or {}
        offered = {f["name"] for f in correct.get("fields", [])} if correct.get("kind") == "fields" else set()
        if isinstance(proposed, dict):
            offered |= set(proposed)
        extra = sorted(set(value) - offered)
        if extra:
            raise InputError(f"These fields can't be changed from this item: {', '.join(extra)}")
        out = {}
        for fname, v in value.items():
            f = schema.fields.get(fname)
            if not f:
                raise InputError(f"Unknown field {fname}")
            if v is None or (isinstance(v, str) and v.strip() == ""):
                if f.required:
                    raise InputError(f"{f.label} is required")
                out[fname] = None
                continue
            try:
                out[fname], _ = clean.clean_value(f, v, schema, "DMY" if f.type == "date" else None)
            except clean.CleanError as err:
                raise InputError(f"{f.label}: {err.message}")
        return out
    return value


def _describe(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} = {v if v is not None else '(empty)'}" for k, v in value.items())
    return str(value)


def _apply(run_id: int, e: dict, action: str, value: Any, note: str, remember: bool, actor: str) -> None:
    """Runs under the run lock: apply the decision, only then mark it resolved, only then let the agent continue."""
    status = {"approve": "approved", "correct": "corrected", "reject": "rejected"}[action]
    try:
        then = _apply_effect(run_id, e, action, value, note, remember, actor)
    except Exception:
        esc.reopen(e["id"])  # put the card back in the queue rather than losing the decision silently
        raise
    esc.mark_resolved(e["id"], status, {"action": action, "value": value, "note": note, "remember": remember}, actor)
    then()


def _after_column_decision(run_id: int) -> None:
    left = esc.open_blocking(run_id)
    if left == 0:
        emit(run_id, "map", "info", "All column-level decisions made - continuing the migration", pace=False)
        pipeline.continue_run(run_id)
    else:
        emit(run_id, "map", "info", f"{left} column-level decision(s) still open", pace=False)


def _apply_effect(run_id: int, e: dict, action: str, value: Any, note: str, remember: bool, actor: str):
    """Persist the decision's effect; returns what the agent should do next."""
    t, ctx, keys = e["type"], e["context"], e["record_keys"]
    verb = {"approve": "approved", "correct": "corrected", "reject": "rejected"}[action]
    shown = "not migrated" if action == "reject" else _describe(value)
    emit(run_id, "review", "human", f"{actor} {verb}: {e['title']} -> {shown}", category=t, pace=False)
    audit(run_id, actor, f"resolved ({verb})", ", ".join(keys) or f"{ctx.get('file')} · {ctx.get('column')}",
          before={"agent_proposal": (e["proposal"] or {}).get("label")}, after=value if action != "reject" else "rejected",
          reason=note or e["title"])

    if t in ("mapping", "date_format"):
        _apply_column(run_id, e, action, value, remember, actor)
        return lambda: _after_column_decision(run_id)

    schema = load_schema()
    recs = {k: Record.load(run_id, k) for k in keys}
    if action == "reject":
        targets = [keys[-1]] if t == "duplicate" else keys
        for k in targets:
            r = recs[k]
            r.status, r.last_error = "skipped", f"Not migrated - decided by {actor}"
            r.save()
            if remember:
                rules.save("skip_record", k, f"skipped by {actor}", f"Always skip {k} ({r.name})", actor, run_id)
        remaining = [k for k in keys if k not in targets]
        return lambda: pipeline.process_records(run_id, remaining)

    if t == "unknown_value":
        fname = ctx["field"]
        for r in recs.values():
            r.set(fname, value, f"decided by {actor}: '{ctx['raw']}' -> '{value}'", by=actor)
            r.save()
        if remember:
            rules.save("value_map", f"{fname}|{clean.vkey(ctx['raw'])}", value,
                       f"{ctx['label']} '{ctx['raw']}' means '{value}'", actor, run_id)
    elif t == "conflict":
        fname, r = ctx["field"], recs[keys[0]]
        r.set(fname, value, f"decided by {actor} (sources disagreed)", by=actor)
        r.save()
        chosen = next((o for o in ctx["options"] if o["value"] == value), None)
        if remember and chosen:
            rules.save("source_priority", fname, chosen["files"][0],
                       f"When files disagree on {ctx['label']}, trust {chosen['files'][0]}", actor, run_id)
    elif t == "duplicate":
        a, b = recs[keys[0]], recs[keys[1]]
        if value == "merge":
            pipeline._merge(run_id, a, b, actor, note or "same person (human decision)")
        else:
            for r in (a, b):
                r.issues = [i for i in r.issues if i.get("code") != "duplicate"]
                r.save()
            audit(run_id, actor, "kept apart", f"{a.key}, {b.key}", reason=note or "different people (human decision)")
        if remember:
            rules.save("duplicate", f"{a.key}|{b.key}", value,
                       f"{a.key} & {b.key}: {'same person' if value == 'merge' else 'different people'}", actor, run_id)
    elif t in ("invalid_value", "validation", "push_failure"):
        r = recs[keys[0]]
        if value == "skip":
            r.status, r.last_error = "skipped", f"Not migrated - decided by {actor}"
            r.save()
            if remember:
                rules.save("skip_record", r.key, f"skipped by {actor}", f"Always skip {r.key} ({r.name})", actor, run_id)
        elif value == "retry":
            r.status = "failed"
            r.save()
        else:
            failing = {i.get("field") for i in r.issues} | ({ctx.get("field")} if ctx.get("field") else set())
            for fname, v in value.items():
                before = r.data.get(fname) if t != "invalid_value" else ctx.get("raw")
                r.set(fname, v, f"corrected by {actor}", by=actor)
                if remember:
                    if before not in (None, "") and fname in failing:
                        rules.save("value_map", f"{fname}|{clean.vkey(before)}", v,
                                   f"{schema.fields[fname].label} '{before}' -> '{v}'", actor, run_id)
                    else:
                        rules.save("record_value", f"{r.key}|{fname}", v,
                                   f"{r.key} {schema.fields[fname].label} = {v if v is not None else '(empty)'}",
                                   actor, run_id)
            r.save()
    todo = keys if t != "duplicate" or value != "merge" else [keys[0]]
    return lambda: pipeline.process_records(run_id, todo)


def _apply_column(run_id: int, e: dict, action: str, value: Any, remember: bool, actor: str) -> None:
    from .. import db

    ctx = e["context"]
    col = ctx["column"]
    schema = load_schema()
    if action == "reject":
        db.execute("UPDATE mappings SET target_field=NULL, status='ignored', method='human' WHERE id=?",
                   (ctx["mapping_id"],))
        if remember:
            rules.save("column_map", norm(col), None, f"Column '{col}' is not migrated", actor, run_id)
        return
    if e["type"] == "mapping":
        db.execute("UPDATE mappings SET target_field=?, status='mapped', method='human', confidence=1 WHERE id=?",
                   (value, ctx["mapping_id"]))
        if remember:
            rules.save("column_map", norm(col), value, f"Column '{col}' -> {schema.all_mappable[value].label}", actor, run_id)
    else:
        db.execute("UPDATE mappings SET date_format=? WHERE id=?", (value, ctx["mapping_id"]))
        if remember:
            rules.save("date_format", norm(col), value,
                       f"Column '{col}' is {pipeline.DATE_LABEL[value]}", actor, run_id)
