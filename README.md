# Migration Agent: client HR data → new platform, with a human in the loop

An AI agent that takes a client's messy legacy exports (CSV + Excel, different column names, mixed date formats,
duplicates, missing fields), works out how they map to the target schema, cleans, reconciles and validates the
data, and pushes it to the new platform's API **on its own**. It stops to ask an implementation consultant only
when the data genuinely can't prove the answer. A web UI lets the consultant watch it work live, resolve its
questions in one click, and see an audit trail of everything that happened.

> One-page write-up of the approach and the escalation boundary: **[WRITEUP.md](WRITEUP.md)**

---

## Quick start

Requirements: Python 3.11+, Node 18+ (only to build the UI).

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env            # macOS/Linux: cp .env.example .env  -> add a free Groq key (optional)

cd frontend && npm install && npm run build && cd ..
python -m uvicorn backend.main:app --port 8000
```

Open **http://localhost:8000** → *Start a migration* → *Sample client export (first extract)*.

- **No API key, or every AI provider down?** Everything still works in *rules-only mode*. The agent then uses
  header and value evidence only, and makes no AI suggestions. The UI says clearly that no AI is involved.
- **UI development:** run `npm run dev` in `frontend/` (http://localhost:5173, proxies `/api` to :8000).
- **Tests:** `python -m pytest -q`. These run the whole pipeline headless: v1 → resolve every escalation →
  v2 delta → rollback.
- **Regenerate the sample data:** `python scripts/generate_data.py`

### Open-source models, with automatic fallback

The runtime agent talks to open-weight models through OpenAI-compatible endpoints. `LLM_PROVIDERS` in `.env` is
an **ordered fallback chain**. Every AI call goes to the first healthy provider, and if that one fails the next
one answers.

| Provider | Default model (`*_MODEL` to change) | Notes |
|---|---|---|
| `groq` | `openai/gpt-oss-120b` | free tier, fast; key from console.groq.com |
| `openrouter` | `meta-llama/llama-3.3-70b-instruct` | many open models behind one key |
| `ollama` | `qwen2.5:7b` | fully local and offline |
| `none` | – | switch AI off |

**Failure handling:**

- **How failures are classified:** each failure is put into plain English. The categories are key rejected,
  model retired, no credits, rate limit, timeout, network, and "answered, but not valid JSON".
- **How long a failed provider is skipped:**
  - a permanently failed provider (bad key, retired model) is skipped for 15 min;
  - a temporarily failed one (429 / 5xx / timeout) is skipped for 60 s;
  - after one bad answer the agent simply falls back for that call.
- **Where you see it:**
  - **Header badge.** It shows which AI is answering right now (violet = primary, amber = backup, red = none).
    Click it for per-provider status and a **Re-check** button. Re-check also re-reads `.env`, so a fixed key
    takes effect without a restart.
  - **Banner.** If every provider fails, it states **"No AI is involved right now"** and why. The agent keeps
    working on deterministic rules only.
  - **Per run.** Each run records which model answered each step, or **"No AI involved in this run"**. This is
    shown on the run header and in the activity feed, so the audit trail says whether AI touched the migration.

`tests/test_llm_fallback.py` covers every path with a fake HTTP layer (no network).

---

## What happens in a run

```
 source files ──► 1 Read ──► 2 Understand columns ──► 3 Clean ──► 4 Combine & de-dup ──► 5 Validate ──► 6 Push
                              │  header aliases          │ dates,     │ merge by ID,          │ schema     │ delta vs target,
                              │  + value profiling       │ phones,    │ source conflicts,     │ checks,    │ managers first,
                              │  + open-weight LLM       │ money,     │ fuzzy duplicates      │ 1 safe     │ retry 5xx,
                              │  → confidence + gap      │ synonyms   │                       │ repair     │ rollback
                              ▼                          ▼            ▼                       ▼            ▼
                    column-level question       value it can't     conflict / possible     fails twice   target rejects
                    (pauses the run)            clean confidently  duplicate                              the record
                              └────────────────────────────┬─────────────────────────────────────────────────┘
                                                 Escalation queue (UI)
                                     Approve · Correct · Reject  (+ "remember for next time")
                                                           │
                                   learned rules ◄─────────┴──► record re-enters validate → push immediately
```

- **Column-level questions pause the run** (a wrong mapping or date format would corrupt every row). Everything
  else is prepared in the meantime.
- **Record-level questions never block the run.** Every other record keeps flowing into the target. A record
  re-enters validate → push the moment its question is answered.
- **Everything is logged twice:**
  - a live activity stream (SSE) for watching the agent;
  - an audit trail (who / what / before → after / why) for accountability, exportable as CSV. Each record also
    carries its own change history and lineage back to the source row and file.

## The escalation boundary

The line is drawn at **evidence**. The agent acts alone when a *deterministic* check proves the decision. The LLM
may propose, and may add confidence to something the data already supports, but **an AI opinion alone never
decides a customer's data**. All thresholds live in [`backend/agent/policy.py`](backend/agent/policy.py). The UI
shows them in plain English under *How it decides*.

| Area | Handled alone | Escalated |
|---|---|---|
| Column mapping | known header alias **and** values fit, clear winner (≥72% combined, ≥15-pt lead on header+values alone) | two fields fit almost equally, or nothing fits well |
| Dates | format proven by the data (a value > 12), or ≥3 employees in other files agree | every value reads both ways (03/04 vs 04/03) |
| Duplicates | same ID / identical rows → merged; same name + different DOB → kept apart | different IDs but same name **and** DOB (or same email) |
| Cleaning | whitespace, casing, phone format, synonyms (`Engg`, `BLR`), typos, placeholders (`N/A`) | unknown category (`Special Projects`), unparseable value (`98765`) |
| Conflicts | files agree, only one has the value, or only one value is valid | two valid values disagree (only the client knows the source of truth) |
| Validation | fail once → safe syntax repair → re-check (`acmecorp,com`) | fails twice / required data missing everywhere |
| Push | 5xx / timeouts retried 3× with backoff; unchanged records skipped | target rejects the record (409 / 422) |

## "Delta" on top of what the AI does

1. **A deterministic layer around the model.** The LLM is one of three signals. The other two, header aliases and
   value profiling, plus schema validation, cross-file date evidence and a "valid value wins" rule for conflicts,
   decide what's safe. `tests/test_llm_boundary.py` checks that a 99%-confident AI still can't break a tie.
2. **Learning from corrections.** Every resolution can be remembered as a rule: column mapping, date format, value
   fix, source of truth, duplicate decision or skip. Re-running the client's next export asks only about what's
   *new*. In the sample, run 1 asks 12 questions and run 2 asks 1.
3. **Incremental (delta) sync.** Before pushing, the agent diffs each record against what's already in the target:
   unchanged records are skipped, changed ones are sent as updates that list exactly which fields changed, and new
   ones are created. Rollback reverses a run: records it created are deleted, and records it updated are restored
   to their previous version.

## Sample data and planted traps

`data/samples/v1` holds three exports of the same ~60 employees from three legacy systems:

| File | Style |
|---|---|
| `hrms_legacy_export.csv` | `Full Name`, `DOJ` as DD/MM/YYYY, `Dept` abbreviations, phone in 4 formats |
| `payroll_export.xlsx` | `employee_code` like `EMP-0002`, Excel dates, salaries like `45.8 LPA` / `₹ 23,90,000` / `36,80,000` |
| `onboarding_tracker.csv` | `Last, First` names, MM/DD/YYYY dates, `Contact Number` |

| Trap | Expected behaviour |
|---|---|
| `Contact Number` could be own mobile or emergency contact | **ask** (column, pauses run) |
| onboarding `Joining Date` all ≤ 12/12, only 1 cross-file match | **ask** (date format, pauses run) |
| `Blood Group` has no target field | left out, reported |
| EMP0007 exact duplicate row | merged |
| EMP0040 & EMP0044 both "Amit Kumar", different DOB | kept apart |
| EMP0050 & EMP0056 "Vikram Singh", same DOB, different IDs | **ask** (possible duplicate) |
| EMP0015 joining date differs HRMS vs payroll | **ask** (conflict) |
| EMP0026 `.co` email in HRMS, `.com` in payroll | valid value wins automatically |
| EMP0023/31 department "Special Projects" | **ask** once for both (unknown value) |
| EMP0017 phone `98765` | **ask** (can't clean) |
| EMP0033 `neha.gupta@acmecorp,com` | fails once → repaired |
| EMP0029 `rahul.verma@acmecorp` | fails twice → **ask**, suggests `.com` |
| EMP0057/59 personal Gmail/Yahoo as work email | **ask**, suggests `first.last@acmecorp.com` and moves the personal address |
| EMP0051 in payroll only, no department | **ask**, suggests manager's department |
| EMP0038 email already used by EMP9001 in target | push 409 → **ask** |
| EMP0042 manager left the company | push 422 → **ask** |
| every 9th employee | first push gets 503 → retried automatically |

`data/samples/v2` is the same client a month later: a department transfer, a promotion, a new mobile number, and
two new joiners (one with a new department, "Data Science").

## Demo script (≈3 min recording)

1. *New migration → Sample client export.* Watch the live feed. The run pauses on **2 column-level questions**.
2. *Contact Number* → **Correct** → *Mobile number* (add a note). *Joining Date* → **Approve** month-first.
3. Let it push (managers first, a 503 retried). **10 cards** remain. Resolve a few: approve the email fix, merge
   the duplicate, pick *Operations* for "Special Projects", type a phone number (try `12345` first to show
   validation).
4. Open *Records* → a record's drawer (lineage + every change with its reason) → *Audit trail* → *Learned rules*.
5. *New migration → re-export (a month later)*. **1 question**, 3 updates, 1 create, 55 unchanged.
6. *Target system → Simulate 20s outage*, resolve the last card → retries → failed → **Retry** → done.
   *Roll back this run* restores the previous versions.

## Project layout

```
backend/
  main.py              FastAPI: REST + SSE stream, serves the built UI, mounts the mock target at /target-api
  schema.py            loads data/target_schema.yaml (aliases, synonyms, validation rules)
  agent/
    pipeline.py        the state machine: ingest → map → clean → reconcile → validate → push, rollback, resume
    policy.py          every escalation threshold + the plain-English policy shown in the UI
    mapper.py          header similarity + value profiling + LLM proposal → confidence & ambiguity gap
    profile.py         per-column value profiling
    clean.py           deterministic parsers (dates, phones, INR amounts, IDs, enums, names)
    validate.py        schema validation, the single safe repair pass, fix suggestions
    escalations.py     escalation cards (question, reason, evidence, proposal, correction form)
    resolve.py         applying approve / correct / reject, saving learned rules, resuming the run
    rules.py           learned rules store
    target.py          HTTP client for the target API
    llm.py             open-weight model fallback chain (Groq → OpenRouter → Ollama), error classification, cool-downs
mock_api/app.py        stub of the new HR platform (409 / 422 / flaky 503 / outage switch)
frontend/              React + TypeScript + Tailwind (Vite)
scripts/generate_data.py
tests/                 end-to-end + LLM-boundary tests
```

**Tech stack:** Python 3.12, FastAPI, pandas, SQLite, Server-Sent Events · React 18, TypeScript, Tailwind CSS 4,
Vite · gpt-oss-120b (Groq) with Llama 3.3 70B (OpenRouter) as fallback, or any open-weight model · pytest.
