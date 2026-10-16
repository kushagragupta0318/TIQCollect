# TIQCollect — pilot handoff summary

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope:** the bank feed pipeline — upload → ingestion → quarantine → scoring →
allocation — for **one** controlled bank.

# VERDICT: NOT READY — EXTERNAL APPROVALS OR OPERATIONAL ACTIONS REQUIRED

Engineering is complete and verified. Seven prerequisites remain, and **none is
code** — each needs a person to decide something, supply something, or watch
something happen.

This is the **entry point** to the handoff. It does not restate the other
documents; it says what is done, what is outstanding, and which document to
open. Read it first, then go where it points.

---

## 1. Document map

| You need | Open |
|---|---|
| **The four requests to send** | `PILOT-HANDOFF-REQUESTS.md` |
| **The checklist for the operator, after approvals** | `PILOT-EXECUTION-CHECKLIST.md` |
| Full audit, defect register, necessity matrix | `FINAL-AUDIT.md` |
| Gate-by-gate status with evidence references | `PHASE11-READINESS-GATES.md` |
| PII policy, rationale, residual risk, sign-off block | `PII-POLICY-SIGNOFF.md` |
| Migration review, union proof, approval checklist | `PHASE9-PROPOSED-MIGRATION.md` |
| Feed format form for the bank + sample procedure | `PILOT-FEED-CONTRACT.md` |
| Environment commands with real expected output | `PILOT-ENV-VERIFICATION.md` |
| Scheduled-night observation form | `PILOT-NIGHT-RUN-RECORD.md` |
| Daily operation, monitoring queries, ownership table | `PILOT-RUNBOOK-FEED.md` |
| Concurrency / crash / throughput / E2E evidence | `PHASE8-ACCEPTANCE.md` |
| Deploy, backup, rollback | `DEPLOY.md` §5–§7 |

## 2. What is implemented and tested

Built across Phases 3–11; every item verified against **real PostgreSQL**
unless noted.

| Capability | Evidence |
|---|---|
| Bank uploads a daily CSV; validation; staging | upload API + dry-run validate |
| Asynchronous ingestion with an **atomic claim** | 10 concurrent claims → exactly 1 winner |
| Crash recovery without duplicate side effects | real `SIGKILL` mid-run → 0 duplicate loans/cases |
| Stranded-batch recovery sweep (15 min, threshold 60 min) | observed firing on a real boundary in 24 ms |
| Quarantine review and **safe** release | concurrent release → 1 placement, 1 case |
| `UNKNOWN_BRANCH` correctly **not** releasable | asserted end to end |
| Nightly sequence enforced: 19:30 → 19:45 → 20:00 IST | gate reads persisted DB facts |
| A blocked night records evidence per manager | `FAILED` + `outcome: BLOCKED_UPSTREAM` |
| Raw-PII gated to **BANK_ADMIN**, audited, fail-closed | 20 focused tests |
| Tenant isolation across all feed surfaces | real HTTP, two banks |
| Honest status reporting throughout | `RECEIVED` never reads as ingested |

**Test results, as executed:**

| Suite | Result |
|---|---|
| `tests/phase8` — concurrency, worker-kill, PII, E2E (real PostgreSQL) | **71 passed** |
| `test_pipeline_scheduling.py` — schedule, gating, DEFECT-A1 | **51 passed** |
| Both feed-ingestion handoff suites | **46 passed** |
| Scoping / permissions / audit / celery | **58 passed** |
| Frontend (`npx vitest run`) | **944 passed**, 87 files |
| Build / lint / `compileall` | clean / 0 errors / clean |

**Measured throughput:** ~30 rows/s (31.5 ms/row). ~10,000 rows/night fits the
window comfortably; re-measure above ~25,000.

## 3. Known limitations — carried into the pilot knowingly

| # | Limitation | Severity | Status |
|---|---|---|---|
| 1 | **No automated alerting.** A daily manual check replaces it, by agreed design | HIGH **until an operator is named** | accepted for one bank |
| 2 | `test_the_seed_equals_the_registry` fails **by design** until `v2_0041` is approved | MEDIUM | not weakened, not hidden |
| 3 | **No stage durations recorded** — a nightly overrun cannot be detected or trended | MEDIUM | open, non-blocking |
| 4 | Throughput unmeasured above 2,000 rows | MEDIUM | re-measure if volume demands |
| 5 | **DEFECT-8.1** — simultaneous *manual CLI* ingestion of one file races on staging rows | LOW | workaround: one at a time |
| 6 | `tenancy.permissions` is one capability behind the code registry | LOW | authorization unaffected (proven) |
| 7 | Two overlapping feed-ingestion test suites (866 lines) | LOW | both pass; removal needs owner approval |
| 8 | 5 test modules cannot be collected locally (`pptx`; numpy/pyarrow) | LOW | pre-existing environment gap |

**One defect was found and fixed during the audit:** DEFECT-A1 — the scoring
readiness check tested existence where its own docstring promised counting, so a
crashed scoring run could have let allocation plan on an unscored book. Fixed,
with 4 regression tests. Detail in `FINAL-AUDIT.md`.

## 4. Outstanding prerequisites

All seven verified outstanding from repository evidence on 2026-10-16: five
mandatory approval/ownership fields still blank, 0 nights observed, backup
restore unticked, and the only running stack is the development stack
(`DEMO_MODE=true`).

| # | Prerequisite | Needed from | Artifact | Status |
|---|---|---|---|---|
| 1 | PII policy approval | owner of borrower-data policy — **not known** | `PII-POLICY-SIGNOFF.md` §6 | **PENDING APPROVAL** |
| 2 | Schema-change approval for `v2_0041` | schema-change authority — **not known** | `PHASE9-PROPOSED-MIGRATION.md` | **PENDING APPROVAL** |
| 3 | Primary operator, backup, records owner | operations lead — **not known** | `PILOT-RUNBOOK-FEED.md` §6 | **PENDING OPERATIONAL INPUT** |
| 4 | Feed spec, agency mappings, volume, sanitized sample | bank integration owner — **not known** | `PILOT-FEED-CONTRACT.md` | **PENDING EXTERNAL INPUT** |
| 5 | A genuine, authorized pilot environment | operations lead | `PILOT-ENV-VERIFICATION.md` Step 0 | **NOT VERIFIED** |
| 6 | One observed scheduled run (19:30/19:45/20:00 IST) | designated operator | `PILOT-NIGHT-RUN-RECORD.md` | **NOT VERIFIED** |
| 7 | Verified backup restoration | environment operator | `DEPLOY.md` §6 | **NOT VERIFIED** |

**No name, email, date, approval or environment detail is recorded anywhere in
this handoff, because none was supplied.**

### Dependency order

```
1, 2, 3, 4  — independent; start all four now
        ↓
5  (needs 3: someone must authorize the environment)
        ↓
7  (needs 5)        6  (needs 3 and 5)
        ↓                    ↓
        └──── pilot execution checklist ────┘
```

## 5. Criteria for declaring the controlled pilot ready

All of the following, with evidence recorded:

1. PII policy signed, with approver and date (prerequisite 1).
2. `v2_0041` either **approved and applied** — with
   `test_the_seed_equals_the_registry` then passing — **or** explicitly deferred
   in writing, with the expected failure documented (prerequisite 2).
3. Primary operator, backup and records location named; the first daily check
   completed and filed (prerequisite 3).
4. Feed contract returned; a sanitized sample validated; row outcomes agreed
   with the bank (prerequisite 4).
5. `PILOT-ENV-VERIFICATION.md` executed **in the pilot environment**, every step
   recorded (prerequisite 5).
6. Backup test-restored, with evidence (prerequisite 7).
7. **At least two** scheduled nights observed: one proving the schedule and gate
   (a correct block counts), one proving the full success path with a feed
   present (prerequisite 6).
8. `PILOT-EXECUTION-CHECKLIST.md` Stages 0–10 complete, with no STOP condition
   triggered, and the sign-off table signed.

**Any single item unmet ⇒ the verdict remains NOT READY.** A report, a
checklist or a passing test suite is not a substitute for the thing itself.

## 6. The two that matter most

Everything above is necessary, but two deserve emphasis because they are easy to
treat as paperwork and are not:

**Name the operator (3).** Manual checks *replace* alerting here. Until someone
owns the daily check, the pilot has **no monitoring at all** — not weaker
monitoring. A stuck feed or a blocked night would first be noticed by field
agents opening an empty app.

**Observe one real night (6).** Nobody has yet seen the 19:30 → 19:45 → 20:00
sequence fire in an environment that matters. Every current proof is a test, a
manually triggered task, or a simulated timestamp. Dev-stack and demo-host
observations are **not** pilot evidence and are labelled as such throughout.

## 7. Preservation statement

- **Protected file:** `backend/scripts/sample_daily_feed.csv` — never opened,
  read, parsed, used as input, modified or reverted. Diff unchanged at
  8 insertions / 8 deletions.
- **Migration state:** `v2_0041` absent from `backend/alembic/versions/`; single
  head remains `v2_0040`; `v2_0008` unmodified; **no migration applied**.
- **Git:** HEAD unchanged at `ded3c1a`; 0 staged; 0 stashes; no commit, push,
  stash, reset, clean, checkout-over or revert.
- **Unrelated work:** every modified and untracked file preserved, including
  those whose ownership is not ours (`test_feed_ingestion_handoff.py`,
  `agency_manager_service.py`, `caddy-local-root.crt`, the frontend files).
- **No deployment, no live bank data, no production or unknown database
  accessed.** Fault injection remained confined to the dedicated
  `fieldops_phase8` test database.
