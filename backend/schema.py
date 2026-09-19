"""Loads the target schema YAML into a small typed structure used by every agent step."""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

import yaml

from . import config


def norm(text: str) -> str:
    """Normalise a header / enum token: split camelCase, lower-case, separators to spaces, collapse whitespace."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text).strip()).lower()  # FirstName -> first name
    text = re.sub(r"[_\-./]+", " ", text)
    text = re.sub(r"[^a-z0-9&+ ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Field:
    name: str
    label: str
    type: str
    required: bool = False
    unique: bool = False
    pattern: str | None = None
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    email_kind: str | None = None
    date_range: tuple[int, int] | None = None
    minimum: float | None = None
    default: str | None = None
    splits_into: list[str] = field(default_factory=list)
    virtual: bool = False

    @functools.cached_property
    def enum_lookup(self) -> dict[str, str]:
        """normalised spelling -> canonical value (canonical values + synonyms)."""
        table = {norm(v): v for v in self.values}
        for canonical, words in self.synonyms.items():
            for w in words:
                table[norm(w)] = canonical
        return table


@dataclass
class TargetSchema:
    entity: str
    primary_key: str
    company_domains: list[str]
    id_prefix: str
    id_digits: int
    fields: dict[str, Field]
    virtual_fields: dict[str, Field]

    @property
    def all_mappable(self) -> dict[str, Field]:
        return {**self.fields, **self.virtual_fields}

    def describe_for_llm(self) -> list[dict]:
        out = []
        for f in self.all_mappable.values():
            item = {"field": f.name, "type": f.type, "label": f.label}
            if f.description:
                item["description"] = f.description
            if f.values:
                item["allowed_values"] = f.values
            if f.splits_into:
                item["note"] = f"one source value that is split into {', '.join(f.splits_into)}"
            out.append(item)
        return out


def _field(name: str, spec: dict, virtual: bool = False) -> Field:
    rng = spec.get("date_range")
    return Field(
        name=name, label=spec.get("label", name), type=spec["type"], required=bool(spec.get("required")),
        unique=bool(spec.get("unique")), pattern=spec.get("pattern"), description=spec.get("description", ""),
        aliases=[norm(a) for a in spec.get("aliases", [])] + [norm(name)],
        values=list(spec.get("values", [])), synonyms=dict(spec.get("synonyms", {})),
        email_kind=spec.get("email_kind"), date_range=tuple(rng) if rng else None,
        minimum=spec.get("min"), default=spec.get("default"), splits_into=list(spec.get("splits_into", [])),
        virtual=virtual,
    )


@functools.lru_cache(maxsize=1)
def load() -> TargetSchema:
    raw = yaml.safe_load(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    idf = raw.get("id_format", {})
    return TargetSchema(
        entity=raw["entity"], primary_key=raw["primary_key"],
        company_domains=[d.lower() for d in raw.get("company_domains", [])],
        id_prefix=idf.get("prefix", ""), id_digits=int(idf.get("digits", 0)),
        fields={k: _field(k, v) for k, v in raw["fields"].items()},
        virtual_fields={k: _field(k, v, virtual=True) for k, v in (raw.get("virtual_fields") or {}).items()},
    )
