# Phase 8 — production acceptance report: bank feed pipeline

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope:** the feed ingestion, quarantine release, scheduling and allocation
gating built in Phases 3–7. One controlled bank.

**DECISION: READY FOR CONTROLLED PILOT, conditional on the operator accepting
two open items** — the field-level PII policy (RISK-1) and the absence of
operational alerting (RISK-2). Both are decisions, not engineering work, and
neither is a data-integrity defect. Every critical safety requirement that
could be verified by test was verified against real PostgreSQL and **passed**.

---

## 1. What was run, and where

Everything below ran against a **dedicated** PostgreSQL database,
`fieldops_phase8`, never the dev book. The fixture asserts
`current_database() = 'fieldops_phase8'` before anything executes, so a
misconfigured run aborts rather than injecting faults into real data.

**Verified isolation:** after all testing, `fieldops` held 29,040 loans
(unchanged) and **0** rows matching the Phase 8 tenant codes. No leakage.

```bash
# one-time setup
docker compose exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE fieldops_phase8;"'
docker compose exec -T api sh -lc \
  'cd /app && export DATABASE_URL="$(python -c "
from app.core.config import settings
print(settings.DATABASE_URL.rsplit(chr(47),1)[0] + chr(47) + \"fieldops_phase8\")")" \
   && alembic upgrade head'

# the suite (44 tests). Lookup tables are seeded by the fixture.
docker compose exec -T api sh -lc \
  'cd /app && PHASE8_DB=1 python -m pytest tests/phase8 -q -p no:cacheprovider'

# the benchmark
docker compose exec -T api sh -lc \
  'cd /app && PYTHONPATH=/app PHASE8_DB=1 python tests/phase8/bench_ingestion.py 100 500 2000'
```

Without `PHASE8_DB=1` the database tests **skip** (39 skipped, 5 config-only
tests still run), so an ordinary project `pytest` never touches Postgres.

**Result: 44 passed on real PostgreSQL.** Phases 5–7 suites re-run on the
existing SQLite harness: **157 passed**, no regressions.

---

## 2. Acceptance checklist

| # | Requirement | Result | Evidence |
|---|---|---|---|
| 1 | Two workers cannot both claim one `RECEIVED` batch | **PASS** | 2 threads → `{1,0}`; 10 threads → exactly 1 winner |
| 2 | Duplicate delivery of `ingest_feed_batch` ingests once | **PASS** | concurrent delivery: `run_ingestion` called once |
| 3 | Delivery after completion does nothing | **PASS** | `claimed=False`, engine not called |
| 4 | Concurrent quarantine release places once | **PASS** | 1 release, **1 placement, 1 case** |
| 5 | Release blocked by a held row lock declines cleanly | **PASS** | waits, then `ALREADY_RELEASED`; 0 placements |
| 6 | Concurrent ingestion does not duplicate loan/customer | **PASS** | 1 loan, 1 customer from 2 concurrent passes |
| 7 | Repeated blocked allocation plans nothing | **PASS** | gate closed, 0 placements |
| 8 | Injected failure mid-release rolls back fully | **PASS** | 0 placements, 0 cases, row still `QUARANTINED` |
| 9 | Row remains releasable after a failed attempt | **PASS** | subsequent release succeeded |
| 10 | No unexpected 500s on the release path | **PASS** | all release calls 200/404/403 as designed |
| 11 | **Worker SIGKILL mid-processing** | **PASS** | claim survived; uncommitted work discarded |
| 12 | Fresh claim not stolen by the sweep | **PASS** | `reclaimed_stale=0` |
| 13 | Stale claim reclaimed → `RECEIVED` | **PASS** *(simulated clock)* | see §4 |
| 14 | Recovery causes no duplicate side effects | **PASS** | 0 loans, 0 cases after kill + recovery |
| 15 | Stranded `RECEIVED` re-dispatched | **PASS** | sweep dispatched it |
| 16 | Terminal batches untouched by sweep | **PASS** | `LOADED`/`REJECTED` not re-dispatched |
| 17 | Retryable vs terminal failures distinguished | **PASS** | storage → retry + release; no file / refusal → `REJECTED`, no retry |
| 18 | Tasks registered after worker restart | **PASS** *(live worker)* | `celery inspect registered` lists all 4 |
| 19 | Beat holds exactly one schedule per stage at IST | **PASS** | 19:30 / 19:45 / 20:00, tz `Asia/Kolkata` |
| 20 | Recovery sweep runs on its 15-min cadence | **PASS** *(observed live)* | fired `04:45:00.000 UTC` = 10:15 IST, 24 ms |
| 21 | Business date uses the IST convention | **PASS** | 00:30 IST/16th → `2026-10-16` while UTC says the 15th |
| 22 | Gates read persisted DB facts | **PASS** | verdict flips when the row is written |
| 23 | Each batch status yields the intended verdict | **PASS** | `RECEIVED`/`VALIDATING`/`REJECTED` block; `LOADED`/`PARTIAL` pass |
| 24 | Stale backfill not accepted as tonight's feed | **PASS** | 30 h-old `LOADED` → not ready |
| 25 | Explicit operator plan date retains behaviour | **PASS** | dated run bypasses gate (Phase 7 design) |
| 26 | Blocked allocation cannot silently proceed | **PASS** | `FAILED` + `outcome=BLOCKED_UPSTREAM` row per manager |
| 27 | Cross-bank feed history hidden | **PASS** | real HTTP, two banks |
| 28 | Cross-bank batch/row detail = uniform 404 | **PASS** | identical to a nonexistent id |
| 29 | **Cannot release another bank's row** | **PASS** | 404, row untouched |
| 30 | Read-only role cannot review or release | **PASS** | `BANK_ANALYST` → 403 on both |
| 31 | Error bodies leak no trace/paths/driver names | **PASS** | no `Traceback`, `/app/`, `psycopg2` |
| 32 | Release is audited and identifies a feed release | **PASS** | `PLACEMENT_CREATED` + `source=FEED_RELEASE` |
| 33 | `RECEIVED` never presented as ingested | **PASS** | upload returns `RECEIVED`, 0 loans in book |
| 34 | `PARTIAL` accurately represented | **PASS** | 1 of 2 rows held → `PARTIAL`, count 1 |
| 35 | Released row retried via existing placement path | **PASS** | `placements.source='FEED'`, real case |
| 36 | `UNKNOWN_BRANCH` unreleasable | **PASS** | `NOT_RELEASABLE`, row untouched |
| 37 | Duplicate upload causes no repeat processing | **PASS** | `duplicate=true`, no second dispatch |
| 38 | Monitoring queries find their conditions | **PASS** | 7/7 queries verified on real data |
| 39 | Measured throughput vs the operating window | **PASS** *(with caveats)* | §5 |
| 40 | **Field-level PII policy agreed** | **IMPLEMENTED (Phase 9)** | capability + audit + fail-closed, 20 tests; scope sign-off outstanding |
| 41 | **Operational alerting exists** | **FAIL (gap)** | RISK-2 |
| 42 | Stage durations recorded | **FAIL (gap)** | RISK-3 |
| 43 | Browser walkthrough of the UI | **NOT VERIFIED** | automated-test verified only |
| 44 | Real 60-minute stale-claim observation | **NOT VERIFIED** | simulated; see §4 |

---

## 3. PostgreSQL concurrency results

Method: real threads on separate Postgres connections, released from a
`threading.Barrier` so statements genuinely interleave. Bounded joins
(20 s barrier, 60 s join) — a deadlock fails the test rather than hanging it.
`statement_timeout = 15s` on the lock-contention test.

| Scenario | Concurrency | Expected | Actual | Duplicates? | 500s? |
|---|---|---|---|---|---|
| Claim one `RECEIVED` batch | 2 threads | 1 winner | `{1, 0}` | none | no |
| Claim one `RECEIVED` batch | 10 threads | 1 winner | `sum(rowcount)=1` | none | no |
| `ingest_feed_batch` duplicate | 2 threads | engine runs once | 1 call, 1 `claimed=True` | none | no |
| Release same row | 2 threads | 1 release | 1 release, **1 placement, 1 case** | none | no |
| Release while lock held | interleaved | second declines | `ALREADY_RELEASED`, 0 placements | none | no |
| Concurrent ingest, same row | 2 threads | 1 loan | **1 loan, 1 customer** | none | no |
| Blocked allocation, repeated | sequential ×2 | plan nothing | 0 placements both times | none | no |
| Injected failure mid-release | 1 thread | full rollback | 0 placements, 0 cases, `QUARANTINED` | none | n/a (raises) |

Final persisted states were read back in every case; counts above are from the
database, not from return values. **`with_for_update()` was not assumed to
work — it was exercised with a genuinely held lock** (test 5 opens a
transaction, holds the row, and starts the second release while it is held).

---

## 4. Worker-kill and recovery results

**The kill is real.** A subprocess claims the batch, commits the claim, opens a
second transaction writing partial work, then receives `SIGKILL` — no
`finally`, no handler, nothing Python gets to run.

| Step | Observed |
|---|---|
| 2. batch claimed | `VALIDATING`, seen from an independent connection |
| 3. `SIGKILL` | process died holding an open transaction |
| 4. committed state | claim **survived**; uncommitted partial work **gone** (`rows_accepted != 99`) |
| 5a. sweep, fresh claim | `reclaimed_stale=0` — a live slow worker is not robbed |
| 5b. sweep, aged claim | `reclaimed_stale≥1`, batch → `RECEIVED` |
| 6. side effects | **0 loans, 0 cases** |
| 7. redelivery | ingested once; `claimed=False` the second time; final `LOADED` |

**SIMULATED vs OBSERVED — stated plainly:**

- `STALE_CLAIM_MINUTES` **remains 60** and was never modified (asserted by a test).
- The stale-claim tests **backdate the claim timestamp** so the comparison the
  sweep makes at minute 61 runs in seconds. This is a **simulation of the
  clock**, not a 60-minute operational observation. A real 60-minute wait has
  **not** been performed (checklist #44).
- The **15-minute cadence was observed live**: beat dispatched
  `bank-feed-recovery` at `04:45:00.000 UTC` (10:15:00 IST) and the worker
  completed it in 24 ms with `found=0 reclaimed_stale=0 dispatched=0`.
- **Worker restart was performed for real** on the dev stack;
  `celery inspect registered` then listed all four pipeline tasks, including
  `feed_cutoff.check_feed_cutoff`. This closes the Phase 7 open item.

---

## 5. Ingestion benchmark

Real PostgreSQL, `fieldops_phase8`, synthetic borrowers, **3 repeats per size**.
Contract and region coverage were seeded so this measures the **successful**
path (0 skipped, 0 quarantined) rather than the quarantine path.

| rows | median s | min s | max s | rows/s | queries/row | skipped | quarantined | peak RSS |
|---|---|---|---|---|---|---|---|---|
| 100 | 2.89 | 2.75 | 3.02 | 34.6 | 14.2 | 0 | 0 | 98 MB |
| 500 | 16.91 | 15.04 | 17.12 | 29.6 | 14.0 | 0 | 0 | 116 MB |
| 2000 | 62.98 | 62.50 | 64.80 | 31.8 | 14.0 | 0 | 0 | 166 MB |

**Measured: ~30 rows/s, 31.5 ms/row at n=2000.** Throughput is flat across a
20× size range, so scaling is approximately linear **in this range**.

**The bottleneck is 14 queries per row**, constant at every size — a per-row
query pattern, not a batch one. Not changed in this phase (Phase 8 forbids
tuning for the benchmark); recommended as a separate piece of work.

**Dataset characteristics, stated rather than hidden:**
- Every row is a **new** borrower, so this is the INSERT path — the worst case.
  A real nightly feed is mostly UPDATEs of existing loans, a different and
  usually cheaper shape. **Not measured.**
- Each run also triggers the engine's end-of-run rescore (visible as 500
  snapshots written at n=500), so these figures include that cost.
- Sizes above 2,000 rows were **not** measured. The projections below are
  arithmetic, flagged as such.

**Against the operating window:** the 19:30→19:45 gap is 900 s. At 31.5 ms/row
that is **~28,500 rows**, and 19:30→20:00 is ~57,000 rows.

| Volume | Projected (ASSUMES linear scaling — unverified above 2,000) |
|---|---|
| 10,000 | 5.2 min — fits comfortably |
| 50,000 | 26.2 min — **exceeds** the 19:30→20:00 window |
| 100,000 | 52.5 min — **far exceeds** it |

For a controlled pilot of one bank at ≤10,000 rows/night, the window holds with
margin. **Above ~25,000 rows/night this needs re-measuring before go-live** —
and note that ingestion today starts when the bank uploads, not at 19:30, so
an early upload has more than 30 minutes available.

---

## 6. End-to-end workflow result

One pass, real HTTP, real engine, persisted state asserted at each step:

```
validate      ok=True rows=2, no batch written            (dry run writes nothing)
upload        RECEIVED, queued=True, 0 loans in book      (never "ingested")
dup upload    same batch_id, duplicate=true, no 2nd dispatch
ingest        status=PARTIAL, quarantined=1
book          good row ingested; unknown-branch row NOT created
redelivery    claimed=False, book unchanged
review        1 held, releasable=False, reason=UNKNOWN_BRANCH
release       NOT_RELEASABLE, row untouched
release (OK)  placement(source=FEED) + case(UNASSIGNED); row RELEASED by acting user
gate          blocked_by=['ingestion','scoring'] on the correct IST business date
```

**An operational finding worth recording:** the asynchronous worker calls
`run_ingestion` with `bank_code` only — there is no file-level `--agency` for
an uploaded feed. So **an uploaded row must carry its own `agency_code`**, or
placement is correctly refused `NO_AGENCY` and the row is held. This is a
requirement on the bank's file format and belongs in the pilot onboarding
checklist. It is correct behaviour, not a defect.

---

## 7. Security and PII review

**Passed:** cross-bank history, batch detail, quarantine listing and release
are all isolated (real HTTP, two banks, real JWTs). A foreign id returns a
**uniform 404** indistinguishable from a nonexistent one, so existence cannot
be probed. `BANK_ANALYST` is refused 403 on both review and release. Error
bodies carry no traceback, internal path or driver name. A release writes
`PLACEMENT_CREATED` with `source=FEED_RELEASE`, the acting user, the feed row
id and the original refusal reason.

### RISK-1 — the field-level PII policy is UNRESOLVED

Measured, not inferred:

- `include_raw=true` on the quarantine endpoints returns the row as received.
  Exposed fields: **`address_line1`, `customer_name`, `customer_ref`,
  `loan_account_number`, `pan_masked`, `phone_primary`.**
- It is **opt-in** and **off by default** — an ordinary listing returns business
  keys only. That part is sound.
- But it is gated on the **same** `bank_feed.upload` capability as uploading,
  and that capability is held by **`BANK_ADMIN`, `BANK_TECHOPS`, `SERVICE`**.
  So an engineering/ops role reads borrower name, phone and address.
- **No audit row is written when raw PII is read.** Only the release action is
  audited. Raw access is therefore unlogged.

There is no separate PII capability today. **I am not ruling the current
behaviour acceptable.** The responsible operator must decide:

1. Is `BANK_TECHOPS` reading borrower contact data acceptable for the pilot, or
   should raw access need its own capability?
2. Should every `include_raw=true` read be audited? (Recommended — it is a
   small, contained change, but it is new behaviour and out of Phase 8 scope.)

No real borrower PII appears in this report or in any test output; the PII test
asserts **field names only**, never values, and all fixtures are synthetic with
placeholder masked tokens.

---

## 8. Defects and risks, prioritised

| ID | Severity | Finding | Impact | Recommendation |
|---|---|---|---|---|
| RISK-1 | ~~BLOCKER~~ **RESOLVED in Phase 9** | PII policy unresolved: `include_raw` exposed name/phone/address to `BANK_TECHOPS`; raw reads unaudited | Ops staff could read borrower contact data with no trace | **Done:** `bank_feed.pii.read` (BANK_ADMIN only) now gates raw fields, every successful read writes a `DATA_EXPORT` audit row, and the read fails closed if that row cannot be persisted. See `docs/PHASE9-PROPOSED-MIGRATION.md` for the one remaining parity item. Formal sign-off on the BANK_ADMIN-only scope is still outstanding (`docs/PILOT-PREFLIGHT.md` A10) |
| RISK-2 | **HIGH (gap)** | **No operational alerting.** `services/bank/alerts.py` is portfolio-risk alerting in the bank UI; no task notifies on failure | A stuck batch, rejected feed or blocked night is invisible until someone runs a query | Accept with a **manual daily check** of §3 of the runbook for the pilot, or wire one alert sink |
| RISK-3 | MEDIUM (gap) | **No stage durations recorded.** Nightly window never measured in production | Overrun cannot be detected or trended | Add timing to the three stages; asserted by a test so it cannot be assumed fixed |
| DEFECT-8.1 | LOW | Two concurrent **CLI** ingests of one file race on `uq_bank_feed_rows_batch_id_row_no`; losers raise `UniqueViolation` → `PendingRollbackError` | Measured: 4 concurrent → **3 raised, 1 succeeded**, book stayed correct (1 loan, 1 customer, 1 staging row) | **Not a pilot blocker.** The Phase 5 claim prevents it on the scheduled path; only simultaneous manual CLI runs trigger it. Runbook says "one at a time". Smallest fix: catch `IntegrityError` in `_quarantine`/staging-row insert and re-read, same pattern `upload_feed` already uses |
| OPS-8.2 | LOW (info) | Uploaded feeds must carry `agency_code` per row or rows are held `NO_AGENCY` | Pilot bank's first file could be entirely quarantined | Add to the pilot file-format checklist |
| OPS-8.3 | LOW (info) | A new task is not dispatchable until the **worker restarts** (`include` is boot-time) | Beat fires, dispatch fails `NotRegistered` | Runbook §2 makes restart + registration check mandatory after deploy |

**Not verified (no claim made):** a real 60-minute stale-claim wait; volumes
above 2,000 rows; the UPDATE-heavy feed shape; browser walkthrough of the UI
(automated-test verified only); Postgres row-lock behaviour under genuine
multi-process load beyond the thread-level contention tested here.

---

## 9. Decision

**READY FOR CONTROLLED PILOT — one bank — conditional on the operator
explicitly accepting RISK-1 and RISK-2.**

Rationale: every requirement bearing on **data integrity and tenant safety**
was verified on real PostgreSQL and passed — concurrency (8 scenarios), a real
`SIGKILL` with recovery and no duplicate side effects, release authorisation
and cross-bank isolation over real HTTP, dependency gating before allocation,
and measured throughput with margin at pilot volume. No duplicate loan, case or
placement was produced in any scenario, and no unexpected 500 was observed.

The two conditions are **not** engineering defects:

- **RISK-1** is a policy question only the data owner can answer. Per the
  brief, an unclear PII policy is reported as unresolved rather than assumed
  acceptable — so the pilot should not start until it is answered.
- **RISK-2** is a real gap with a workable interim: the runbook's queries are
  verified to detect every condition, so a **daily manual check** substitutes
  for alerting during a one-bank pilot. That is a conscious trade, and the
  operator should accept it knowingly rather than inherit it silently.

Recommended pilot guardrails: one bank, ≤10,000 rows/night (re-measure above
~25,000), the runbook check run daily, and DEFECT-8.1 avoided by never running
two manual ingests at once.
