# Write-up: scoping the agent's autonomy

## Approach

I built the agent as a **resumable state machine with an LLM at the judgment points**, not a free-running tool
loop (read → map → clean → combine → validate → push). A customer-facing migration needs predictability and an
explainable audit trail more than open-ended planning.

Each step does the confident work itself. Only the uncertain residue becomes an **escalation card**, which carries
the question, why the agent didn't decide, the evidence, and a one-click suggestion.

## Where I drew the line, and why there

**The agent acts alone when a deterministic check can prove the answer.** The open-weight LLM (gpt-oss-120b on
Groq, falling back to Llama 3.3 70B on OpenRouter) may *propose*, or add confidence to what the data already
supports. Its opinion alone never changes customer data. If every provider is down, the agent keeps working and
says "No AI involved".

- **Columns.** A column is mapped alone only if header + values *by themselves* separate the winner from the
  runner-up. Every real model said "Contact Number = mobile" at 90–100%. It is genuinely ambiguous (own or
  emergency contact?), so the agent still asks.
- **Column-level doubt pauses the run; record-level doubt never does.** A wrong date format corrupts every row. One
  odd record shouldn't hold up 59 good ones.
- **Formatting vs business decisions.** The agent fixes `Engg`, `acmecorp,com`, `N/A` and `45.8 LPA`, because each
  can only mean one thing. Which department "Special Projects" belongs to is a business decision, so it asks.
- **Cost of being wrong.** Merging two real people, choosing between two *valid* conflicting values, and inventing
  a required field are all hard to undo. These are always asked, with the agent's best guess as the suggestion.
- **"Fails twice".** A validation failure gets one syntax-only repair, then escalates. Push errors are retried if
  transient; rejections (409/422) are escalated.

I deliberately did **not** draw the line at "ask whenever the LLM is unsure". LLM confidence is poorly calibrated,
so that line would move with the model.

## The delta beyond what the AI does

1. **A deterministic layer around the model.** Value profiling, schema validation, cross-file date evidence, and a
   rule that when sources conflict, the only valid value wins.
2. **Learning from corrections.** Every resolution can become a rule. The client's re-export a month later asks
   **1 question instead of 12**.
3. **Incremental sync.** A diff against the target sends only changed fields, in dependency order (managers
   first). **Rollback** restores the exact previous state.

## How I know it works

[EVALUATION.md](EVALUATION.md) scores the agent against **ground truth**, not an LLM judge. The data generator
writes the expected outcome for every dataset, and a simulated consultant who knows the truth answers the agent's
questions. The report measures:

- escalation precision and recall (criterion 3: "not everything, not nothing");
- wrong autonomous decisions;
- field-level correctness in the target;
- delta, learning and rollback.

Scores on my own sample data would be circular, so the headline comes from randomized **held-out** datasets.
These move the traps and use unfamiliar column headers. Two held-out rounds each exposed a real bug (category words
scored as names; camelCase headers). Both bugs made the agent ask *more*, never decide wrongly. After the fixes, a
batch nothing was tuned on scored:

- **0 wrong autonomous decisions and 0 silent errors** across 9,440 fields;
- **100% recall**;
- **97% precision**. The misses were 2 unneeded questions, and they're left unfixed on purpose.

An adversarial suite covers prompt injection against an AI that *obeys* it, formula injection, XML bombs and
tampered API calls. It found and closed seven issues.

## What I'd build next

- **Dry-run preview.** Show the full diff against the target for sign-off before the first push.
- **Synonym-aware header matching** (cell = mobile), which would remove the last unneeded questions.
- **Rule governance** per client, and **threshold calibration** from logged human overrides.
- **Bulk resolution.** Resolve similar cards in one action.
- **Real connectors.** Auth, rate limits and idempotency against a real HRMS API.
- **Production security.** SSO with roles and a second approver for irreversible actions. There is no
  authentication in this prototype.
