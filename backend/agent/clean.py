"""Deterministic value parsers / cleaners. No AI here on purpose: every fix must be explainable
and reproducible, and anything a parser can't settle is raised as a CleanError for escalation."""
from __future__ import annotations

import datetime as dt
import difflib
import math
import re
from typing import Any

from ..schema import Field, TargetSchema, norm
from . import policy

PLACEHOLDERS = {"", "na", "n/a", "n.a.", "none", "null", "nil", "-", "--", "---", "?", "??", "???", "unknown",
                "tbd", "not available", "nan", "#n/a"}

EMAIL_LOOSE = re.compile(r"^[^@\s]+@[^@\s]+$")
EMAIL_STRICT = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
NUMERIC_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$")
ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?$")
TEXT_DATE_FORMATS = ["%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%b %d %Y", "%d-%B-%Y", "%d %B %Y", "%B %d, %Y",
                     "%d-%b-%y"]
ID_RE = re.compile(r"^\s*([A-Za-z]*)[\s\-_#]*(\d+)\s*$")


class CleanError(Exception):
    def __init__(self, code: str, message: str, suggestion: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion


# --- basic predicates ----------------------------------------------------------------------------

def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        import pandas as pd
        if value is pd.NaT or (not isinstance(value, (str, int, float, dt.date)) and pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and value.strip() == ""


def is_placeholder(value: Any) -> bool:
    return is_blank(value) or (isinstance(value, str) and value.strip().lower() in PLACEHOLDERS)


def text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def vkey(value: Any) -> str:
    """Key used by learned value rules."""
    return text(value).lower()


# --- dates ---------------------------------------------------------------------------------------

def as_date(value: Any) -> dt.date | None:
    """Parse anything that is unambiguous on its own (date objects, ISO, month names)."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = text(value)
    m = ISO_DATE.match(s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    for fmt in TEXT_DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_date(value: Any, numeric_format: str | None) -> dt.date:
    d = as_date(value)
    if d:
        return d
    s = text(value)
    m = NUMERIC_DATE.match(s)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if numeric_format is None:
            if a > 12 and b <= 12:
                numeric_format = "DMY"
            elif b > 12 and a <= 12:
                numeric_format = "MDY"
            else:
                raise CleanError("ambiguous_date", f"'{s}' could be day-first or month-first")
        day, month = (a, b) if numeric_format == "DMY" else (b, a)
        try:
            return dt.date(year, month, day)
        except ValueError:
            raise CleanError("bad_date", f"'{s}' is not a real date when read as {numeric_format}")
    raise CleanError("bad_date", f"'{s}' is not a recognisable date")


def looks_like_date(value: Any) -> tuple[bool, int | None]:
    """(parseable under some interpretation, year)."""
    d = as_date(value)
    if d:
        return True, d.year
    m = NUMERIC_DATE.match(text(value))
    if m:
        return True, int(m.group(3))
    return False, None


def detect_date_format(values: list[Any]) -> tuple[str | None, dict]:
    """Infer DMY vs MDY for a whole column. Returns (format | 'ambiguous' | 'conflict' | None, stats)."""
    numeric = [NUMERIC_DATE.match(text(v)) for v in values if not is_placeholder(v)]
    numeric = [m for m in numeric if m]
    stats = {"numeric_values": len(numeric), "first_gt_12": 0, "second_gt_12": 0}
    if not numeric:
        return None, stats
    for m in numeric:
        a, b = int(m.group(1)), int(m.group(2))
        stats["first_gt_12"] += a > 12
        stats["second_gt_12"] += b > 12
    if stats["first_gt_12"] and stats["second_gt_12"]:
        return "conflict", stats
    if stats["first_gt_12"]:
        return "DMY", stats
    if stats["second_gt_12"]:
        return "MDY", stats
    return "ambiguous", stats


# --- individual cleaners -------------------------------------------------------------------------

def smart_title(name: str) -> str:
    def fix(token: str) -> str:
        if not token:
            return token
        if token.islower() or token.isupper() or not token[0].isupper():
            return "-".join("'".join(p[:1].upper() + p[1:].lower() for p in part.split("'"))
                            for part in token.split("-"))
        return token
    return " ".join(fix(t) for t in text(name).split(" "))


def clean_id(value: Any, schema: TargetSchema) -> str:
    s = text(value)
    m = ID_RE.match(s)
    if not m:
        raise CleanError("bad_id", f"'{s}' is not a recognisable employee ID")
    prefix, digits = m.group(1), m.group(2)
    if prefix and prefix.upper() != schema.id_prefix.upper():
        raise CleanError("bad_id", f"'{s}' has an unexpected prefix '{prefix}'")
    number = int(digits)
    if schema.id_digits and len(str(number)) > schema.id_digits:
        raise CleanError("bad_id", f"'{s}' has too many digits")
    return f"{schema.id_prefix}{number:0{schema.id_digits}d}"


def clean_phone(value: Any) -> str:
    s = text(value)
    if not re.fullmatch(r"[\d\s+\-().]+", s):
        raise CleanError("bad_phone", f"'{s}' contains characters that are not part of a phone number")
    digits = re.sub(r"\D", "", s)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    raise CleanError("bad_phone", f"'{s}' is not a valid 10-digit Indian mobile number ({len(digits)} digits)")


def parse_money(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            raise ValueError
        return float(value)
    s = text(value).lower()
    if re.fullmatch(r"\+?\d[\d\s-]{9,}", s) and len(re.sub(r"\D", "", s)) >= 10:
        raise ValueError("looks like a phone number")
    s = re.sub(r"(₹|rs\.?|inr|/-|p\.?a\.?|per annum)", "", s).replace(",", "").strip()
    mult = 1.0
    unit = re.search(r"(lpa|lakhs?|lacs?|l|cr|crores?|k)$", s)
    if unit:
        u = unit.group(1)
        mult = 1e5 if u.startswith("l") else 1e7 if u.startswith("cr") else 1e3
        s = s[: unit.start()].strip()
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError
    return float(s) * mult


def clean_money(value: Any) -> int:
    try:
        return int(round(parse_money(value)))
    except ValueError:
        raise CleanError("bad_number", f"'{text(value)}' is not a recognisable amount")


def enum_match(f: Field, value: Any) -> tuple[str | None, str | None]:
    """(canonical value, how) - how is 'exact' | 'synonym' | 'typo'."""
    key = norm(value)
    if key in {norm(v) for v in f.values}:
        return f.enum_lookup[key], "exact"
    if key in f.enum_lookup:
        return f.enum_lookup[key], "synonym"
    close = difflib.get_close_matches(key, list(f.enum_lookup), n=1, cutoff=policy.ENUM_TYPO_CUTOFF)
    if close:
        return f.enum_lookup[close[0]], "typo"
    return None, None


FORMULA_START = ("=", "+", "@")


def check_safe_text(value: Any) -> None:
    """Refuse free-text values that would execute as a formula when someone opens an export in Excel."""
    s = str(value).strip()
    if s.startswith(FORMULA_START) or "\t" in s or "\r" in s:
        raise CleanError("suspicious_value", f"'{s[:40]}' looks like a spreadsheet formula - it could run when "
                                             "someone opens an export in Excel, so I won't migrate it unchecked")


def split_full_name(value: Any) -> tuple[str | None, str | None, str]:
    check_safe_text(value)
    s = text(value)
    if "," in s:
        last, first = [p.strip() for p in s.split(",", 1)]
        return smart_title(first) or None, smart_title(last) or None, "comma"
    parts = s.split(" ")
    if len(parts) == 1:
        return smart_title(parts[0]), None, "single"
    return smart_title(" ".join(parts[:-1])), smart_title(parts[-1]), "space"


def clean_value(f: Field, value: Any, schema: TargetSchema, date_format: str | None = None) -> tuple[Any, str | None]:
    """Returns (clean value, category of the change or None). Raises CleanError when unsure."""
    if f.type == "id":
        out = clean_id(value, schema)
        return out, ("id_format" if out != text(value) else None)
    if f.type == "name":
        check_safe_text(value)
        out = smart_title(value)
        return out, ("casing" if out != text(value) else "whitespace" if out != str(value) else None)
    if f.type == "text":
        check_safe_text(value)
        out = text(value)
        return out, ("whitespace" if out != value else None)
    if f.type == "email":
        raw = str(value)
        out = re.sub(r"^mailto:", "", raw.strip().lower())
        return out, ("email_format" if out != raw else None)
    if f.type == "phone":
        out = clean_phone(value)
        return out, ("phone_format" if out != text(value) else None)
    if f.type == "date":
        out = parse_date(value, date_format).isoformat()
        return out, ("date_format" if out != text(value) else None)
    if f.type == "money":
        out = clean_money(value)
        return out, ("amount_format" if str(out) != text(value) else None)
    if f.type == "enum":
        out, how = enum_match(f, value)
        if out is None:
            raise CleanError("unknown_value", f"'{text(value)}' is not one of: {', '.join(f.values)}")
        return out, (None if how == "exact" and out == text(value) else f"enum_{how}")
    return text(value), None


CATEGORY_LABELS = {
    "id_format": "employee IDs normalised (e.g. emp-0012 -> EMP0012)",
    "casing": "names re-cased / whitespace trimmed",
    "whitespace": "stray whitespace trimmed",
    "email_format": "emails lower-cased / trimmed",
    "phone_format": "phone numbers standardised to +91XXXXXXXXXX",
    "date_format": "dates converted to YYYY-MM-DD",
    "amount_format": "salary amounts parsed (12,50,000 / 18 LPA / ₹ ...)",
    "enum_synonym": "known synonyms mapped (e.g. Engg -> Engineering, BLR -> Bengaluru)",
    "enum_typo": "obvious typos fixed in category values",
    "enum_exact": "category values re-cased",
    "placeholder": "placeholder values (N/A, -, ?) treated as empty",
    "name_split": "full names split into first / last name",
    "rule": "values fixed using a rule you taught it earlier",
}
