"""Schema validation, the single safe auto-repair pass, and fix *suggestions* for the human.

Repairs are limited to syntax the data can only mean one way (a comma typed for a dot in an email,
a schema default for a missing status). Anything that requires inferring intent becomes a
suggestion on an escalation card instead - the human confirms it with one click.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from ..schema import TargetSchema
from . import clean, rules
from .records import Record

PHONE_RE = re.compile(r"^\+91[6-9]\d{9}$")
NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]*$")   # a person's name - not a sentence, formula or instruction
FREE_MAIL = ("gmail.", "yahoo.", "outlook.", "hotmail.", "rediffmail.", "icloud.", "proton")


@dataclass
class Issue:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"field": self.field, "code": self.code, "message": self.message}


def validate(data: dict[str, Any], schema: TargetSchema) -> list[Issue]:
    issues: list[Issue] = []
    for name in schema.fields:
        issues.extend(field_issues(name, data.get(name), schema))
    return issues


def field_issues(name: str, v: Any, schema: TargetSchema) -> list[Issue]:
    f = schema.fields[name]
    issues: list[Issue] = []
    if v in (None, ""):
        if f.required:
            issues.append(Issue(name, "missing", f"{f.label} is required but missing"))
        return issues
    if f.pattern and not re.fullmatch(f.pattern, str(v)):
        issues.append(Issue(name, "pattern", f"{f.label} '{v}' doesn't match the required format"))
    if f.type == "name":
        s = str(v)
        if len(s) > 40 or len(s.split()) > 4 or not NAME_RE.match(s):
            issues.append(Issue(name, "suspicious_name", f"{f.label} '{s[:40]}' doesn't look like a person's name"))
    if f.type == "email":
        s = str(v)
        if not clean.EMAIL_STRICT.match(s):
            issues.append(Issue(name, "email_format", f"'{s}' is not a valid email address"))
        elif f.email_kind == "work" and s.split("@", 1)[1] not in schema.company_domains:
            issues.append(Issue(name, "email_domain",
                                f"'{s}' is not on the company domain ({', '.join(schema.company_domains)})"))
    elif f.type == "phone" and not PHONE_RE.match(str(v)):
        issues.append(Issue(name, "phone_format", f"'{v}' is not a valid mobile number"))
    elif f.type == "date":
        try:
            d = dt.date.fromisoformat(str(v))
            if f.date_range and not (f.date_range[0] <= d.year <= f.date_range[1]):
                issues.append(Issue(name, "date_range", f"{f.label} {v} is outside {f.date_range[0]}-{f.date_range[1]}"))
        except ValueError:
            issues.append(Issue(name, "date_format", f"'{v}' is not a valid date"))
    elif f.type == "money" and f.minimum is not None and float(v) < f.minimum:
        issues.append(Issue(name, "too_small", f"{f.label} {v} is below the minimum {f.minimum:g}"))
    elif f.type == "enum" and v not in f.values:
        issues.append(Issue(name, "not_allowed", f"'{v}' is not an allowed {f.label.lower()}"))
    return issues


def repair(record: Record, issues: list[Issue], schema: TargetSchema) -> list[str]:
    """One safe repair pass. Returns human-readable notes of what was changed."""
    notes = []
    for issue in issues:
        f = schema.fields[issue.field]
        current = record.data.get(issue.field)
        learned = rules.lookup("value_map", f"{issue.field}|{clean.vkey(current)}") if current is not None else None
        if learned is None:
            learned = rules.lookup("record_value", f"{record.key}|{issue.field}")
        if learned is not None:
            record.set(issue.field, learned, "rule you taught it earlier")
            notes.append(f"{f.label}: applied learned rule")
            continue
        if issue.code == "missing" and f.default is not None:
            record.set(issue.field, f.default, f"schema default for missing {f.label.lower()}")
            notes.append(f"{f.label}: filled schema default '{f.default}'")
        elif f.type == "email" and issue.code == "email_format" and current:
            fixed = str(current).replace(" ", "").replace(",", ".").replace("@@", "@").rstrip(".")
            fixed = re.sub(r"\.{2,}", ".", fixed)
            if fixed != current and clean.EMAIL_STRICT.match(fixed):
                record.set(issue.field, fixed, "syntax repair (stray comma/space/dot)")
                notes.append(f"{f.label}: '{current}' -> '{fixed}'")
    return notes


def email_pattern_share(records: list[Record], schema: TargetSchema) -> float:
    """Share of work emails that follow first.last@<company domain>."""
    total = hits = 0
    for r in records:
        e, fn, ln = r.data.get("email"), r.data.get("first_name"), r.data.get("last_name")
        if e and fn and ln and e.split("@")[-1] in schema.company_domains:
            total += 1
            hits += e.split("@")[0].rstrip("0123456789") == f"{fn}.{ln}".lower().replace(" ", "")
    return hits / total if total else 0.0


def suggest(record: Record, issues: list[Issue], schema: TargetSchema, others: list[Record]) -> dict[str, dict]:
    """field -> {value, why, confidence} for fixes a human can approve in one click."""
    out: dict[str, dict] = {}
    domain = schema.company_domains[0] if schema.company_domains else None
    taken = {o.data.get("email") for o in others if o.key != record.key}
    for issue in issues:
        current = record.data.get(issue.field)
        if issue.field == "email" and domain:
            local, _, dom = (str(current or "").partition("@"))
            if current and dom and domain.startswith(dom.split(".")[0]) and dom != domain:
                out["email"] = {"value": f"{local}@{domain}", "confidence": 0.8,
                                "why": f"Domain '{dom}' looks like a truncated '{domain}'"}
                continue
            fn, ln = record.data.get("first_name"), record.data.get("last_name")
            share = email_pattern_share(others, schema)
            if fn and ln and share >= 0.8:
                candidate = f"{fn}.{ln}@{domain}".lower().replace(" ", "")
                if candidate not in taken:
                    why = f"{round(share * 100)}% of work emails follow first.last@{domain}"
                    if current and any(dom.startswith(p) for p in FREE_MAIL):
                        why += f"; '{current}' is a personal address - keep it as personal email"
                        if not record.data.get("personal_email"):
                            out["personal_email"] = {"value": current, "confidence": 0.9,
                                                     "why": "Move the personal address to the personal email field"}
                    out["email"] = {"value": candidate, "confidence": 0.7, "why": why}
        elif issue.field == "department" and issue.code == "missing":
            mgr = record.data.get("manager_email")
            boss = next((o for o in others if mgr and o.data.get("email") == mgr), None)
            if boss and boss.data.get("department"):
                out["department"] = {"value": boss.data["department"], "confidence": 0.6,
                                     "why": f"Reports to {boss.name}, who is in {boss.data['department']}"}
    return out
