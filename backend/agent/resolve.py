"""Applying a human decision (approve / correct / reject) to an escalation, then letting the agent carry on.

Input is checked synchronously (so the UI can show "that's not a valid phone number" immediately);
the effect is applied in the background under the run lock, and the pipeline resumes by itself.
"""
from __future__ import annotations

from typing import Any

from ..schema import load as load_schema, norm
from . import clean, escalations as esc, pipeline, problems, rules
from . import validate as validate_mod
from .log import audit, emit
from .records import Record

ACTIONS = ("approve", "correct", "reject")


class InputError(ValueError):
    pass


SCOPED_TYPES = ("invalid_value", "validation", "push_failure")


def resolve(esc_id: int, action: str, value: Any, note: str, remember: bool, actor: str,
            scope: str | None = None) -> dict:
    """scope (for cards about one employee's values): "record" = remember for this employee only (the default),
    "problem" = remember as a rule for this kind of problem, for every employee (only 'leave it empty')."""
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
    value = _check_input(e, action, value, note)
    scope = scope or "record"
    if scope not in ("record", "problem"):
        raise InputError(f"Unknown scope '{scope}'")
    if remember and scope == "problem" and not problem_targets(e, value):
        raise InputError("A 'whenever this happens' rule only works when you leave an optional field empty - "
                         "remember it for this employee instead")
    resolution = {"action": action, "value": value, "note": note, "remember": remember,
                  "scope": scope if e["type"] in SCOPED_TYPES else None}
    if not esc.claim(esc_id, resolution, actor):   # open -> processing; loses if someone else got there first
        raise InputError("This item was already resolved")
    pipeline.in_background(e["run_id"], _apply, e, action, value, note, remember, actor, scope)
    return esc.get(esc_id)


def _problem_candidates(e: dict) -> list[tuple[str, str]]:
    c = e.get("context") or {}
    if e["type"] == "invalid_value":
        code = c.get("code")
        schema = load_schema()
        if not code and c.get("field") in schema.fields and c.get("raw") is not None:
            code = problems.infer(schema.fields[c["field"]], c["raw"], schema)  # cards from older versions
        return [(c.get("field"), code)]
    if e["type"] == "validation":
        return [(i.get("field"), i.get("code")) for i in c.get("issues", [])]
    if e["type"] == "push_failure" and c.get("error") == "manager_not_found":
        return [("manager_email", "manager_not_found")]
    return []


def problem_targets(e: dict, value: Any) -> list[tuple[str, str]]:
    """(field, problem) pairs a 'whenever this happens' rule could cover for this decision."""
    schema = load_schema()
    if not isinstance(value, dict):
        return []
    return [(f, code) for f, code in _problem_candidates(e)
            if f in schema.fields and f in value and value[f] is None and problems.eligible(schema.fields[f], code)]


def remember_options(e: dict) -> list[dict] | None:
    """What 'remember' can mean for this card - shown to the consultant so the scope is explicit."""
    if e["type"] not in SCOPED_TYPES:
        return None
    schema = load_schema()
    rec = (e.get("context") or {}).get("record") or {}
    opts = [{"value": "record", "label": f"Only for {rec.get('key', 'this employee')} ({rec.get('name', '')})".replace(" ()", "")}]
    for f, code in _problem_candidates(e):
        if f in schema.fields and problems.eligible(schema.fields[f], code):
            opts.append({"value": "problem", "field": f,
                         "label": problems.describe(schema.fields[f], code) + " - for every employee"})
            break
    return opts


def _check_input(e: dict, action: str, value: Any, note: str = "") -> Any:
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
        if isinstance(value, dict) and "other" in value:
            # A value that is in NO source file: allowed where the card offers it, but it must pass the same checks
            # as file data and the consultant must say why (it overrides every source).
            if not (e["correct"] or {}).get("other"):
                raise InputError("This item only accepts the values found in the source files")
            if len((note or "").strip()) < 5:
                raise InputError("Add a note explaining where this value comes from - it isn't in any source file")
            f = schema.fields[e["context"]["field"]]
            if value["other"] in (None, ""):
                raise InputError(f"Enter the correct {f.label.lower()}")
            try:
                cleaned, _ = clean.clean_value(f, value["other"], schema, "DMY" if f.type == "date" else None)
            except clean.CleanError as err:
                raise InputError(f"{f.label}: {err.message}")
            issues = validate_mod.field_issues(f.name, cleaned, schema)
            if issues:
                raise InputError(issues[0].message)
            return {"other": cleaned}
        # Otherwise only the values the source files actually disagreed on may be chosen.
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
    if isinstance(value, dict) and set(value) == {"other"}:
        return f"{value['other']} (entered by hand - not from any source file)"
    if isinstance(value, dict):
        return ", ".join(f"{k} = {v if v is not None else '(empty)'}" for k, v in value.items())
    return str(value)


def _apply(run_id: int, e: dict, action: str, value: Any, note: str, remember: bool, actor: str,
           scope: str = "record") -> None:
    """Runs under the run lock: apply the decision, only then mark it resolved, only then let the agent continue."""
    status = {"approve": "approved", "correct": "corrected", "reject": "rejected"}[action]
    try:
        then = _apply_effect(run_id, e, action, value, note, remember, actor, scope)
    except Exception:
        esc.reopen(e["id"])  # put the card back in the queue rather than losing the decision silently
        raise
    esc.mark_resolved(e["id"], status, {"action": action, "value": value, "note": note, "remember": remember,
                                        "scope": scope if e["type"] in SCOPED_TYPES else None}, actor)
    then()


def _shown(v: Any) -> str:
    return "(leave empty)" if v is None or v == "" else str(v)


def _after_column_decision(run_id: int) -> None:
    left = esc.open_blocking(run_id)
    if left == 0:
        emit(run_id, "map", "info", "All column-level decisions made - continuing the migration", pace=False)
        pipeline.continue_run(run_id)
    else:
        emit(run_id, "map", "info", f"{left} column-level decision(s) still open", pace=False)


def _apply_effect(run_id: int, e: dict, action: str, value: Any, note: str, remember: bool, actor: str,
                  scope: str = "record"):
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
        if isinstance(value, dict) and "other" in value:
            r.set(fname, value["other"], f"entered by {actor} - not from any source file ({note})", by=actor)
            r.save()
            if remember:  # no file "won", so the only thing to remember is this employee's value
                rules.save("record_value", f"{r.key}|{fname}", value["other"],
                           f"{r.key} {ctx['label']} = {value['other']}", actor, run_id)
        else:
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
            # Remembering a correction of a free-form value (phone, email, date...) is about THIS employee: an
            # exact-value rule would copy one person's value onto anyone else with the same bad input.
            covered = problem_targets(e, value) if remember and scope == "problem" else []
            covered_fields = {f for f, _ in covered}
            for fname, v in value.items():
                r.set(fname, v, f"corrected by {actor}", by=actor)
                if remember and fname not in covered_fields:
                    rules.save("record_value", f"{r.key}|{fname}", v,
                               f"{r.key} {schema.fields[fname].label} = {_shown(v)}", actor, run_id)
            for fname, code in covered:
                rules.save("issue_policy", f"{fname}|{code}", problems.ACTION,
                           problems.describe(schema.fields[fname], code), actor, run_id)
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
