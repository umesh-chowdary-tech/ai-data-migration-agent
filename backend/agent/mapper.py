"""Source column -> target field mapping.

Three independent pieces of evidence per (column, field) pair:
  1. header similarity to the field's known aliases          (deterministic)
  2. how well the column's *values* fit the field's type       (deterministic)
  3. what the LLM proposes after seeing headers + samples      (open-weight model, optional)
The combined score + the gap to the runner-up decide: map it / ask a human / leave it out.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field

from ..schema import Field, TargetSchema, norm
from . import policy
from .llm import LLM, AIResult, dig
from .profile import ColumnProfile, value_score


@dataclass
class Candidate:
    field: str
    label: str
    score: float
    name_score: float
    value_score: float
    llm_score: float | None
    evidence: list[str] = field(default_factory=list)

    @property
    def deterministic(self) -> float:
        """Score from header + values only - the evidence that must separate candidates on its own."""
        return policy.W_NAME_NO_LLM * self.name_score + policy.W_VALUE_NO_LLM * self.value_score

    def as_dict(self) -> dict:
        return {"field": self.field, "label": self.label, "score": round(self.score, 3),
                "deterministic": round(self.deterministic, 3),
                "name_score": round(self.name_score, 3), "value_score": round(self.value_score, 3),
                "llm_score": None if self.llm_score is None else round(self.llm_score, 3),
                "evidence": self.evidence}


@dataclass
class Decision:
    column: str
    target: str | None
    confidence: float
    method: str          # auto | escalate | unmapped | rule
    reason: str
    candidates: list[Candidate]
    samples: list[str]


def name_score(column: str, f: Field) -> tuple[float, str]:
    c = norm(column)
    best, best_alias = 0.0, ""
    for alias in f.aliases:
        if c == alias:
            return 1.0, f"header '{column}' is a known name for {f.label}"
        ratio = difflib.SequenceMatcher(None, c, alias).ratio()
        ct, at = set(c.split()), set(alias.split())
        jaccard = len(ct & at) / len(ct | at) if ct | at else 0.0
        s = max(ratio, jaccard) * 0.9
        if s > best:
            best, best_alias = s, alias
    return best, f"header '{column}' resembles '{best_alias}'" if best_alias else ""


def _conflicts(field_name: str, claimed: set[str], schema: TargetSchema) -> bool:
    f = schema.all_mappable[field_name]
    wanted = set(f.splits_into) | {field_name} if f.virtual else {field_name}
    for c in claimed:
        cf = schema.all_mappable[c]
        taken = set(cf.splits_into) | {c} if cf.virtual else {c}
        if wanted & taken:
            return True
    return False


def ask_llm(llm: LLM, file: str, profiles: list[ColumnProfile], schema: TargetSchema) -> tuple[dict[str, dict] | None, AIResult]:
    system =("You map columns from a client's legacy HR export to a target HR schema. For each source column pick "
              "the single best target field, or null if none fits. Judge by header AND sample values. Give a "
              "confidence 0-1 that reflects real ambiguity (use <0.7 when two fields are plausible).")
    user = json.dumps({
        "file": file,
        "target_fields": schema.describe_for_llm(),
        "source_columns": [{"column": p.name, "samples": p.samples[:6]} for p in profiles],
        "output_format": {"mappings": [{"column": "<source column>", "target": "<field name or null>",
                                        "confidence": 0.0, "reason": "<short reason>"}]},
    }, ensure_ascii=False)
    res = llm.complete_json(system, user)
    mappings = dig(res.data, "mappings")
    if mappings is None:
        if res.data is not None:
            res.failures.append({"provider": res.provider, "model": res.model, "kind": "bad_output",
                                 "error": "reply had no 'mappings' list"})
            res.data = None
        return None, res
    result = {}
    for m in mappings:
        if isinstance(m, dict) and m.get("column"):
            target = m.get("target")
            result[str(m["column"])] = {
                "target": target if target in schema.all_mappable else None,
                "confidence": max(0.0, min(1.0, float(m.get("confidence") or 0))),
                "reason": str(m.get("reason") or "")[:240],
            }
    return result, res


def score_column(prof: ColumnProfile, schema: TargetSchema, llm_view: dict | None) -> list[Candidate]:
    cands = []
    for f in schema.all_mappable.values():
        ns, n_ev = name_score(prof.name, f)
        vs, v_ev = value_score(prof, f)
        evidence = [e for e in (n_ev, v_ev) if e]
        if llm_view is not None:
            ls = llm_view["confidence"] if llm_view.get("target") == f.name else 0.0
            score = policy.W_NAME * ns + policy.W_VALUE * vs + policy.W_LLM * ls
            if llm_view.get("target") == f.name:
                evidence.append(f"AI proposes this ({round(ls * 100)}%): {llm_view.get('reason', '')}".strip())
        else:
            ls = None
            score = policy.W_NAME_NO_LLM * ns + policy.W_VALUE_NO_LLM * vs
        cands.append(Candidate(f.name, f.label, score, ns, vs, ls, evidence))
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


def map_file(file: str, profiles: list[ColumnProfile], schema: TargetSchema, llm_views: dict | None,
             column_rules: dict[str, str | None]) -> list[Decision]:
    scored = {p.name: score_column(p, schema, (llm_views or {}).get(p.name) if llm_views is not None else None)
              for p in profiles}
    samples = {p.name: p.samples for p in profiles}
    order = sorted(profiles, key=lambda p: (norm(p.name) not in column_rules, -scored[p.name][0].score))
    claimed: set[str] = set()
    decisions: list[Decision] = []
    for prof in order:
        col = prof.name
        cands = scored[col]
        rule_key = norm(col)
        if rule_key in column_rules:
            target = column_rules[rule_key]
            if target is None or not _conflicts(target, claimed, schema):
                if target:
                    claimed.add(target)
                decisions.append(Decision(col, target, 1.0, "rule",
                                          "Applied your earlier decision for this column header", cands[:4],
                                          samples[col]))
                continue
        avail = [c for c in cands if not _conflicts(c.field, claimed, schema)]
        top = avail[0] if avail else None
        second = avail[1].score if len(avail) > 1 else 0.0
        if prof.non_empty == 0 or top is None or top.score < policy.MAP_IGNORE_BELOW:
            decisions.append(Decision(col, None, top.score if top else 0.0, "unmapped",
                                      "No target field fits this column (best match "
                                      f"{top.label if top else '-'} at {round((top.score if top else 0) * 100)}%) "
                                      "- it will not be migrated", avail[:4], samples[col]))
            continue
        gap = top.score - second
        # The AI may confirm a mapping but never break a tie: header + values alone must separate the top two.
        rival = max((c for c in avail[1:]), key=lambda c: c.deterministic, default=None)
        det_gap = top.deterministic - (rival.deterministic if rival else 0.0)
        if top.score >= policy.MAP_AUTO_MIN and gap >= policy.MAP_MIN_GAP and det_gap >= policy.MAP_MIN_GAP:
            claimed.add(top.field)
            decisions.append(Decision(col, top.field, top.score, "auto", "; ".join(top.evidence), avail[:4],
                                      samples[col]))
        else:
            if gap < policy.MAP_MIN_GAP and len(avail) > 1:
                why = (f"'{avail[0].label}' ({round(top.score * 100)}%) and '{avail[1].label}' "
                       f"({round(second * 100)}%) fit almost equally well")
            elif det_gap < policy.MAP_MIN_GAP and rival is not None:
                why = (f"the header and values fit '{top.label}' and '{rival.label}' almost equally well "
                       f"({round(top.deterministic * 100)}% vs {round(rival.deterministic * 100)}%) - the AI leans towards "
                       f"'{top.label}', but an AI opinion alone isn't enough to decide")
            else:
                why = f"best match '{top.label}' is only {round(top.score * 100)}% confident"
            decisions.append(Decision(col, None, top.score, "escalate", why, avail[:4], samples[col]))
    order_index = {p.name: i for i, p in enumerate(profiles)}
    decisions.sort(key=lambda d: order_index[d.column])
    return decisions
