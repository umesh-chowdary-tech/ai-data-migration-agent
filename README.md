# Migration Agent: client HR data → new platform, with a human in the loop

An AI agent that takes a client's messy legacy exports (CSV + Excel, different column names, mixed date formats,
duplicates, missing fields), works out how they map to the target schema, cleans, reconciles and validates the
data, and pushes it to the new platform's API **on its own**. It stops to ask an implementation consultant only
when the data genuinely can't prove the answer. A web UI lets the consultant watch it work live, resolve its
questions in one click, and see an audit trail of everything that happened.

> - One-page write-up of the approach and the escalation boundary: **[WRITEUP.md](WRITEUP.md)**
> - How well it works, measured against ground truth on held-out data: **[EVALUATION.md](EVALUATION.md)**

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
- **Tests:** `python -m pytest -q` runs every suite: end-to-end, security, resilience, AI fallback and
  concurrency.
- **Evaluation:** `python -m evals.run` scores the agent against ground truth on the canonical dataset and 15
  randomized held-out datasets, then writes [EVALUATION.md](EVALUATION.md). Add `--with-ai` to repeat it with the
  real models.
- **Regenerate the sample data (and its ground truth):** `python scripts/generate_data.py`

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
2. **Learning from corrections.** Every resolution can be remembered as a rule. Re-running the client's next export
   asks only about what's *new*. In the sample, run 1 asks 12 questions and run 2 asks 1.

   A rule is kept at the level of generality the decision really had:

   | Scope | Example | Used for |
   |---|---|---|
   | this employee | EMP0017's mobile = +91 98765 01234 | corrections of free-form values (phone, email, date). This is the default. |
   | this exact value | Department "Special Projects" means Operations | category fields, where the word itself carries the meaning |
   | this kind of problem | Whenever a mobile number isn't valid, leave it empty instead of asking | optional fields only, and the only action is "leave empty" |

   An exact-value rule on a free-form field would be wrong. It would almost never fire again, and when it did, it
   could copy one person's phone number onto someone else. So on cards about an employee's value, **Remember** asks
   which scope you mean. Older exact-value rules on free-form fields are flagged in the rules tab, with a one-click
   **Make it generic** option.

   Consultants can also **add or edit rules by hand** in the *Learned rules* tab, for example to record what the
   client said during onboarding. Every rule is a standing permission for the agent to decide alone, so a
   hand-written rule is **checked before it can be saved**:

   - **Validation.** It must be as valid as a decision on a card.
   - **Impact on real data.** The latest run shows what it would do: whether the agent already decides this on its
     own (so the rule isn't needed), whether it would override the evidence, and which employees it touches.
   - **AI review.** The AI reads only those measured facts and must cite them (`F1`, `F2` …). It recommends,
     advises caution, or advises against, but it can't block the rule.

   Saving then requires a reason, and an explicit acknowledgement if there are warnings. The server saves only the
   exact proposal that was reviewed. Rules about a single employee (skip, merge, per-record value) can't be created
   by hand, only edited or deleted. Every change is kept in the rule's history.
3. **Incremental (delta) sync.** Before pushing, the agent diffs each record against what's already in the target:
   unchanged records are skipped, changed ones are sent as updates that list exactly which fields changed, and new
   ones are created. Rollback reverses a run: records it created are deleted, and records it updated are restored
   to their previous version.

## Evaluation and safety

[EVALUATION.md](EVALUATION.md) is generated by `python -m evals.run`. No number in it is hand-written. It checks the
agent at four layers:

| Layer | Question |
|---|---|
| **Decisions** | Did it ask exactly about the genuinely ambiguous cases (escalation precision / recall), and were its own decisions right? |
| **Data** | Field-by-field, does the target system match the ground truth? |
| **Safety** | Can hostile files, a compromised AI or tampered API calls cause harm? |
| **Operations** | Does it survive AI outages, target outages and fast human input? |

How it works:

- **Ground truth.** The data generator writes the expected result alongside every dataset.
- **Consultant.** A simulated consultant who knows the truth answers the agent's questions, so any error left in the
  target is the agent's own.
- **Held-out data.** Beyond the canonical dataset (the one the agent was built against), 15 randomized datasets move
  the traps, change the people, and rename 30+ column headers, many to names outside the schema's alias lists.

The held-out run found a real profiling bug, which was fixed and then verified on fresh datasets. The report
documents it.

The adversarial suite ([`tests/test_security.py`](tests/test_security.py)) covers:

- prompt injection in headers and cells, using an AI that *obeys* it;
- spreadsheet-formula injection;
- an XML-bomb `.xlsx`;
- tampered resolution calls;
- path-traversal and oversized uploads.

Known limitation: there is no authentication. It's a single-user prototype.

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
| EMP0050 & EMP0056 "Vikram Singh", same DOB, different IDs (really one person re-entered) | **ask** (possible duplicate) |
| EMP0015 joining date differs HRMS vs payroll | **ask** (conflict) |
| EMP0026 `.co` email in HRMS, `.com` in payroll | valid value wins automatically |
| EMP0023/31 department "Special Projects" (really a sub-team of Operations) | **ask** once for both (unknown value) |
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

Each folder also has a `ground_truth.json`, written by the generator. It records:

- the correct final record for every employee;
- the correct mapping and date format for every column;
- which cases are genuinely ambiguous, and their answers;
- which traps must be handled without asking.

`evals/` scores the agent against it.

## Demo script (≈3 min recording)

1. *New migration → Sample client export.* Watch the live feed. The run pauses on **2 column-level questions**.
2. *Contact Number* → **Correct** → *Mobile number* (add a note). *Joining Date* → **Approve** month-first.
3. Let it push (managers first, a 503 retried). **10 cards** remain. Resolve a few:
   - approve the email fix;
   - merge the duplicate;
   - pick *Operations* for "Special Projects";
   - type a phone number (try `12345` first to show validation);
   - on the *joining date* conflict, choose **A different value** and give a reason.
4. Open *Records* → a record's drawer (lineage + every change with its reason) → *Audit trail* → *Learned rules*.
   Click **Add rule** and map column `Emp ID` to *Annual salary*. The data checks and the AI review both flag it as
   harmful.
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
    rules.py           learned rules store + change history
    rule_review.py     checks a hand-written rule: validation, measured impact on real data, AI review with citations
    problems.py        problem types a "whenever this happens" rule can target (and which fields qualify)
    target.py          HTTP client for the target API
    llm.py             open-weight model fallback chain (Groq → OpenRouter → Ollama), error classification, cool-downs
mock_api/app.py        stub of the new HR platform (409 / 422 / flaky 503 / outage switch)
frontend/              React + TypeScript + Tailwind (Vite)
scripts/generate_data.py   sample + randomized held-out datasets, each with ground_truth.json
evals/
  run.py               one command: generate held-out sets, run everything, write EVALUATION.md
  golden.py            runs the agent with a truth-knowing consultant and scores decisions + target data
  report.py            builds EVALUATION.md from the measured results
  results/             raw result JSON (incl. round 1, before the profiler fix)
tests/                 end-to-end, security, resilience, AI fallback, AI boundary, concurrency
```

**Tech stack:** Python 3.12, FastAPI, pandas, SQLite, Server-Sent Events · React 18, TypeScript, Tailwind CSS 4,
Vite · gpt-oss-120b (Groq) with Llama 3.3 70B (OpenRouter) as fallback, or any open-weight model · pytest.
