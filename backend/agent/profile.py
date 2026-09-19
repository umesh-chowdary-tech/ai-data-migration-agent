"""Column profiling: what do the values in each source column actually look like?

Value evidence is what stops the agent from trusting a header blindly (e.g. a column called
"Email" full of gmail addresses is not the work email)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..schema import Field, TargetSchema
from . import clean


@dataclass
class ColumnProfile:
    file: str
    name: str
    values: list[Any]
    non_empty: int = 0
    placeholders: int = 0
    samples: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    years: list[int] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"column": self.name, "non_empty": self.non_empty, "placeholders": self.placeholders,
                "samples": self.samples, "features": {k: round(v, 2) for k, v in self.features.items()}}


def _frac(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def profile_column(file: str, name: str, series: pd.Series, schema: TargetSchema) -> ColumnProfile:
    values = list(series)
    real = [v for v in values if not clean.is_placeholder(v)]
    prof = ColumnProfile(file=file, name=name, values=values, non_empty=len(real),
                         placeholders=sum(1 for v in values if not clean.is_blank(v) and clean.is_placeholder(v)))
    seen: list[str] = []
    for v in real:
        s = v.date().isoformat() if isinstance(v, pd.Timestamp) else clean.text(v)
        if s not in seen:
            seen.append(s)
        if len(seen) >= 6:
            break
    prof.samples = seen
    sample = real[:300]
    if not sample:
        return prof

    texts = [clean.text(v) for v in sample]
    emails = [bool(clean.EMAIL_LOOSE.match(t)) for t in texts]
    prof.domains = [t.split("@", 1)[1].lower() for t, e in zip(texts, emails) if e]
    company = [any(d == c or d.startswith(c.split(".")[0]) for c in schema.company_domains) for d in prof.domains]

    date_flags, years = [], []
    for v in sample:
        ok, year = clean.looks_like_date(v)
        date_flags.append(ok)
        if year:
            years.append(year)
    prof.years = years

    def is_phone(t: str) -> bool:
        return bool(re.fullmatch(r"[\d\s+\-().]{7,}", t)) and 10 <= len(re.sub(r"\D", "", t)) <= 13

    def is_money(v: Any) -> bool:
        if isinstance(v, (pd.Timestamp,)) or clean.looks_like_date(v)[0]:
            return False
        try:
            clean.parse_money(v)
            return True
        except (ValueError, TypeError):
            return False

    name_tokens = [t.replace(",", " ").split() for t in texts]
    alpha = [bool(re.fullmatch(r"[A-Za-z .,'\-]+", t)) for t in texts]
    prof.features = {
        "email": _frac(emails),
        "company_email": _frac(company) if company else 0.0,
        "phone": _frac([is_phone(t) for t in texts]),
        "date": _frac(date_flags),
        "number": _frac([is_money(v) for v in sample]),
        "id_like": _frac([bool(re.fullmatch(r"[A-Za-z]{0,4}[\s\-_#]?\d{1,6}", t)) for t in texts]),
        "single_name": _frac([a and len(tok) == 1 for a, tok in zip(alpha, name_tokens)]),
        "full_name": _frac([a and (len(tok) >= 2 or "," in t) and len(tok) <= 5
                            for a, tok, t in zip(alpha, name_tokens, texts)]),
        "text": _frac([bool(re.fullmatch(r"[A-Za-z][A-Za-z &/,.'()\-]{2,}", t)) and "@" not in t for t in texts]),
    }
    enum_fields = [f for f in schema.fields.values() if f.type == "enum"]
    hits = {f.name: [clean.enum_match(f, t)[0] is not None for t in texts] for f in enum_fields}
    for name, flags in hits.items():
        prof.features[f"enum:{name}"] = _frac(flags)
    # Values that are known category words ("Intern", "Full-Time", "Active") are not people's names.
    prof.features["enum_any"] = _frac([any(h[k] for h in hits.values()) for k in range(len(texts))])
    return prof


def value_score(prof: ColumnProfile, f: Field) -> tuple[float, str]:
    """How well do this column's values fit target field f? (score 0..1, plain-English evidence)."""
    ft = prof.features
    if not ft:
        return 0.0, "column is empty"
    pct = lambda x: f"{round(x * 100)}%"  # noqa: E731
    if f.type == "id":
        return ft["id_like"], f"{pct(ft['id_like'])} of values look like employee codes"
    if f.type == "name":
        s = ft["single_name"] * (1 - ft.get("enum_any", 0.0))
        return s, f"{pct(s)} of values look like a single name"
    if f.type == "full_name":
        s = ft["full_name"] * (1 - ft.get("enum_any", 0.0))
        return s, f"{pct(s)} of values look like full names"
    if f.type == "email":
        if f.email_kind == "work":
            s = ft["email"] * ft["company_email"]
            return s, f"{pct(ft['email'])} are emails, {pct(ft['company_email'])} of those on the company domain"
        if f.email_kind == "personal":
            s = ft["email"] * (1 - ft["company_email"])
            return s, f"{pct(ft['email'])} are emails, {pct(1 - ft['company_email'])} of those on personal domains"
        return ft["email"], f"{pct(ft['email'])} of values are emails"
    if f.type == "phone":
        return ft["phone"], f"{pct(ft['phone'])} of values look like phone numbers"
    if f.type == "date":
        if not f.date_range or not prof.years:
            return ft["date"], f"{pct(ft['date'])} of values are dates"
        lo, hi = f.date_range
        inside = _frac([lo <= y <= hi for y in prof.years])
        return ft["date"] * inside, (f"{pct(ft['date'])} are dates, {pct(inside)} within {lo}-{hi} "
                                     f"(years {min(prof.years)}-{max(prof.years)})")
    if f.type == "money":
        return ft["number"], f"{pct(ft['number'])} of values are amounts"
    if f.type == "enum":
        s = ft.get(f"enum:{f.name}", 0.0)
        return s, f"{pct(s)} of values are known {f.label.lower()} values"
    if f.type == "text":
        return ft["text"] * 0.8, f"{pct(ft['text'])} of values are free text"
    return 0.0, ""
