# 0001. One definition per business rule

**Status:** Accepted, 2026-09-07. **Context date:** 2026-08 to 2026-09.

## Context

This repo's most expensive defects were one rule copied twice and drifting apart:

- **Two `risk_score` formulas.**
- **Two `recovery_potential` writers,** both random.
- **Two sets of allocator weights.**
- **Two `RESOLVED_STATUSES` sets.**
- **Seven copies of the DPD→bucket rule.** Two of them disagreed with the enum's own comments.
  Reading the code found six copies; a test asserting that nobody restates the rule found the
  seventh.
- **Four spellings of "what day is it"** in `manager.py`. They decided leave against the last
  plan date instead of the calendar date (CLAUDE.md known issue 13, 2026-09-23).

The 2026-09-24 audit (ENGINEERING-AUDIT §3.5, §5.4) found the pattern still live:
- an 8th DPD copy, in `scripts/generate_bank_data.py`, which disagrees on negative DPD
- three `bank_risk_score` formulas
- four permissive copies of "may this agent open this case" against three strict ones
- 14 restated constant families in the frontend

## Decision

- **Every business rule has exactly one definition.** The register is ARCHITECTURE.md §3.
  Other code imports it.
- **When a rule has already been copied once, a tripwire test guards it:** a test that fails if
  the rule is restated.
- A tripwire may read source text. It is the only kind of test that may, and it says so in its
  docstring.

## Consequences

- New code imports the rule. A reviewer rejects a restatement even when it is "just a
  threshold".
- `models/loan.dpd_bucket_for` is the DPD rule; `tests/test_dpd_bucket.py` is its tripwire. The
  tripwire checks a named list of files, so a new file that restates the rule is invisible to it
  until the list grows. That is its known weakness.
- The restructure (RESTRUCTURE-PLAN 2.3, 2.7, 2.8) consolidates the remaining copies behind
  characterisation tests.
