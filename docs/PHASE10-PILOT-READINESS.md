# Phase 10 — one-bank pilot readiness closure

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope:** closing the operational readiness gaps Phase 9 left open. Verification,
documentation and configuration review only.

> **Superseded in part by `docs/PHASE11-READINESS-GATES.md`** (2026-10-16),
> which carries the current gate table. Phase 11 changed no acceptance
> criterion here; it added the approval/assignment artifacts each gate needs and
> re-verified the engineering. The decision below still stands.

**DECISION: NOT READY — three missing operational inputs and two pending
approvals.** No engineering defect blocks the pilot. Every unresolved item needs
a person to supply a name, a decision or a bank's file format; none needs code.

A report is not a deployment. Nothing in this phase was executed in a pilot
environment, no production deployment occurred, and no live bank data was
ingested.

---

## 1. Acceptance checklist

| # | Item | Status | Classification |
|---|---|---|---|
| 1 | PII policy **implementation** | **PASS** | — |
| 2 | PII policy **formal approval** | **NOT VERIFIED** | decision requiring approval |
| 3 | Permission-seed migration **review** | **PASS** | — |
| 4 | Permission-seed migration **approval** | **NOT VERIFIED** | decision requiring approval |
| 5 | Pilot feed contract prepared | **PASS** | — |
| 6 | Pilot feed contract **completed by the bank** | **NOT VERIFIED** | missing operational input |
| 7 | Sample feed validated | **NOT VERIFIED** | missing operational input (no sample supplied) |
| 8 | Named daily operator and backup | **NOT VERIFIED** | missing operational input |
| 9 | Daily check **recording location** | **NOT VERIFIED** | missing operational input |
| 10 | Daily check **record form** exists | **PASS** | — |
| 11 | Pilot-environment worker/beat verification **plan** | **PASS** | — |
| 12 | Pilot-environment worker/beat verification **executed** | **NOT VERIFIED** | missing operational input (no environment) |
| 13 | Feed-to-allocation acceptance test (isolated DB) | **PASS** | — |
| 14 | Feed-to-allocation acceptance test (pilot environment) | **NOT VERIFIED** | missing operational input |
| 15 | Rollback procedure documented | **PASS** | — |
| 16 | Rollback **rehearsed**, backup **test-restored** | **NOT VERIFIED** | missing operational input |
| 17 | Escalation contacts named | **NOT VERIFIED** | missing operational input |
| 18 | DEFECT-8.1 tracked with workaround | **PASS** | engineering defect, accepted |

**No row is classified as an unresolved engineering defect** except DEFECT-8.1,
which is documented, low severity, and has an operational workaround.

## 2. Permission-seed migration review (§2)

Measured, not assumed:

```
$ alembic heads    -> v2_0040 (v2) (head)      # single head; graph is linear
$ alembic current  -> v2_0040 (head)           # the database is at head
34 revisions on disk, no v2_0041 in alembic/versions/
```

Registry vs the v2_0008 frozen literals:

```
seed PERMISSIONS : 81     registry caps   : 82
seed ROLE_GRANTS : 156    registry grants : 157
missing from seed: ['bank_feed.pii.read']     extra in seed: []
```

**Test impact is narrower than Phase 9 feared.** Exactly one test fails:
`test_the_seed_equals_the_registry`. The two pinned counts (81, 156) live in
`test_the_seed_is_well_formed` and read v2_0008's own literals, so a *separate*
revision leaves them correct and untouched.

**Options:**

| Option | Effect | Verdict |
|---|---|---|
| 1. New revision `v2_0041` (parent `v2_0040`) | v2_0008 stays a true historical snapshot; the equality fixture must read both revisions | **recommended** |
| 2. Edit v2_0008's literals | smaller test diff, but environments already at v2_0008 never receive the row while new ones do — the exact divergence the frozen-literal rule prevents | rejected |
| 3. Defer indefinitely | authorization is already correct, but a test fails by design, so real future drift would look like the known failure | not recommended |

**Deployment implications of Option 1:** two `INSERT … ON CONFLICT DO NOTHING`
statements, no DDL, idempotent, reversible, and **no change in application
behaviour** — authorization reads the code registry, so the app behaves
identically before and after. It can ride along with any ordinary release.

**Does not block the pilot.** Proven against the live, un-synced database:

```
BANK_ADMIN -> True    BANK_TECHOPS -> False    SERVICE -> False
```

**Exact approval required:** create `v2_0041` seeding one capability and one
`BANK_ADMIN` grant, and widen `tests/test_permission_seed.py`'s fixture to read
both seed revisions. Approval block: `docs/PHASE9-PROPOSED-MIGRATION.md`
§"The exact approval required".

The proposal remains **outside** `backend/alembic/versions/`, was not executed,
and no schema was modified. The equality test was **not** weakened.

## 3. PII policy (§3)

Formalized in `docs/PII-POLICY-SIGNOFF.md`: nine rules (P1–P9), each traced to
the code that enforces it and the test that proves it, plus residual risks and
the failure window. Scope unchanged from Phase 9 — BANK_ADMIN only, no field
added to the allow-list.

Re-verified this phase: **20/20 PII tests pass** on real PostgreSQL.

Approver and date fields are **blank**. No approval has been given or implied.

## 4. Feed contract (§4)

`docs/PILOT-FEED-CONTRACT.md` — a form for the bank, not a guess at its schema.
The "what the platform requires" column is read from code (3 required headers,
25 MB, 200,000 rows, lat/lon and branch rules for new borrowers); every
bank-specific value is marked **NOT VERIFIED**.

The highest-risk item is called out explicitly: **each row must carry
`agency_code`**, because the asynchronous worker ingests an uploaded feed with
the bank code only. Without it every row is held `NO_AGENCY` — measured in
Phase 8, and the most likely first-day failure.

No sample was supplied, so §10 of that document is NOT VERIFIED. The externally
modified `backend/scripts/sample_daily_feed.csv` was **not** read for content,
used or modified.

## 5. Operational ownership (§5)

`docs/PILOT-RUNBOOK-FEED.md` now carries §6 (the 10-check daily pass with
escalation), **§6a — a per-day record form**, and §7 (raw-PII policy and the
review query). The record form captures date, business date, primary operator,
backup, all ten results, actions, escalation, incident reference, raw-PII read
count and a completion signature.

**Operator names remain blank.** The daily manual check is therefore **NOT
operationally covered**: since manual checking replaces alerting for this pilot,
an unowned check means the pilot has no monitoring. That is stated in the
runbook rather than smoothed over.

No automated alerting was built.

## 6. Pilot-environment verification (§6)

`docs/PILOT-ENV-VERIFICATION.md` — eight steps, starting with **Step 0:
identify the database before touching anything**, then backups and the approved
deploy procedure (`docs/DEPLOY.md` §5–§7, cited not duplicated), restart and
registration, the 15-minute sweep, IST dates and the three stages, tenant
isolation and PII audit, feed-to-allocation on approved test data, and rollback
with a decision rule and per-symptom escalation.

Every result column is **empty**, and the document states plainly that it is a
procedure, not a record of execution. Prior evidence from the dev stack and the
isolated database is cited in a separate column so the two are never confused.

**Two things that cannot be simulated and have not been done:** a full
19:30→20:00 night at the real clock times, and a test-restore of a backup.

## 7. Test results

| Suite | Result |
|---|---|
| `tests/phase8` (Postgres, 71 tests incl. 20 PII) | **71 passed** |
| permissions + quarantine + audit | 75 passed, **1 expected failure** |
| `test_the_seed_equals_the_registry` | **FAILS BY DESIGN** — see §2 |

The expected failure is the drift detector working. It must not be silenced; it
clears when v2_0041 is approved and applied.

## 8. DEFECT-8.1 (§7)

Unchanged and still tracked: concurrent manual CLI ingestion of one file races
on `uq_bank_feed_rows_batch_id_row_no`. Measured in Phase 8 (4 concurrent → 3
raised, 1 succeeded; the book stayed correct: 1 loan, 1 customer, 1 staging
row). The scheduled path is protected by the Phase 5 claim.

**Workaround, preserved verbatim in the runbook §8:** never run simultaneous
manual ingestions of the same feed. The proposed minimal fix stays documented
separately. Ingestion was not redesigned and no concurrent CLI ingestion was run
against a live database in this phase.

## 9. What stands between here and a pilot

**Two approvals:**
1. PII policy sign-off — `docs/PII-POLICY-SIGNOFF.md` §6
2. Migration v2_0041 — `docs/PHASE9-PROPOSED-MIGRATION.md`

**Three operational inputs:**
3. A named daily-check operator, a backup, and a place to record outcomes
4. The completed feed contract plus a sanitized sample that passes validation
5. A pilot environment in which `docs/PILOT-ENV-VERIFICATION.md` is executed,
   including a test-restored backup and one observed full night

Until 3 is supplied the pilot has no monitoring, and until 4 is supplied the
first feed may be entirely quarantined. Those two are the real gates; 1, 2 and 5
are procedural.

## 10. Git safety

Verified at the start and end of this phase:

```
branch : TIQCollect-app
HEAD   : ded3c1a          (unchanged)
staged : 0
stashes: 0
```

No commits, no staging, no stash, reset, clean, revert or any other destructive
operation. `backend/scripts/sample_daily_feed.csv` remains exactly as found
(8 insertions / 8 deletions, untouched by this phase). No scratchpad or
untracked file was deleted. No production database was accessed and no
borrower PII appears in any artefact produced here.
