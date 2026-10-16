# Phase 11 — pilot readiness gate status

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope:** closing the readiness gates left open by Phase 10. Coordination,
verification and documentation only.

**DECISION: NOT READY.** Of seven gates, **one is closed**. Four wait on named
people, one waits on the pilot bank, and one requires observing a real night in
an authorized environment. **No gate is blocked by an engineering defect.**

No approval, operator name, bank detail or verification result was invented. No
deployment, no migration, no live data, no multi-bank work.

---

## 1. Gate table

| # | Gate | Required evidence | Status | Responsible party | Next action | Evidence reference |
|---|---|---|---|---|---|---|
| A | PII policy approval | A signed policy record naming an approver and date | **PENDING APPROVAL** | *not known* — needs whoever owns borrower-data policy | Sign §6 of the sign-off record | `docs/PII-POLICY-SIGNOFF.md` §6; 20/20 tests re-verified |
| B | Permission-seed migration approval | A signed decision on revision `v2_0041` | **PENDING APPROVAL** | *not known* — needs schema-change authority | Sign the approval table; then create + apply v2_0041 | `docs/PHASE9-PROPOSED-MIGRATION.md` §"Phase 11 — union proof and approval checklist" |
| C | Daily operator, backup, recording location | Three names/locations: primary, backup, filing location | **PENDING OPERATIONAL INPUT** | *not known* — needs whoever assigns ops duty | Complete the ownership assignment table | `docs/PILOT-RUNBOOK-FEED.md` §6 "Ownership assignment" |
| D | Completed bank feed contract + approved sample | §§1–9 completed; a sanitized sample that passes validation | **PENDING EXTERNAL INPUT** | the pilot bank / integration owner | Send the §11 request; validate on arrival | `docs/PILOT-FEED-CONTRACT.md` §§1–11 |
| E | Pilot-environment verification | Eight steps executed **in the pilot environment** | **NOT VERIFIED** | authorized environment operator | Execute the Appendix command sheet | `docs/PILOT-ENV-VERIFICATION.md` Appendix |
| F | Actual scheduled-night observation | One night with all three stages dispatched at the real IST times | **NOT VERIFIED** | authorized environment operator | Observe a night on non-production data; fill the record | `docs/PILOT-NIGHT-RUN-RECORD.md` |
| G | CLI ingestion race workaround documented | Defect recorded, workaround preserved | **PASS** | engineering (closed) | none | `docs/PHASE8-ACCEPTANCE.md` DEFECT-8.1; runbook §8 |

**Engineering verification vs human approval vs external input**

| Classification | Gates | Note |
|---|---|---|
| Engineering verification complete | A, B, G (and the *preparation* for C, D, E, F) | the controls exist, are tested, and were re-verified this phase |
| Awaiting human approval | A, B | decisions, not work |
| Awaiting operational input | C, E, F | needs named people and an environment |
| Awaiting external input | D | needs the bank's format, volume and sample |

## 2. What this phase actually closed

**Gate G — closed.** DEFECT-8.1 remains documented in the acceptance report
with measured evidence (4 concurrent CLI ingests → 3 raised, 1 succeeded; book
correct), the workaround is preserved verbatim in runbook §8, and its evidence
test is retained. Ingestion was not redesigned and no constraint was touched
(`backend/app/models/lending.py` has no diff).

**Engineering re-verification, all passing:**

| Check | Result |
|---|---|
| PII implementation (20 focused tests, real PostgreSQL) | **20 passed** |
| Authorized role set unchanged | `{BANK_ADMIN}` — asserted, not assumed |
| Raw-field allow-list unchanged | 9 fields |
| Full acceptance suite (`tests/phase8`) | **71 passed** |
| Permissions + feed + audit suites | **95 passed**, 1 expected failure |
| Alembic graph | single head `v2_0040`; DB at head |
| `v2_0041` containment | **absent** from `alembic/versions/` (35 files, 0 matches) |

## 3. Gate B — the union was proven, not argued

Phase 10 reasoned that the equality test must assemble its expected seed from
both revisions. Phase 11 **ran** it: a prototype applied
`test_the_seed_equals_the_registry`'s three assertions verbatim to the union of
v2_0008's literals and the rows v2_0041 would insert.

```
v2_0008 alone        : codes match? False   grants match? False   missing: ['bank_feed.pii.read']
v2_0008 ∪ v2_0041    : codes match? True    grants match? True    per-row mismatches: none
strength check       : with a capability removed -> False  (detector still fires)
RESULT: UNION SATISFIES THE UNWEAKENED ASSERTIONS
```

So the fixture change is a fix to how the expected set is **assembled**, not a
weakening of what is **asserted**. The prototype touched no test file, no
migration directory and no database.

## 4. Commands prepared vs commands executed

| Command | Prepared for the operator | Executed by me, and where |
|---|---|---|
| Database identity (`current_database()`) | ✅ | ✅ **dev stack** → `fieldops\|fieldops` |
| Alembic parity (`current` vs `heads`) | ✅ | ✅ **dev stack** → both `v2_0040` |
| Worker/beat restart | ✅ | ❌ not run this phase (run in Phase 8) |
| `celery inspect registered` | ✅ | ✅ **dev stack** → all 5 tasks listed |
| Beat schedule + timezone | ✅ | ✅ earlier phases → 19:30/19:45/20:00, `Asia/Kolkata` |
| Recovery-sweep log watch | ✅ | ✅ **dev stack**, Phase 8 → fired `04:45:00.000Z` = 10:15 IST |
| IST business-date check | ✅ | ✅ earlier phases |
| PII holders + `DATA_EXPORT` query | ✅ | ✅ **isolated test DB** → `BANK_ADMIN` only |
| `tests/phase8` fault-injection suite | ✅ | ✅ **isolated `fieldops_phase8`** → 71 passed |
| Sample-feed dry run (`/bank/feed/validate`) | ✅ | ❌ no sample exists |
| A real scheduled night | ✅ (record form) | ❌ **never observed** |

**Nothing was executed in a pilot environment, because no pilot environment has
been identified.** Dev-stack and isolated-test-database results are labelled as
such everywhere and are not counted as Gate E or F evidence.

## 5. Documents changed, and why

| Document | Change | Why |
|---|---|---|
| `docs/PII-POLICY-SIGNOFF.md` | Status → **PENDING APPROVAL**; re-verification note | Gate A: record that tests passing ≠ approval |
| `docs/PHASE9-PROPOSED-MIGRATION.md` | New section: union proof + 10-row approval checklist | Gate B §4–§5: prove the test change, give the approver one page |
| `docs/PILOT-RUNBOOK-FEED.md` | Replaced the loose gap bullets with an **ownership assignment** section | Gate C: a form that can actually be signed |
| `docs/PILOT-FEED-CONTRACT.md` | Status → **PENDING EXTERNAL INPUT**; platform figures re-verified from code; new §11 sample request + validation procedure | Gate D §1–§5 |
| `docs/PILOT-ENV-VERIFICATION.md` | New Appendix: exact commands with **real** expected outputs + result-recording table | Gate E §2–§7 |
| `docs/PILOT-NIGHT-RUN-RECORD.md` | **New** | Gate F §8: the only artifact that can capture a real night |
| `docs/PHASE11-READINESS-GATES.md` | **New** (this file) | §10 deliverable |

Phase 8/9/10 reports were **not** rewritten; their terminology, statuses and
acceptance criteria are unchanged.

## 6. The honest summary

Every technical control the pilot depends on is implemented, tested against real
PostgreSQL, and re-verified this phase. What is missing is not code:

1. **Two signatures** (PII policy; migration v2_0041).
2. **Three names** (primary operator, backup, where records are filed).
3. **One bank's file format, volume and a sanitized sample.**
4. **One environment** in which to run the verification sheet.
5. **One night, watched at 19:30/19:45/20:00 IST.**

Items 2 and 5 are the ones I would not start a pilot without. Without named
operators the pilot has **no monitoring at all** — manual checks replace
alerting here. And without one observed night, nobody has seen the schedule
fire in the environment that matters; every other proof is a test or a
simulation.

## 7. Git safety

Verified at the start and end of this phase:

```
branch  : TIQCollect-app
HEAD    : ded3c1a            (unchanged)
staged  : 0
stashes : 0
modified : 47   (46 at phase start + docs/DATA-MODEL-V2.md, a Phase 9 edit
           already present; no file was newly modified by Phase 11 code changes)
untracked: 47   (45 at phase start + 2 new documents)
```

No commits, staging, stash, reset, clean, revert or any other destructive
operation. `backend/scripts/sample_daily_feed.csv` remains exactly as found
(8 insertions / 8 deletions) and its contents were **not read or used**. No
scratchpad or untracked file deleted. No migration applied, no schema changed,
no production database accessed, and no borrower PII in any artifact.
