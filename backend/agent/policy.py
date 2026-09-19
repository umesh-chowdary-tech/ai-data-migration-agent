"""The escalation boundary - every threshold that decides "handle it" vs "ask a human" lives here.

Guiding principle: the agent acts alone when a *deterministic* check can confirm the decision
(an exact header alias, a value that parses one way only, an exact duplicate). The LLM may
*propose* - it never gets to be the only evidence for changing a customer's data.
"""

# --- Column mapping -----------------------------------------------------------------------------
MAP_AUTO_MIN = 0.72        # combined score needed to map a column without asking
MAP_MIN_GAP = 0.15         # ...and the runner-up must be at least this far behind (else it's ambiguous)
MAP_IGNORE_BELOW = 0.40    # below this, no target field is plausible -> column is not migrated (reported)

# Weights of the three evidence sources in the combined mapping score.
W_NAME, W_VALUE, W_LLM = 0.45, 0.35, 0.20
W_NAME_NO_LLM, W_VALUE_NO_LLM = 0.55, 0.45

# --- Dates --------------------------------------------------------------------------------------
# A column like 03/04/2025 is ambiguous. One value > 12 in either position settles the whole column.
# Otherwise we look for the same employees in other files: need this many agreeing matches.
DATE_EVIDENCE_MIN_MATCHES = 3

# --- Values -------------------------------------------------------------------------------------
ENUM_TYPO_CUTOFF = 0.86    # "Enginering" -> "Engineering" is a typo fix; below this similarity we ask

# --- Validation & push --------------------------------------------------------------------------
VALIDATION_MAX_ATTEMPTS = 2   # validate -> safe auto-repair -> validate again -> escalate
PUSH_MAX_ATTEMPTS = 3         # transient (5xx / network) errors are retried with backoff
PUSH_BACKOFF_SECONDS = 0.4

# Plain-English version shown in the UI so the consultant knows exactly when they'll be asked.
POLICY = [
    {
        "area": "Column mapping",
        "auto": "Header is a known name for a target field AND the values look right, with a clear winner.",
        "ask": "Two target fields fit almost equally well, or nothing fits confidently. The AI can confirm a mapping but never break a tie.",
        "why": "A wrong column mapping silently corrupts every row, so a close call costs one click to confirm.",
    },
    {
        "area": "Dates",
        "auto": "The format can be proven from the data (e.g. 25/03/2021 can only be day-first).",
        "ask": "Every value in a column reads validly both ways (03/04 vs 04/03) and other files can't settle it.",
        "why": "Guessing wrong shifts dates by months without failing any check.",
    },
    {
        "area": "Duplicates",
        "auto": "Same employee ID (or identical rows) -> merged. Same name but different birth date -> kept apart.",
        "ask": "Different IDs but same name AND birth date (or same email) - could be a re-hire or a data-entry duplicate.",
        "why": "Merging two real people (or creating one person twice) is hard to undo downstream.",
    },
    {
        "area": "Cleaning values",
        "auto": "Whitespace, casing, phone formats, known synonyms (Engg -> Engineering), obvious typos, placeholders like N/A.",
        "ask": "A value that isn't a known spelling (e.g. department 'Special Projects') or can't be parsed (phone '98765').",
        "why": "Mapping an unknown value to a category is a business decision, not a formatting fix.",
    },
    {
        "area": "Conflicting sources",
        "auto": "Files agree, only one file has the value, or only one of the values is valid (a malformed email can't be the truth).",
        "ask": "Two files disagree on the same field for the same employee (unless you've told it which source wins).",
        "why": "Only the client knows which legacy system is the source of truth.",
    },
    {
        "area": "Validation",
        "auto": "First failure -> safe syntax repair (comma for dot in an email, schema default for status) -> re-check.",
        "ask": "A record that still fails after the repair attempt (fails twice).",
        "why": "Anything beyond a syntax repair would be inventing data.",
    },
    {
        "area": "Push to target",
        "auto": "Timeouts / 5xx errors are retried up to 3 times with backoff; unchanged records are skipped.",
        "ask": "The target rejects a record (e.g. email already used, manager doesn't exist).",
        "why": "A rejection means the data or the target needs a decision - retrying won't fix it.",
    },
    {
        "area": "Learning",
        "auto": "Once you resolve something with 'remember' ticked, the same situation is handled automatically next time.",
        "ask": "Anything new.",
        "why": "You should never have to answer the same question twice.",
    },
]
