# Write-up: scoping the agent's autonomy

## Approach

I built the agent as a **resumable state machine with an LLM at the judgment points**, not as a free-running
tool-calling loop. In a customer-facing migration, predictability and an explainable audit trail matter more
than open-ended planning. The run is: read → map columns → clean → combine and de-duplicate → validate → push.

Each step does the confident work itself and turns only the uncertain residue into **escalation cards**. A card
carries the question, why the agent didn't decide, the evidence (samples, candidate scores, side-by-side records)
and a suggested answer, so a non-technical consultant can resolve it in one click.

The target schema is plain YAML (aliases, synonyms, validation rules), so a new client or entity is a
configuration change, not a code change.

## Where I drew the line, and why there

**Rule: the agent acts alone when a deterministic check can prove the answer. The open-weight LLM (gpt-oss-120b on
Groq, falling back to Llama 3.3 70B on OpenRouter) may propose, or add confidence to something the data already
supports, but its opinion alone never changes a customer's data.** A useful side effect: if every AI provider is
down, the agent keeps working safely, and the UI and run record say "No AI involved".

- **Columns** are scored on three independent signals: header similarity to known aliases, how well the
  *values* fit the field (a column called "Email" full of Gmail addresses isn't the work email), and the LLM.
  The agent maps a column alone only if the combined score is high **and** header + values alone separate the
  winner from the runner-up. So the AI can confirm but never break a tie. A test checks this: a 99%-confident
  fake LLM still can't auto-map the genuinely ambiguous `Contact Number` (own mobile vs emergency contact).
- **Column-level uncertainty pauses the run; record-level uncertainty never does.** A wrong mapping or date
  format silently corrupts every row, so it's worth stopping for. One odd record shouldn't hold up 59 good ones:
  they're pushed, and the odd one follows as soon as it's resolved.
- **Some cases are formatting and some are business decisions.** `Engg` → Engineering, `acmecorp,com` → `.com`,
  `N/A` → empty, and `45.8 LPA` → 4,580,000 can only mean one thing, so the agent fixes them. `Special Projects`
  → which department? That one is a business decision, so the agent asks, with the AI's guess shown only as a
  suggestion.
- **Cost of being wrong.** Merging two real people, or picking the wrong source of truth when two *valid* values
  disagree, is hard to undo downstream. So is inventing a missing required field. These are escalated even when
  the agent has a good guess, and the guess becomes the one-click suggestion.
- **"Fails twice".** A validation failure gets exactly one safe repair (syntax only). If it still fails, the
  agent asks. Transient push errors are retried; rejections (409/422) are escalated, because retrying won't
  change a decision the target has made.

The result on the sample client: roughly 500 fixes and decisions made alone versus 12 questions (2
column-level + 10 record-level across 60 employees), and every question maps to a real ambiguity. The line was
deliberately **not** drawn at "ask on anything the LLM is unsure of". LLM confidence is poorly calibrated, and
that line would move with the model.

## The delta beyond what the AI does

1. **A deterministic layer around the model:** value profiling, schema validation, cross-file date evidence, and
   a rule that when sources conflict and only one value is valid, the valid one wins.
2. **Learning from corrections.** Every resolution can become a rule (mapping, date format, value fix, source of
   truth, duplicate decision, skip). The client's re-export a month later produced **1 question instead of 12**.
3. **Incremental sync:** a diff against the target, so only changed fields are sent as updates, unchanged records
   are skipped, and records are pushed in dependency order (managers first). **Rollback** reverses a run.

## What I'd build next

- **Dry-run / preview mode:** show the full diff against the target before the first push, for sign-off.
- **Rule governance:** scope rules per client and entity, add expiry and review, and show "rule applied N times"
  inline on records.
- **Calibrated thresholds:** log every human override of an agent suggestion and tune `policy.py` per client from
  that data. Add an active-learning loop that surfaces the rules most often overridden.
- **Bulk resolution:** group similar record-level cards (e.g. "8 new joiners without a work email → generate
  all").
- **More entities and real connectors:** departments, managers and payroll as separate schemas with referential
  checks; auth, rate limits and idempotency keys against a real HRMS API.
- **Multi-user review:** assignment, a second-approver option for irreversible actions, and notifications when the
  run pauses.
