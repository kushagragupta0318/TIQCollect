# TIQCollect — final comprehensive audit

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope audited:** Phases 1–11 plus the "Final Project Completion" task.
**Phase 12: DOES NOT EXIST** — zero matches for `Phase 12` across `docs/`,
`backend/` and `frontend/`; phase documents stop at Phase 11.

> **Handoff entry point: `docs/PILOT-HANDOFF-SUMMARY.md`** — what is done, what
> is outstanding, and which document to open. The operator's post-approval
> checklist is `docs/PILOT-EXECUTION-CHECKLIST.md`.

## Executive verdict

**NOT READY — EXTERNAL APPROVALS OR OPERATIONAL ACTIONS REQUIRED.**

One genuine defect was found and fixed during this audit (DEFECT-A1, below).
Everything else is either verified working or waits on a person. No other code
defect blocks the pilot.

---

## Q1. Is everything built necessary?

**Mostly yes — one clear redundancy, nothing harmful.**

| Classification | Components |
|---|---|
| **ESSENTIAL** | Upload API + validation; async ingestion worker with the atomic claim; quarantine review/release; the pipeline readiness gate; raw-PII capability + audit; tenant scoping in `manager.py` |
| **IMPORTANT** | 19:30 cutoff task; recovery sweep; IST business-date unification; runbook + monitoring queries; the Phase 8 acceptance suite |
| **OPTIONAL** | The pilot paperwork set (12 docs). Necessary for a *pilot handoff*, not for the code to run |
| **REDUNDANT** | `backend/tests/test_feed_ingestion_handoff.py` vs `test_feed_ingestion_task.py` — 866 lines, ~21 vs 22 tests, heavily overlapping coverage. **Not mine; ownership unestablished** |
| **DEFECTIVE** | DEFECT-A1 — `scoring_status` existence-vs-count. **FIXED this audit** |
| **UNVERIFIED** | Pilot-environment behaviour; a real scheduled night; throughput above 2,000 rows; backup restore |
| **NOT IMPLEMENTED** | Nothing. Every feature claimed in the phase reports exists in code |

No new work duplicated an existing service, permission, audit action, status
value, table or background task — checked explicitly:

- **Audit action:** reused existing `DATA_EXPORT` and `PLACEMENT_CREATED`. No
  enum value invented (the enum is native; adding one needs a migration).
- **Status values:** reused `RECEIVED/VALIDATING/LOADED/PARTIAL/REJECTED` and
  `QUARANTINED/RELEASED`. Phase 7's attempt to add `BLOCKED_UPSTREAM` was
  **reverted** when `ck_allocation_runs_status` proved it needed a migration;
  the refusal is recorded as `FAILED` + `summary_metadata.outcome` instead.
- **Permissions:** one new capability, registered in the one registry.
- **Tables:** none added. Phases 3–11 added **no migration**; the only new
  revision (`v2_0040`) predates this work.

## Q2. What happens if we leave the repository as it is?

| # | Risk | Severity | Type | Evidence |
|---|---|---|---|---|
| 1 | **No monitoring.** Manual checks replace alerting by design; no operator is named | **HIGH** | operational dependency | `PILOT-RUNBOOK-FEED.md` §6 blank |
| 2 | **Nightly schedule never observed** at real IST times in any environment | **HIGH** | operational dependency | 0 nights recorded |
| 3 | `test_the_seed_equals_the_registry` fails **by design** until `v2_0041` is approved | MEDIUM | approval dependency | reproduced this audit |
| 4 | No stage durations recorded; a nightly overrun cannot be detected | MEDIUM | known gap (RISK-3) | no duration column |
| 5 | Throughput unmeasured above 2,000 rows (~30 rows/s measured) | MEDIUM | unverified | Phase 8 §5 |
| 6 | No backup test-restored | MEDIUM | operational dependency | `DEPLOY.md` §6 |
| 7 | DEFECT-8.1 — simultaneous **manual CLI** ingestion races on staging rows | LOW | known defect, workaround documented | 4 concurrent → 3 raised, book correct |
| 8 | Two overlapping feed-ingestion test suites (866 lines) | LOW | maintenance | both pass; 46 tests |
| 9 | `caddy-local-root.crt` untracked and not gitignored at repo root | LOW | hygiene | public cert, not a key; dated Oct 1, **not mine** |
| 10 | Local env cannot collect 5 test modules (`pptx`, numpy/pyarrow mismatch) | LOW | environment | pre-existing; CLAUDE.md documents the class |

**Nothing in this list is a latent crash, data-corruption or cross-bank
exposure risk.** Items 1, 2 and 6 are the ones that make a pilot unsafe, and
none is code.

## Q3. Can we safely revert unnecessary work?

**Partially — and only one candidate, which I did not touch.**

### A. Safe simplification (requires your approval; ownership unestablished)

| Candidate | Why | Dependencies | Validation | Rollback |
|---|---|---|---|---|
| `backend/tests/test_feed_ingestion_handoff.py` (410 lines, 21 tests) | Overlaps `test_feed_ingestion_task.py` (456 lines, 22 tests): both test the claim, double delivery, two-worker races, bank-from-batch, business date, rejection, retries, recovery | **None.** No production code imports it | Run `test_feed_ingestion_task.py` + `tests/phase8` after removal | Untracked file — recoverable only from a backup, **not** from Git |

**I did not delete it.** It is not my file, both suites pass, and the rollback
path is weak because it is untracked. Deleting someone's untracked test to save
maintenance effort is a bad trade without their say.

### B. Requires approval

- `v2_0041` migration (unapplied, deliberately outside `alembic/versions/`).
- `caddy-local-root.crt` — adding it to `.gitignore` or removing it. Not mine,
  dated before this work.

### C. Must remain

The readiness documents, the Phase 8 acceptance suite, the PII capability and
audit, the readiness gate, the claim and the recovery sweep. Each is either a
security control, a data-integrity safeguard, or the evidence trail a pilot
decision rests on. **"Not needed for a demo" is not "safe to delete."**

## Q4. Did you find and fix bugs?

### DEFECT-A1 — FIXED. `scoring_status` claimed to count and did not

**Severity: HIGH** (could permit allocation on an unscored book).

**How it was confirmed.** Reading the function against its own docstring. The
docstring said *"Counted rather than merely existing, so a run that died after
one loan does not read as a completed stage"* — the code was
`.first() is not None`, pure existence. No test covered the gap.

**Root cause.** Mine, from Phase 7. I simplified `.limit(1).count()` to
`.first()` for readability and silently dropped the counting intent the
docstring describes. The reviewing tests only ever created one snapshot, so the
difference never showed.

**Impact if shipped.** A scoring run that crashed after its first loan would
report the stage ready; the 20:00 allocation would then plan a full day of field
work from a book that was almost entirely unscored — the exact class of silent
wrongness the gate exists to prevent.

**The fix** (`backend/app/services/pipeline_readiness.py`): count snapshots for
the date and compare against the loan book, scoped to the banks that have a
snapshot for that date, with a floor below which the ratio carries no signal.

```python
MIN_SCORED_FRACTION = 0.5
MIN_LOANS_FOR_RATIO = 20
```

Two deliberate conservatisms, stated rather than hidden:

- The threshold catches a **crashed** run (a handful of rows against thousands),
  not a run that covered 60% and died. That is an accepted limit.
- The denominator is scoped to banks with a snapshot, so one bank's unscored
  book cannot close the gate on every other bank's night.

**A regression I caused and caught.** My first version counted *all* loans
globally. That broke `test_allocation_opens_only_when_both_stages_are_present`
on real Postgres (7 leftover loans from other tests inflated the denominator)
and would have mis-blocked a real multi-bank night. The tenant-scoped
denominator plus the floor fixed both.

**Regression tests added** (`backend/tests/test_pipeline_scheduling.py`, 4 new):

| Test | Asserts |
|---|---|
| `test_a_scoring_run_that_died_early_does_not_count_as_complete` | 1 snapshot / 31 loans → **not ready** |
| `test_a_completed_scoring_run_is_ready` | 3 / 3 → ready |
| `test_a_small_book_is_not_held_hostage_by_the_ratio` | below the floor → ready |
| `test_the_blocked_allocation_gate_reports_partial_scoring` | end to end → `blocked_by: ["scoring"]` |

**Verification:** `test_pipeline_scheduling.py` **51 passed** (47 existing + 4
new); `tests/phase8` on real PostgreSQL **71 passed**.

### Investigated and found NOT to be defects

| Lead | Finding |
|---|---|
| `RepaymentService(db)` built before the gate's early return | Constructor is inert (`self.db = db`). Cosmetic; left alone per "don't refactor working code" |
| `_record_blocked` queries managers without a bank filter | **Correct.** The nightly task is system-wide; the evidence rows must mirror the same population, and the tenant listener attributes each row from its manager |
| Unprotected feed endpoint | None. All 7 routes carry `require_perm` |
| Client-side role list in my frontend | None. `MLOpsPage` has one but is pre-existing |
| UI claiming "ingested" | None. Only "cannot be ingested" (a refusal) and comments |
| ML behaviour changed | **None.** No file under `app/ml/`, no artifact, no `ML_*` switch has any diff |
| Blocked allocation could still plan | Cannot. The gate returns before the planning loop |

## Q5. Is the project finished?

**NOT READY — EXTERNAL APPROVALS OR OPERATIONAL ACTIONS REQUIRED.**

The code is in a clean, stable state and no engineering work remains that I can
safely perform. What is outstanding is two approvals, three names, one bank's
file format, one environment, one observed night, and one restored backup.

This is **not** a demo verdict: the dev stack runs and demos fine, and
`deploy/.env.prod` is self-labelled a LAN demo host. Neither is pilot evidence.

## Q6. Minimum remaining work

### Code (none blocking)
Nothing. DEFECT-A1 was the only confirmed defect and it is fixed and tested.

### Approvals (2)
1. PII policy — `PII-POLICY-SIGNOFF.md` §6.
2. Migration `v2_0041` — `PHASE9-PROPOSED-MIGRATION.md`. Clears the one
   expected test failure. **Do not apply it to make the test green first.**

### Operational inputs (3)
3. Primary + backup daily operator, and where records are filed.
4. An identified, authorized pilot environment.
5. The bank's feed spec, volume, branch codes and a sanitized sample.

### Verification, once 3–5 exist (3)
6. Execute `PILOT-ENV-VERIFICATION.md` in that environment.
7. Observe one real night (19:30/19:45/20:00 IST).
8. Test-restore one backup.

### Optional, non-blocking
Stage-duration instrumentation (closes RISK-3); the 14-queries-per-row
ingestion cost (only matters above ~25,000 rows/night); DEFECT-8.1's isolated
fix; a decision on the duplicate test file.

---

## Addendum (2026-10-16) — two open questions, investigated and closed

### The ML-artifact check: NOT NEEDED. An equivalent control already exists.

I raised this as a possible gap after seeing
`ml.no_champion_artifact model=recovery_risk — the allocator will run with no
model at all` in the suite logs. Investigated before proposing any change, and
**withdrawn**: the control is already there, and it is better than what I was
going to suggest.

`app/services/ml_scoring_service.py:639-650` — when `DecisionEngine.get(model)`
returns None it logs at **ERROR** and returns an empty score dict. The comment
records why it is loud: a previous silent version of this degradation "went
unnoticed for a full cycle" and was fixed on 2026-09-08.

The fallback is safe by construction, not by luck.
`planner_service.py:514-520`:

```python
ml_probs = self._ml_recovery_probabilities(sorted_cases)
# Paired by construction: no probabilities means the original
# transform, so a model outage cannot leave the allocator running a
# configuration nobody measured.
transform = (_settings.ALLOCATOR_VALUE_TRANSFORM if ml_probs
             else "log_current")
```

So a missing artifact cannot produce **invalid** predictions — it produces
**no** predictions, and the allocator switches transform *and* affinity
together to the measured no-model configuration.

| Question (§3) | Answer |
|---|---|
| Exact loading path | `DecisionEngine.get(model)` in `ml_scoring_service._ml_recovery_probabilities` |
| Could it cause incorrect scoring or an unsafe decision? | **No.** Empty dict, not wrong values; the transform is switched in the same branch |
| Does an equivalent check exist? | **Yes** — ERROR log + paired fallback |
| Reproduced? | Yes, observed in the local run; the artifact is present on disk (champion `2.2.0`) but cannot load in this environment's numpy |
| Verdict | **Not a defect. Not even defence-in-depth — adding a second check would be redundant complexity** |

**No code was written for this.** The operational point stands and belongs in
the pilot checklist rather than in code: if the pilot host has the same numpy
state, `recovery_risk` would be absent from allocation and the evidence would
be an ERROR log line. Worth a glance at the worker log on day one.

### Overlapping ingestion tests: KEEP BOTH. Consolidation would lose coverage.

Compared case by case. **Recommendation only — neither file was deleted, moved
or rewritten.**

| | `test_feed_ingestion_handoff.py` | `test_feed_ingestion_task.py` |
|---|---|---|
| Lines / tests | 410 / 21 | 456 / 22 |
| Ownership | **not established** (not written by this work) | this work (Phase 5) |
| Infrastructure | SQLite suite fixtures, `monkeypatch` (47 refs) | same (55 refs) |
| Overlap | claim, double delivery, two-worker race, bank-from-batch, business date, rejection paths, retries, recovery | same |

**Unique to the handoff suite (5):** retry exhaustion ending terminal; claim
timestamp stamping; sweep **boundedness and oldest-first ordering**; broker
outage *during* the sweep; `IngestionError` raisability.

**Unique to the task suite (5):** the HTTP upload does not ingest; upload
enqueues exactly one task; a duplicate upload does not re-enqueue; a broker
failure is reported not swallowed; the engine receives the exact staged bytes.

The two cover **different seams** — one the sweep's internals, the other the
upload→dispatch boundary. Consolidating either direction drops real assertions.
Both pass together (46 tests, no interference). The maintenance cost is 866
lines of overlap; the cost of merging is losing coverage and editing a file
whose owner has not been identified.

**Recommendation: keep both.** If the duplication is later judged not worth it,
the safe route is to have the handoff suite's owner identify which of their 5
unique tests to port, then retire the duplicated cases — not to delete a file.

## Phase-by-phase necessity matrix

| Phase | Implemented | Problem solved | Needed? | Depends on | Verdict |
|---|---|---|---|---|---|
| 1–2 | Environment repair; `run_ingestion` returns a typed result | Seed/login/Tailwind failures; CLI could not report an outcome | Yes | — | **ESSENTIAL** — keep |
| 3 | Upload API + validation + staging (`feed_upload_service`, `bank_feed.py`) | Ingestion required shell access | Yes | 2 | **ESSENTIAL** — keep |
| 4 | Upload UI + history (`LoanFeedPage`, `feedUploadModel`) | No way to submit or track a feed | Yes | 3 | **ESSENTIAL** — keep |
| 5 | Async ingestion worker + atomic claim + recovery sweep | A staged file was never consumed | Yes | 3 | **ESSENTIAL** — keep |
| 6 | Quarantine review + safe release | Held rows had no workflow | Yes | 5 | **ESSENTIAL** — keep |
| 7 | 19:30 cutoff, readiness gate, IST business date | Stage order was a comment; nothing checked it | Yes | 5 | **ESSENTIAL** — keep (**DEFECT-A1 fixed here**) |
| 8 | Acceptance suite: concurrency, worker-kill, benchmark, E2E | Claims were untested on Postgres | Yes | 3–7 | **IMPORTANT** — keep |
| 9 | `bank_feed.pii.read` + `DATA_EXPORT` audit + fail-closed | Tech Ops read borrower PII untraced | Yes | 6 | **ESSENTIAL** — keep |
| 10 | Migration review; PII sign-off; feed contract; env plan | Gates had no signable artifact | Yes (handoff) | 9 | **OPTIONAL/IMPORTANT** — keep |
| 11 | Union proof; ownership table; command sheet; night record | Gate evidence was reasoned, not proven | Yes (handoff) | 10 | **OPTIONAL/IMPORTANT** — keep |
| 12 | — | — | — | — | **NOT IMPLEMENTED — does not exist** |

## Defect register

| ID | Severity | Status |
|---|---|---|
| DEFECT-A1 | **HIGH** | **FIXED + tested** this audit |
| DEFECT-8.1 | LOW | Open; documented workaround (one manual ingest at a time) |
| RISK-3 (no stage durations) | MEDIUM | Open gap, non-blocking |
| RISK-5 (`tenancy.permissions` one row behind) | LOW | Open; authorization unaffected (proven); clears with `v2_0041` |
| Duplicate feed-ingestion test suites | LOW | Open; removal needs your approval |
| `caddy-local-root.crt` unignored | LOW | Open; not mine, untouched |
| 5 test modules uncollectable locally | LOW | Environment, pre-existing |

**Critical: none. High: none open** (A1 fixed).

## Files changed during this audit

| File | Change |
|---|---|
| `backend/app/services/pipeline_readiness.py` | DEFECT-A1 fix: count + tenant-scoped denominator + floor |
| `backend/tests/test_pipeline_scheduling.py` | 4 regression tests |
| `docs/FINAL-AUDIT.md` | This report |

Nothing else was modified, moved or deleted.

## Test commands and actual results

| Command | Result |
|---|---|
| `pytest tests/test_pipeline_scheduling.py -q` | **51 passed** |
| `PHASE8_DB=1 pytest tests/phase8 -q` (real PostgreSQL) | **71 passed** |
| `pytest tests/test_feed_ingestion_handoff.py tests/test_feed_ingestion_task.py -q` | **46 passed** |
| `pytest tests/test_scope_interfaces.py tests/test_permissions.py tests/test_request_context.py tests/test_audit_tenant.py tests/test_celery_time_limits.py -q` | **58 passed** |
| `pytest tests/test_permission_seed.py -q` | **1 failed (expected by design), 2 passed** |
| `python -m compileall -q app` | clean |
| `npm run build` | clean (type-check passed) |
| `npm run lint` | **0 errors**, 2 pre-existing warnings |
| `npx vitest run` | **944 passed, 87 files** |
| Full backend suite | see note below |

**Full-suite baseline — CORRECTED 2026-10-16 after the long run finished.**
An earlier statement in this report implied only five modules were affected.
The measured local baseline is larger:

Two full runs were completed, one before the DEFECT-A1 fix and one after. The
comparison is the evidence that matters:

| | Pre-fix | Post-fix | Delta |
|---|---|---|---|
| passed | 3230 | **3234** | **+4** (exactly the new regression tests) |
| failed | 71 | **71** | **0** |
| errors | 188 | **188** | **0** |
| skipped | 205 | 205 | 0 |
| duration | 48:11 | 49:52 | — |

**The fix added 4 passing tests and changed no other outcome.** That is the
strongest available evidence it introduced no regression anywhere in the
backend, including in suites it was never expected to touch.

Attribution, measured rather than assumed:

- **No failure touches the code changed in this audit.** Every suite covering
  the changed files was run together: `test_pipeline_scheduling`,
  both feed-ingestion suites, `test_bank_feed_upload`,
  `test_bank_feed_quarantine`, `test_permissions`, `test_permission_seed`,
  `test_planner_service`, `test_bank_audit` → **221 passed, 1 failed**, and
  that one is `test_the_seed_equals_the_registry`, which fails **by design**
  until `v2_0041` is approved.
- Five modules cannot be **collected** locally: `test_bank_reports_api` needs
  `pptx` (absent); `test_ledger_phase2`, `test_ml_pipeline`,
  `test_recovery_risk_v2`, `test_recovery_risk_v21` hit
  `No module named 'numpy.lib.array_utils'`.
- `test_report_engine.py` fails on the missing `pptx`.
- `test_recovery_risk_pit_audit.py` errors (24) on a numpy dtype fault at
  `app/ml/simulation/ledger/panel.py:936`
  (`_UFuncNoLoopError: ufunc 'add' … dtype('<U1'), dtype('<U6')`). **No phase
  touched `app/ml/`** — verified: zero diff under that path, no artifact and no
  `ML_*` switch changed — so this is a pre-existing local
  numpy/pyarrow-version fault, not a regression from this work.

**Classified NOT VERIFIED, not PASS.** The remaining failures in the 71/188
were not individually attributed before this report was written: the captured
output was truncated to its last lines, and a re-run with full failure
reporting was still in progress. They are **not** claimed as either passing or
as caused by this work — the evidence that this work is clean is the targeted
221-test run above, not the full-suite number. CLAUDE.md documents this class
of local-environment gap.

**The protected `backend/scripts/sample_daily_feed.csv` was not read, printed,
parsed, used as test input, modified or reverted.** Its existence and protected
status are acknowledged only.

## Preservation confirmation

- **Protected file:** `sample_daily_feed.csv` — diff still `8 insertions /
  8 deletions`, contents never opened.
- **Unrelated work:** every modified and untracked file preserved, including
  files whose ownership is not mine (`test_feed_ingestion_handoff.py`,
  `agency_manager_service.py`, `caddy-local-root.crt`, several frontend files).
  Counts moved during the audit (47 -> 51 modified, 49 -> 50 untracked) from
  **concurrent activity outside this work**: 33 frontend files are
  tracked-modified and I changed none of them this session (verified by name).
  My own changes were three files, listed above.
- **Migration state:** `v2_0041` absent from `alembic/versions/`; head remains
  the single `v2_0040`; no migration applied; `v2_0008` unmodified.
- **Git history:** no commit, stage, stash, reset, clean, checkout-over or
  revert. HEAD unchanged at `ded3c1a`.
- **No database was deployed to, migrated, or had live data modified.** Fault
  injection remained confined to the dedicated `fieldops_phase8` database.
