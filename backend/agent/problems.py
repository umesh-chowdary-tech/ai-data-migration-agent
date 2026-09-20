"""Problem types a "whenever this happens" rule can target.

A problem rule (kind "issue_policy") generalises a decision to a whole class of cases: "whenever a mobile number
isn't a valid mobile number, leave it empty instead of asking". It keys on the PROBLEM, not the value, so it applies
to every employee equally - unlike an exact-value rule, which on a free-form field either never fires again or
copies one person's value onto another.

Deliberately narrow: the only action is "leave it empty", only optional fields qualify (a required field can't be
emptied, so it keeps escalating), and category fields are excluded (their fixes are exact-value rules).
"""
from __future__ import annotations

from typing import Any

from ..schema import Field, TargetSchema
from . import clean
from . import validate as val

# code -> (field types it applies to, plain-English description of the problem)
CATALOG: dict[str, tuple[tuple[str, ...], str]] = {
    "bad_phone": (("phone",), "isn't a valid mobile number"),
    "bad_date": (("date",), "isn't a real date"),
    "date_range": (("date",), "is outside the allowed years"),
    "bad_number": (("money",), "isn't a readable amount"),
    "too_small": (("money",), "is below the minimum"),
    "email_format": (("email",), "isn't a valid email address"),
    "email_domain": (("email",), "isn't on the company domain"),
    "suspicious_value": (("text", "name"), "looks like a spreadsheet formula"),
    "manager_not_found": (("email",), "doesn't exist in the new system"),
}
ACTION = "clear"


def eligible(f: Field, code: str | None) -> bool:
    if not code or code not in CATALOG or f.required or f.type == "enum":
        return False
    if code == "manager_not_found" and f.name != "manager_email":
        return False
    if code == "email_domain" and f.email_kind != "work":
        return False
    return f.type in CATALOG[code][0]


def for_field(f: Field) -> list[dict]:
    return [{"code": c, "label": CATALOG[c][1]} for c in CATALOG if eligible(f, c)]


def describe(f: Field, code: str) -> str:
    return f"Whenever {f.label.lower()} {CATALOG[code][1]}: leave it empty instead of asking"


def infer(f: Field, raw: Any, schema: TargetSchema) -> str | None:
    """Which problem does this raw value have? (used to generalise an old exact-value rule)"""
    try:
        cleaned, _ = clean.clean_value(f, raw, schema, "DMY" if f.type == "date" else None)
    except clean.CleanError as e:
        return e.code if e.code in CATALOG else None
    issues = val.field_issues(f.name, cleaned, schema)
    return issues[0].code if issues and issues[0].code in CATALOG else None
