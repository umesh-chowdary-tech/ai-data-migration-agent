"""Review of a rule change BEFORE it is saved.

A rule is a standing permission for the agent to decide alone, so a human-written rule gets three checks:

  1. validation   - the rule must be as valid as a decision taken on a card (real field, allowed value, clean data)
  2. impact       - measured on the latest real data, deterministically: what it would touch, whether the agent already
                    reaches the same answer on its own (not needed), whether it would override the evidence, and
                    whether it contradicts an earlier human decision. Each finding is a numbered fact (F1, F2, ...).
  3. AI review    - an open-weight model reads ONLY those facts and says whether the rule is needed and safe, citing
                    fact ids for every claim (unknown ids are stripped). It advises; it cannot block and cannot add
                    numbers of its own. With no AI available, the review is the data checks alone.

The human then saves with a reason - and must explicitly acknowledge any warning or negative AI verdict. The server
only ever saves the exact proposal that was reviewed.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from .. import db
from ..schema import Field, load as load_schema, norm
from . import clean, mapper, pipeline, problems, rules
from . import escalations as esc
from . import validate as val
from .llm import UNTRUSTED, get as get_llm
from .profile import profile_column, value_score
from .records import Record

REVIEW_TTL_SECONDS = 1800
_reviews: dict[str, dict] = {}
_lock = threading.Lock()

SCOPE = {
    "column_map": "every file, in every future run, with a column of this name",
    "date_format": "every future run where a column of this name is ambiguous (the agent never uses it on dates the data already proves)",
    "value_map": "every future run where this field has this exact value (it replaces the agent's own cleaning)",
    "source_priority": "every future run where files disagree on this field (it is checked before 'the only valid value wins')",
    "record_value": "only this employee, in every future run",
    "duplicate": "only these two employees, in every future run",
    "issue_policy": "every employee, in every future run, whose value has this problem (it will be left empty and "
                    "nobody will be asked)",
}


class RuleError(ValueError):
    pass


class Facts:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, level: str, text: str) -> str:
        fid = f"F{len(self.items) + 1}"
        self.items.append({"id": fid, "level": level, "text": text})
        return fid


# ------------------------------------------------------------------------------------------------
# 1. validation -> a normalised proposal
# ------------------------------------------------------------------------------------------------

def _clean_for(f: Field, value: Any, allow_empty: bool = False) -> Any:
    schema = load_schema()
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_empty:
            return None
        raise RuleError(f"{f.label} needs a value")
    if f.type == "enum":
        canonical, how = clean.enum_match(f, value)
        if canonical is None or how == "typo":
            raise RuleError(f"'{value}' is not an allowed {f.label.lower()} ({', '.join(f.values)})")
        return canonical
    try:
        out, _ = clean.clean_value(f, value, schema, "DMY" if f.type == "date" else None)
    except clean.CleanError as e:
        raise RuleError(f"{f.label}: {e.message}")
    issues = val.field_issues(f.name, out, schema)
    if issues:
        raise RuleError(issues[0].message)
    return out


def normalise(p: dict) -> dict:
    schema = load_schema()
    fields = schema.fields
    existing = None
    if p.get("rule_id"):
        existing = rules.get(int(p["rule_id"]))
        if not existing:
            raise RuleError("Rule not found")
    kind = existing["kind"] if existing else p.get("kind")
    if not existing and kind not in rules.PATTERN_KINDS:
        raise RuleError("Only pattern rules (column mapping, date format, value fix, source of truth) can be added by "
                        "hand. Rules about one employee come from a decision taken on that employee's records.")
    if kind == "skip_record":
        raise RuleError("A skip rule can't be edited - delete it and the agent will ask about that employee again")
    if existing and kind == "issue_policy":
        raise RuleError("A problem rule has only one action (leave it empty) - delete it to go back to asking")

    prop: dict[str, Any] = {"kind": kind, "rule_id": existing["id"] if existing else None,
                            "before": existing["value"] if existing else None}
    if kind in ("column_map", "date_format"):
        header = existing["key"] if existing else str(p.get("header") or "").strip()
        if not header:
            raise RuleError("Enter the column header the rule is about")
        prop["header"], prop["key"] = header, norm(header)
        if kind == "column_map":
            target = p.get("target") or None
            if target is not None and target not in schema.all_mappable:
                raise RuleError(f"'{target}' is not a field of the new system")
            prop["value"] = target
            prop["description"] = (f"Column '{header}' -> {schema.all_mappable[target].label}" if target
                                   else f"Column '{header}' is not migrated")
        else:
            fmt = p.get("format")
            if fmt not in ("DMY", "MDY"):
                raise RuleError("Choose day-first or month-first")
            prop["value"] = fmt
            prop["description"] = f"Column '{header}' is {pipeline.DATE_LABEL[fmt]}"
    elif kind == "value_map":
        if existing:
            field, raw = existing["key"].split("|", 1)
        else:
            field, raw = p.get("field"), str(p.get("raw") or "").strip()
        if field not in fields:
            raise RuleError("Choose a field")
        if not raw:
            raise RuleError("Enter the value as it appears in the client's files")
        if not existing and fields[field].type != "enum":
            raise RuleError(f"Exact-value rules only make sense for category fields (department, location, ...). For "
                            f"{fields[field].label.lower()} each bad value belongs to one person, so use a 'whenever "
                            "this problem happens' rule, or decide on that employee's card.")
        prop["field"], prop["raw"], prop["key"] = field, raw, f"{field}|{clean.vkey(raw)}"
        prop["value"] = _clean_for(fields[field], p.get("value"), allow_empty=not fields[field].required)
        prop["description"] = f"{fields[field].label} '{raw}' -> '{prop['value'] if prop['value'] is not None else '(empty)'}'"
    elif kind == "source_priority":
        field = existing["key"] if existing else p.get("field")
        file = str(p.get("file") or "").strip()
        if field not in fields:
            raise RuleError("Choose a field")
        if not file:
            raise RuleError("Choose the file that should win")
        prop.update(field=field, key=field, value=file,
                    description=f"When files disagree on {fields[field].label}, trust {file}")
    elif kind == "issue_policy":
        field, code = p.get("field"), p.get("problem")
        if field not in fields:
            raise RuleError("Choose a field")
        f = fields[field]
        if f.required:
            raise RuleError(f"{f.label} is required - the agent can't leave it empty, so it has to keep asking")
        if not problems.eligible(f, code):
            raise RuleError(f"Choose a problem that can happen to {f.label.lower()}")
        prop.update(field=field, problem=code, key=f"{field}|{code}", value=problems.ACTION,
                    description=problems.describe(f, code))
        if p.get("replaces"):
            old = rules.get(int(p["replaces"]))
            if not old or old["kind"] not in ("value_map", "record_value") or field not in old["key"].split("|"):
                raise RuleError("The rule to replace doesn't match this field")
            prop["replaces"] = old["id"]
            prop["replaces_description"] = old["description"]
    elif kind == "record_value":
        rec_key, field = existing["key"].split("|", 1)
        f = fields[field]
        v = _clean_for(f, p.get("value"), allow_empty=not f.required)
        prop.update(key=existing["key"], record=rec_key, field=field, value=v,
                    description=f"{rec_key} {f.label} = {v if v is not None else '(empty)'}")
    elif kind == "duplicate":
        v = p.get("value")
        if v not in ("merge", "keep_both"):
            raise RuleError("Choose 'same person' or 'different people'")
        a, b = existing["key"].split("|")
        prop.update(key=existing["key"], value=v,
                    description=f"{a} & {b}: {'same person' if v == 'merge' else 'different people'}")
    else:
        raise RuleError("Unknown rule type")
    if existing and existing["value"] == prop["value"]:
        raise RuleError("Nothing changed")
    return prop


# ------------------------------------------------------------------------------------------------
# 2. impact on the latest real data (deterministic)
# ------------------------------------------------------------------------------------------------

def _latest_column(key: str, date_only: bool = False) -> dict | None:
    schema = load_schema()
    for m in db.query("SELECT * FROM mappings ORDER BY run_id DESC, id"):
        if norm(m["source_column"]) != key:
            continue
        if date_only and not (m["target_field"] and schema.all_mappable[m["target_field"]].type == "date"):
            continue
        return m
    return None


def _latest_run_with_records() -> int | None:
    row = db.one("SELECT MAX(run_id) AS r FROM records")
    return row["r"] if row else None


def _column_frame(m: dict):
    try:
        frames, _ = pipeline.load_files(m["run_id"])
    except Exception:
        return None, None
    df = frames.get(m["file"])
    return (df, frames) if df is not None and m["source_column"] in df.columns else (None, None)


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def analyse(prop: dict) -> tuple[list[dict], str]:
    """Returns (facts, needed) where needed is yes | no | overrides | unknown."""
    schema = load_schema()
    F = Facts()
    kind, value = prop["kind"], prop["value"]
    F.add("info", f"Applies to {SCOPE[kind]}.")
    if not prop["rule_id"] and rules.find(kind, prop["key"]):
        F.add("warn", f"Replaces an existing rule: {rules.find(kind, prop['key'])['description']}.")
    needed = "unknown"

    if kind == "column_map":
        m = _latest_column(prop["key"])
        if not m:
            F.add("info", "No column with this name has appeared in any run yet, so the effect can't be checked "
                          "against real data.")
            return F.items, needed
        df, _ = _column_frame(m)
        if df is None:
            F.add("info", f"Run #{m['run_id']}'s files are no longer available to check against.")
            return F.items, needed
        file, col = m["file"], m["source_column"]
        profiles = [profile_column(file, c, df[c], schema) for c in df.columns]
        prof = next(p for p in profiles if p.name == col)
        F.add("info", f"In run #{m['run_id']}, {file} has '{col}' with values like {', '.join(prof.samples[:4])}.")
        decisions = mapper.map_file(file, profiles, schema, None, {})  # the evidence alone: no rules, no AI
        d = next(x for x in decisions if x.column == col)
        if value:
            fit, why = value_score(prof, schema.all_mappable[value])
            F.add("ok" if fit >= 0.8 else "warn" if fit < 0.5 else "info",
                  f"{_pct(fit)} of the values fit {schema.all_mappable[value].label} ({why}).")
        if d.method == "auto":
            F.add("info", f"Without a rule the agent maps it to {schema.all_mappable[d.target].label} on its own "
                          f"({_pct(d.confidence)} confident).")
        elif d.method == "escalate":
            F.add("info", f"Without a rule the agent asks a consultant, because {d.reason}.")
        else:
            F.add("info", "Without a rule the agent leaves this column out (no target field fits).")
        if (d.method == "auto" and d.target == value) or (d.method == "unmapped" and value is None):
            needed = "no"
            F.add("warn", "Not needed: the agent already reaches the same answer from the data.")
        elif d.method == "escalate":
            needed = "yes"
            F.add("ok", "Useful: it answers a question the agent currently has to ask.")
        else:
            needed = "overrides"
            F.add("warn", "It would override the agent's evidence-based decision above.")
        if value:
            other = next((x for x in decisions if x.column != col and x.target
                          and mapper._conflicts(value, {x.target}, schema)), None)
            if other:
                F.add("warn", f"In {file}, '{other.column}' is already mapped to {schema.all_mappable[other.target].label}. "
                              f"Rules are applied first, so '{other.column}' would lose that mapping.")

    elif kind == "date_format":
        m = _latest_column(prop["key"], date_only=True)
        if not m:
            F.add("info", "No date column with this name has appeared in any run yet, so the effect can't be checked.")
            return F.items, needed
        df, frames = _column_frame(m)
        if df is None:
            F.add("info", f"Run #{m['run_id']}'s files are no longer available to check against.")
            return F.items, needed
        values = list(df[m["source_column"]])
        detected, stats = clean.detect_date_format(values)
        label = pipeline.DATE_LABEL[value]
        if detected in ("DMY", "MDY"):
            needed = "no"
            if detected == value:
                F.add("warn", f"Not needed: the data already proves it is {label}.")
            else:
                F.add("warn", f"The data proves this column is {pipeline.DATE_LABEL[detected]}. Date rules are "
                              "only used for ambiguous columns, so it would have no effect here - but it contradicts "
                              "the data.")
        elif detected is None:
            needed = "no"
            F.add("warn", "Not needed: the column holds unambiguous dates (ISO or real Excel dates).")
        elif detected == "conflict":
            F.add("warn", "The column mixes values that are only valid day-first with values only valid month-first; "
                          "a single format can't read all of them.")
        else:
            needed = "yes"
            F.add("ok", f"Useful: all {stats['numeric_values']} values in run #{m['run_id']} read validly both ways, "
                        "so the agent currently has to ask.")
            maps = db.query("SELECT * FROM mappings WHERE run_id=? AND status='mapped' AND target_field IS NOT NULL",
                            (m["run_id"],))
            dmy, mdy = pipeline._date_evidence(m, frames, maps)
            chosen, other = (dmy, mdy) if value == "DMY" else (mdy, dmy)
            F.add("ok" if chosen > other else "warn" if other > chosen else "info",
                  f"Cross-check with other files: {dmy} employee(s) match day-first, {mdy} match month-first.")
        bad = 0
        for v in values:
            mt = clean.NUMERIC_DATE.match(clean.text(v))
            if mt:
                try:
                    clean.parse_date(v, value)
                except clean.CleanError:
                    bad += 1
        if bad:
            F.add("warn", f"{bad} value(s) in this column are not real dates when read as {label}.")

    elif kind == "value_map":
        f = schema.fields[prop["field"]]
        raw = prop["raw"]
        if f.type != "enum":
            F.add("warn", f"This is an exact-value rule on a free-form field: it only fires for the exact text '{raw}', "
                          "and it would give every employee with that text the same value - possibly someone else's. "
                          "Consider making it generic ('whenever this problem happens') instead.")
        try:
            auto, _ = clean.clean_value(f, raw, schema, None)
            if auto == value:
                needed = "no"
                F.add("warn", f"Not needed: the agent already turns '{raw}' into '{auto}' on its own.")
            else:
                needed = "overrides"
                F.add("warn", f"The agent would normally clean '{raw}' to '{auto}'; the rule replaces that with "
                              f"'{value}'.")
        except clean.CleanError as e:
            needed = "yes"
            F.add("ok", f"Useful: the agent can't clean '{raw}' on its own ({e.message}) and asks a consultant.")
        run = _latest_run_with_records()
        if run:
            cols = [(m["file"], m["source_column"]) for m in db.query(
                "SELECT file, source_column FROM mappings WHERE run_id=? AND target_field=?", (run, f.name))]
            hits = []
            for r in Record.load_all(run):
                if any(row["file"] == file and clean.vkey(row["raw"].get(col) or "") == clean.vkey(raw)
                       for row in r.rows for file, col in cols):
                    hits.append(r)
            if hits:
                F.add("info", f"In run #{run}, {len(hits)} employee(s) have {f.label} = '{raw}': "
                              + ", ".join(f"{r.key} {r.name}" for r in hits[:5]) + ("..." if len(hits) > 5 else "") + ".")
                for r in hits:
                    human = [c for c in r.changes if c.get("field") == f.name and c.get("by") not in ("agent", None)]
                    if human and r.data.get(f.name) != value:
                        F.add("warn", f"In run #{run} {human[-1]['by']} set {r.key}'s {f.label.lower()} to "
                                      f"'{r.data.get(f.name)}'; the rule says '{value}'.")
            else:
                F.add("info", f"'{raw}' doesn't appear in the latest run (#{run}).")

    elif kind == "source_priority":
        f = schema.fields[prop["field"]]
        file = value
        known = {r["file"] for r in db.query("SELECT DISTINCT file FROM mappings")}
        if file not in known:
            F.add("warn", f"No run has contained a file named '{file}'. The rule matches the exact file name, so "
                          "check the spelling.")
            return F.items, needed
        run = db.one("SELECT MAX(run_id) AS r FROM mappings WHERE file=?", (file,))["r"]
        maps = db.query("SELECT * FROM mappings WHERE run_id=? AND target_field=? AND status='mapped'", (run, f.name))
        if not any(m["file"] == file for m in maps):
            needed = "no"
            F.add("warn", f"{file} has no column mapped to {f.label}, so the rule could never apply.")
            return F.items, needed
        conflicts = differs = invalid_pref = 0
        for r in Record.load_all(run):
            vals: dict[str, Any] = {}
            for m in maps:
                fmt = m["date_format"] if m["date_format"] in ("DMY", "MDY") else None
                for row in r.rows:
                    raw = row["raw"].get(m["source_column"]) if row["file"] == m["file"] else None
                    if raw is None or clean.is_placeholder(raw):
                        continue
                    try:
                        out, _ = clean.clean_value(f, raw, load_schema(), fmt)
                        vals[m["file"]] = out if not val.field_issues(f.name, out, load_schema()) else ("invalid", out)
                    except clean.CleanError:
                        vals[m["file"]] = ("invalid", raw)
            distinct = {json.dumps(v, default=str) for v in vals.values()}
            if file in vals and len(distinct) > 1:
                conflicts += 1
                if isinstance(vals[file], tuple):
                    invalid_pref += 1
                elif r.data.get(f.name) != vals[file]:
                    differs += 1
        needed = "yes" if conflicts else "no"
        F.add("ok" if conflicts else "warn",
              f"In run #{run}, {conflicts} employee(s) had files disagreeing on {f.label} with {file} involved."
              if conflicts else f"Not needed in the last run (#{run}): no disagreements on {f.label} involving {file}.")
        if differs:
            F.add("warn", f"For {differs} of them the final value came from another file (a consultant chose it); the "
                          f"rule would have picked {file}'s value instead.")
        if invalid_pref:
            F.add("warn", f"For {invalid_pref} of them {file}'s value was invalid and another file had a valid one. The "
                          "rule is checked first, so the invalid value would win and then fail validation.")

    elif kind == "issue_policy":
        f, code = schema.fields[prop["field"]], prop["problem"]
        if prop.get("replaces"):
            old = rules.get(prop["replaces"])
            F.add("info", f"Replaces the exact-value rule: {prop['replaces_description']} (it has been applied "
                          f"{old['times_applied'] if old else 0} time(s) so far).")
        F.add("warn", "Values with this problem will be dropped without anyone being asked. Each one is still recorded "
                      "in that employee's history as 'cleared by your rule'.")

        def card_code(c: dict) -> str | None:
            # cards created by older versions don't store the problem type: work it out from the raw value
            return c.get("code") or (problems.infer(f, c.get("raw"), schema) if c.get("raw") is not None else None)

        def matches(e: dict) -> bool:
            c = e["context"] or {}
            return (e["type"] == "invalid_value" and c.get("field") == f.name and card_code(c) == code) or \
                (e["type"] == "validation" and any(i.get("field") == f.name and i.get("code") == code
                                                   for i in c.get("issues", []))) or \
                (e["type"] == "push_failure" and code == "manager_not_found" and c.get("error") == code)

        # the most recent run where this problem actually reached a consultant (a rule may have hidden it since)
        run, cases = None, []
        for row in db.query("SELECT id FROM runs ORDER BY id DESC"):
            cases = [e for e in esc.list_for_run(row["id"]) if matches(e)]
            if cases:
                run = row["id"]
                break
        if cases:
            needed = "yes"
            keys = sorted({k for e in cases for k in e["record_keys"]})
            F.add("ok", f"Useful: in run #{run} a consultant was asked {len(cases)} time(s) because "
                        f"{f.label.lower()} {problems.CATALOG[code][1]}: {', '.join(keys[:6])}.")
            for e in cases:
                res, k = e.get("resolution") or {}, (e["record_keys"] or ["?"])[0]
                v = res.get("value")
                if e["status"] in ("open", "processing"):
                    continue
                if isinstance(v, dict) and v.get(f.name) not in (None, ""):
                    F.add("warn", f"For {k} the consultant entered a real value ('{v[f.name]}') instead of leaving it "
                                  "empty - with this rule that value would have been lost.")
                elif isinstance(v, dict) and f.name in v:
                    F.add("ok", f"For {k} the consultant also chose to leave it empty.")
        else:
            F.add("info", "This problem hasn't reached a consultant in any run so far, so the rule can't be checked "
                          "against real cases yet.")
    elif kind == "record_value":
        run = _latest_run_with_records()
        r = Record.load(run, prop["record"]) if run else None
        f = schema.fields[prop["field"]]
        needed = "yes"
        if r:
            F.add("info", f"Only affects {r.key} {r.name}; in run #{run} their {f.label.lower()} is "
                          f"'{r.data.get(f.name)}'.")
        else:
            F.add("info", f"{prop['record']} isn't in the latest run.")
    elif kind == "duplicate":
        run = _latest_run_with_records()
        a, b = prop["key"].split("|")
        ra, rb = (Record.load(run, a), Record.load(run, b)) if run else (None, None)
        needed = "yes"
        if ra and rb:
            F.add("info", f"{a}: {ra.name}, born {ra.data.get('date_of_birth')}; {b}: {rb.name}, born "
                          f"{rb.data.get('date_of_birth')}.")
        if value == "keep_both":
            F.add("warn", f"{b} would be migrated as a separate employee in future runs.")
        else:
            F.add("warn", f"{b} would be merged into {a} (its details only fill gaps) in future runs.")
    return F.items, needed


# ------------------------------------------------------------------------------------------------
# 3. AI review - advisory, grounded in the facts
# ------------------------------------------------------------------------------------------------

def ai_review(prop: dict, facts: list[dict]) -> dict:
    llm = get_llm()
    if not llm.available:
        return {"available": False, "reason": llm.reason_unavailable()}
    system = ("You review a proposed standing rule for an HR data-migration agent. A rule makes the agent decide "
              "automatically in future runs instead of asking a consultant, so a wrong rule silently changes data. "
              "Judge (1) whether the rule is needed and (2) whether it is safe, using ONLY the numbered facts - never "
              "invent numbers or facts. Every effect and risk must cite the fact ids it relies on (e.g. \"F3\"). "
              "verdict must be recommend, caution or not_recommended; needed must be yes, no or unclear. " + UNTRUSTED)
    user = json.dumps({
        "rule": {"type": prop["kind"], "meaning": prop["description"], "applies_to": SCOPE[prop["kind"]],
                 "previous_value": prop["before"]},
        "facts": [{"id": f["id"], "text": f["text"]} for f in facts],
        "output_format": {"verdict": "recommend|caution|not_recommended", "needed": "yes|no|unclear",
                          "summary": "<= 2 sentences", "effects": [{"text": "", "cites": ["F1"]}],
                          "risks": [{"text": "", "cites": ["F2"]}]},
    }, ensure_ascii=False, default=str)
    res = llm.complete_json(system, user, max_tokens=1800)
    if res.data is None:
        return {"available": False, "reason": res.failure_text() or llm.reason_unavailable()}
    ids = {f["id"] for f in facts}

    def items(key: str) -> list[dict]:
        out = []
        for x in (res.data.get(key) or [])[:6]:
            if isinstance(x, dict) and x.get("text"):
                out.append({"text": str(x["text"])[:300], "cites": [c for c in (x.get("cites") or []) if c in ids]})
            elif isinstance(x, str) and x.strip():
                out.append({"text": x[:300], "cites": []})
        return out

    verdict = res.data.get("verdict")
    needed = res.data.get("needed")
    return {"available": True, "model": res.answered_by, "fell_back": bool(res.failures),
            "verdict": verdict if verdict in ("recommend", "caution", "not_recommended") else "caution",
            "needed": needed if needed in ("yes", "no", "unclear") else "unclear",
            "summary": str(res.data.get("summary") or "")[:400], "effects": items("effects"), "risks": items("risks")}


# ------------------------------------------------------------------------------------------------
# public API: review, then save exactly what was reviewed
# ------------------------------------------------------------------------------------------------

def review(p: dict) -> dict:
    prop = normalise(p)
    facts, needed = analyse(prop)
    ai = ai_review(prop, facts)
    warnings = [f for f in facts if f["level"] == "warn"]
    needs_ack = bool(warnings) or needed in ("no", "overrides") or \
        (ai.get("available") and ai.get("verdict") in ("caution", "not_recommended"))
    rec = {"id": uuid.uuid4().hex, "proposal": prop, "facts": facts, "needed": needed, "ai": ai,
           "needs_acknowledgement": needs_ack, "created_at": time.time()}
    with _lock:
        for k in [k for k, v in _reviews.items() if time.time() - v["created_at"] > REVIEW_TTL_SECONDS]:
            _reviews.pop(k, None)
        _reviews[rec["id"]] = rec
    return rec


def save(review_id: str, reason: str, actor: str, acknowledge: bool) -> dict:
    with _lock:
        rec = _reviews.get(review_id)
    if not rec or time.time() - rec["created_at"] > REVIEW_TTL_SECONDS:
        raise RuleError("Review this change first (or again - reviews expire after 30 minutes)")
    if len((reason or "").strip()) < 5:
        raise RuleError("Give a reason - it's kept in the rule's history")
    if rec["needs_acknowledgement"] and not acknowledge:
        raise RuleError("This change has warnings or the AI advised caution - confirm you've read them to save anyway")
    prop = rec["proposal"]
    if prop["rule_id"]:
        current = rules.get(prop["rule_id"])
        if not current or current["value"] != prop["before"]:
            raise RuleError("The rule changed since it was reviewed - review it again")
    snapshot = {"needed": rec["needed"], "facts": rec["facts"],
                "ai": {k: rec["ai"].get(k) for k in ("available", "model", "verdict", "needed", "summary", "reason")},
                "acknowledged_warnings": bool(acknowledge and rec["needs_acknowledgement"])}
    rule_id = rules.save(prop["kind"], prop["key"], prop["value"], prop["description"], actor, None,
                         origin="manual", reason=reason.strip(), review=snapshot)
    if prop.get("replaces") and rules.get(prop["replaces"]):
        rules.delete(prop["replaces"], actor, f"replaced by the generic rule: {prop['description']}")
    with _lock:
        _reviews.pop(review_id, None)
    return rules.get(rule_id)


def generalisation(rule_id: int) -> dict:
    """Turn an exact-value rule on a free-form field into the 'whenever this problem happens' rule it stands for."""
    schema = load_schema()
    r = rules.get(rule_id)
    if not r or r["kind"] != "value_map":
        raise RuleError("Only exact-value rules can be made generic")
    field, raw = r["key"].split("|", 1)
    f = schema.fields.get(field)
    if not f or f.type == "enum":
        raise RuleError("Category rules are already generic: the exact word is what carries the meaning")
    code = problems.infer(f, raw, schema)
    if not problems.eligible(f, code):
        raise RuleError(f"Can't turn this into a generic rule: either '{raw}' has no recognisable problem, or "
                        f"{f.label.lower()} is required and can't be left empty. Keep it, or delete it.")
    return {"field": field, "problem": code, "description": problems.describe(f, code), "replaces": r["id"],
            "note": None if r["value"] in (None, "") else
            f"The old rule sets '{r['value']}'. A generic rule can only leave the value empty - a specific value "
            "belongs to one employee, so decide that on the employee's card instead."}
