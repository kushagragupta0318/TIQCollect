# Uncommitted changes — review summary

**Branch:** `TIQCollect-app` · **Written:** 2026-10-16 · **Status:** nothing committed

115 working-tree entries: **55 modified**, **60 untracked** (including this file). Tracked files alone are
**+2,257 / −487** lines.

This summarises what changed and why, grouped by feature rather than by file, so it can be
read before committing. Numbers in it were measured, not estimated; where something is
unverified it says so.

---

## 1. Summary

| Area | What landed |
|---|---|
| **Security** | Cross-tenant leak in the manager's unallocated-case pool, closed |
| **Bank feed** | Upload API → worker → ingestion engine, end to end |
| **Quarantine** | Held/skipped rows are now recorded and reviewable |
| **Manager review** | Per-day Overview (`?on=`) + daily trend chart |
| **Correctness** | "Collected" counted unverified money; two dead ORM columns found |
| **UI** | Brand loader, nav order, agency identity editing, agency-admin pages |
| **Docs** | 15 new pilot/readiness documents (not written here — see §6) |

**Verified at the time of writing:**

```
frontend : npm run build  green
           npm test       964 passed (90 files)
           npm run lint   0 errors, 2 pre-existing warnings
backend  : compileall     clean
           pytest (6 suites, feed + manager + tenancy + planner)
                          173 passed
```

---

## 2. Security

### Cross-tenant leak in the unallocated pool — **fixed**

`GET /manager/cases/unallocated` and `POST /manager/cases/{id}/reassign` reached cases
through `status == UNASSIGNED` / `agent_id IS NULL` with **no tenant predicate**, so every
unplaced case in the database matched — any bank, any agency, with the borrower's name
attached. The write route was equally exposed.

The same defect had already been fixed next door in `planner_service.plan_next_day` (A04);
the fix never reached this router.

**Why three safety nets missed it:**

1. The textual sweep (`test_every_manager_route_that_reads_tenant_data_is_scoped`) found
   `_scoped_agent_ids` in the handler — used for the *agents* query, not the case query
   underneath. The test's own comment predicted exactly this failure mode.
2. `test_cross_tenant_behavioral`'s fixture only ever created **ASSIGNED** cases, so the
   `agent_id IS NULL` branch was never exercised.
3. The reassign route's comment justified itself with *"visible to every manager through
   GET /cases/unallocated"* — circular reasoning resting on the read bug.

**Fix:** both queries now go through `services/scope.cases_in_scope` — the frozen canonical
rule (ADR 0001), which already covers *"the agency's UNASSIGNED pool … never another
agency's pool"* and handles AGENCY_ADMIN correctly.

**Files:** `api/v1/endpoints/manager.py`, `tests/test_unallocated_cases_tenancy.py` (new, 290 lines)

**Evidence:** 13 tests. **9 of 13 fail against the pre-fix code**, all 13 pass after.

---

## 3. Bank feed pipeline

### 3.1 Upload → worker handoff — **new**

`POST /bank/feed` stored a file and wrote a batch at `RECEIVED`, then **nothing consumed
it**. `run_ingestion` had exactly one caller in the repo: the CLI's own `main()`. The loan
book did not change until somebody ran the script by hand.

`workers/tasks/feed_ingestion.py` (369 lines) closes that gap:

- **Atomic claim** — one `UPDATE … WHERE status='RECEIVED'`. Whichever worker's rowcount is
  1 owns the batch; duplicate deliveries get 0 and return. Celery is at-least-once
  (`task_acks_late`), so this, not the broker, is what makes redelivery safe.
- **Bank identity from the persisted batch row**, never from task arguments.
- **Typed retries** — transient (storage/DB) retries with backoff to a bound; permanent
  (bad file, missing object) is terminal `REJECTED` immediately.
- **`recover_stranded_batches`** every 15 min: redispatches batches whose dispatch was lost,
  and reclaims `VALIDATING` batches abandoned by a dead worker.

**Delivery is at-least-once with idempotent processing — not exactly-once.** Replay safety
rests on the engine converging (payments gated behind `ALREADY_RESOLVED`, quarantine dedup
on `(batch_id, row_no)`), which was verified empirically by ingesting the same file twice
and comparing database state.

### 3.2 Defects found while building it

| Defect | Impact | Status |
|---|---|---|
| `IngestionError` decorated `@dataclass` | Generated `__init__(self)`, shadowing `Exception.__init__` — **every `raise IngestionError("…")` died with a TypeError** instead of the refusal it meant | Fixed |
| `getattr(batch, "id")` after `db.close()` | `DetachedInstanceError` (which `getattr` does not swallow) — **crashed every successful run on its last line**, CLI included | Fixed |
| `from scripts.ingest_daily import …` in the worker | `ModuleNotFoundError` — pytest inserts rootdir into `sys.path`, Celery does not. **Every uploaded feed retried 3× and was rejected.** Found only by running it against a real worker | Fixed |
| `SoftTimeLimitExceeded` caught as transient | A 30-min timeout retried 3× — ~90 min of worker time on work that cannot finish | Fixed |
| Staleness measured from `received_at` | Upload time, not claim time: a file uploaded hours before being claimed was instantly "stale", so the sweep could **steal a live worker's batch** | Fixed |
| Unbounded recovery sweep | Dispatched every `RECEIVED` batch at once | Bounded to 50, oldest-first |

The third is worth noting: **the test suite passed 18/18 while the feature was 100% broken
in production.** Only an end-to-end run against the real worker surfaced it.

### 3.3 Skipped rows were invisible — **fixed**

`process_row` can decline a row (e.g. *"missing lat/lon for new customer"*). That incremented
a counter and logged at `log.debug` — **off in production**. Measured on a 500-row feed: the
batch reported `PARTIAL, rows_skipped=500, rows_quarantined=0` with **no row anywhere saying
why**.

`PlacementService.record_skipped()` now writes a `SKIPPED` row — the status the schema
already declared and nothing used. Deliberately *not* `QUARANTINED`: release re-tries rows a
person fixed, and an engine-level skip is not that. `release_rows` filters on `QUARANTINED`,
so skipped rows are non-releasable by construction.

**Files:** `services/placement_service.py`, `scripts/ingest_daily.py`,
`tests/test_bank_feed_quarantine.py`

---

## 4. Manager per-day review

### 4.1 "Collected" counted money that was never collected — **fixed**

`/manager/dashboard` and `/manager/agents/live-status` each summed `Payment.amount` with **no
status filter** — two byte-identical copies. `Payment.status` defaults to
`PENDING_VERIFICATION`, so unverified, rejected and reversed money all counted.

Measured on the demo book: **+1.7% on average, +11% on 2026-09-22** (₹75,160 of PENDING on
₹6.85 L real).

VERIFIED-only is the product's existing rule — six other aggregations in the same file
already filter it, and **ADR 0015** states a reversal removes the amount from
`case.collected_amount` and that `verified_paid_against` counts VERIFIED only.

The figure had **zero test coverage**, which is how it survived. Now `_collected_between()`,
one definition, used by both sites.

> **Known inconsistency, deliberately out of scope:** the agent/DPD breakdown still filters
> `!= REJECTED`, so it counts PENDING and REVERSED. It will disagree with the Overview.

### 4.2 New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /manager/dashboard?on=YYYY-MM-DD` | Any past day. Omitted → behaviour unchanged |
| `GET /manager/performance-series?days=30\|60` | Daily target / collected / visits |

Future and malformed dates return **422**, never a silent fallback to today — answering a
different day than the one asked is how a reviewer trusts the wrong numbers.

**`agents_on_duty` cannot be historical.** `workforce.agents.status` is a single mutable
column with no history. For a past date the response returns who *held a beat* that day and
sets `agents_metric: "WITH_PLAN"`, so the UI relabels the tile **"Agents With A Plan"**
rather than showing today's roster under an old date.

### 4.3 Data sources — and two dead columns

The series reads **`planning.beats`** for target/planned and **`collections.payments`
(VERIFIED)** for collected.

It deliberately does **not** read `beats.amount_collected` or `beats.cases_completed`. Those
are written **only by seed and demo scripts** — `payment_service` and `visit_service` never
touch a Beat, and the 02:00 `beat_reconciliation` fills only distance/duration. On a real
book they stay `0` forever. Reading them would plot a flat zero line and call it performance.

> **Open item:** those two columns are dead in production. Either wire them into
> `beat_reconciliation` or drop them — a separate decision.

### 4.4 UI

- **`PerformanceTrendCard`** — grey target area, blue collected line, one shared rupee axis
  (collected reads as a *share* of target; a dual axis would hide that). 30/60-day toggle.
  Placed directly under the collection tile: tiles say what today did, the chart says whether
  that is normal.
- **`ReviewDatePicker`** — calendar icon in the header beside the notification bell. A
  "Today" affordance appears **only once you have left today**. Opens an inline month grid;
  the native `<input type="date">` was replaced because it rendered a second floating panel
  outside the popover and looked different per OS.
- **`ReviewDayContext`** — the reviewed day, shared between the layout header and the page
  across `<Outlet />`. Split into `useReviewDay.ts` following the existing
  `BeatContext`/`useBeat` precedent.

---

## 5. UI and consistency

| Change | Detail |
|---|---|
| **Brand loader** | Boot splash in `index.html` (the pre-mount window was a white screen), lazy-route fallback, and post-login holds for **both** manager and agent — AgentLayout had no gate and flashed an empty shell |
| **Header hover** | `hover:bg-muted` on the bell and calendar **never fired** — both set `background` inline, which outranks a utility class. `.header-icon-btn` fixes both |
| **Nav order** | Compliance moved last for every manager role |
| **Agents page** | Manager filter restyled to match its neighbours; summary cards now follow the filter instead of showing whole-agency figures above a filtered table |
| **Month-on-month** | Agents with no history were silently dropped (4+11+0 shown against 18). Now counted and labelled |
| **Performance index** | `Math.round` collapsed 15 distinct values to 5 — the top five all printed "7". Comparison tables now show one decimal |
| **Agency identity** | Edit dialog on the bank's agency profile (BANK_ADMIN only — `agency.update`) |
| **Audit Trail card** | Removed from the agency Compliance page; it lives on the bank side |
| **MonteCarloPage test** | Two failures fixed — the 2026-10-08 chart work rendered the same figure twice, so `findByText` became ambiguous |

---

## 6. Not mine / pre-existing

Present in the tree but **not written or reviewed in this session**:

- `services/bank/feed_quarantine_service.py`, `api/v1/endpoints/bank_feed_quarantine.py`
- `services/pipeline_readiness.py`, `workers/tasks/feed_cutoff.py`
- `tests/phase8/` (10 files), `test_pipeline_scheduling.py`, `test_feed_ingestion_task.py`
- Frontend: `QuarantineReview.tsx`, `LoanFeedPage.tsx`, `ManagerOnboarding.tsx`,
  `AgencyManagersPage.tsx`, `AddManagerDrawer.tsx`
- **15 pilot/readiness docs** under `docs/` (FINAL-AUDIT, PHASE8-11, PII-POLICY-SIGNOFF,
  PILOT-* )
- `caddy-local-root.crt`

Listed so the boundary is explicit — this document does not vouch for them.

---

## 7. Open items

| # | Item | Severity |
|---|---|---|
| 1 | **Demo book has no payments after 2026-09-21** while beats run to 10-17, so the trend chart plots target against flat zero. Needs `demo_record_visits.py` run — not done, needs approval | Demo only |
| 2 | **Throughput measured at 30 rows/sec** on the successful path (dev hardware, single worker, one run). A 60k-row feed ≈ 33 min, approaching `STALE_CLAIM_MINUTES = 60` | Medium |
| 3 | **Worker + beat must be restarted after deploy.** Proven necessary: beat ran 7 hours without ever scheduling a newly added task | **Deployment blocker** |
| 4 | `beats.amount_collected` / `cases_completed` dead in production | Low |
| 5 | Agent/DPD breakdown still `!= REJECTED` | Low |
| 6 | `sys.path` insertion for the `scripts` import is a workaround | Low |

**Not verified:** worker crash mid-ingestion under load, throughput on production hardware,
volumes above 1,000 rows, long-term beat stability.

---

## 8. Deployment

**No migration** for any of the above — `claimed_at` and `SKIPPED` use existing columns and
JSON fields. (`v2_0040` is an unrelated untracked migration for agent experience fields.)

1. Deploy code
2. **Restart worker AND beat** — see open item 3
3. Confirm `celery inspect registered` lists `ingest_feed_batch` and `recover_stranded_batches`
4. Confirm a `feed_ingestion.recovery` log line appears within 15 minutes
5. Alert on `RECEIVED` batches older than 30 min, and on any `REJECTED`

**One behaviour change is visible:** "Collected today" drops by up to 11% on some days. That
corrects an overstatement — worth telling whoever reads that number before they notice it.
