# Scheduled-night observation record

**Status: NOT VERIFIED — no real scheduled night has been observed.**

The 19:30 → 19:45 → 20:00 IST sequence has been verified only by unit tests,
manually triggered tasks and simulated timestamps. **None of those is proof of a
real scheduled night.** This form exists to capture one.

Why it cannot be substituted:

| What was done | What it proves | What it does NOT prove |
|---|---|---|
| Unit/integration tests | the gate logic and state machine | that beat fires at 19:30/19:45/20:00 in this environment |
| Manually triggered tasks (`.delay()`, `.run()`) | the task body works | that the schedule dispatches it |
| Backdated timestamps (stale-claim) | the comparison the sweep makes | that a real 60-minute expiry behaves the same |
| Recovery sweep observed on a `:15` boundary | beat *can* dispatch on time | that the three nightly stages do |

Run this on a **non-production dataset** before the first real feed. Do not
change the schedule or bypass a gate to force a green result: a night that
legitimately blocks is a **successful** observation of the gate working, and
should be recorded as such.

---

## Run record (copy one per observed night)

```
═══════════════════════════════════════════════════════════════════════
TIQCollect — scheduled nightly run observation

Environment            : ____________________  (not production unless approved)
Database (current_database()) : ____________________
Observer               : ____________________
Date of observation    : ____-__-__
Business date expected : ____-__-__   (IST; see pipeline_readiness.business_date)

PRE-CONDITIONS
  Worker + beat running            ☐      Image tag / release : ____________
  feed_cutoff registered           ☐      alembic current     : ____________
  Feed uploaded for this date?     ☐ yes ☐ no    Upload time (IST): __:__
  Rows in the uploaded file        : ______

─── STAGE 1 — feed cutoff ───────────────────────────────────────────
  Scheduled (IST)        : 19:30
  Actual dispatch (IST)  : __:__:__      Actual completion : __:__:__
  Beat log line seen     ☐   Worker log line seen ☐
  Verdict reported       : ☐ ready  ☐ not ready — reason: ____________
  Batches found          : ______   in-flight: ______  rejected: ______

─── STAGE 2 — repayment scoring ─────────────────────────────────────
  Scheduled (IST)        : 19:45
  Actual dispatch (IST)  : __:__:__      Actual completion : __:__:__
  Outcome                : ☐ scored  ☐ skipped (upstream not ready)
  If skipped, reason     : ____________________
  Input freshness: business date used : ____-__-__  (matches expected? ☐)
  Loans scored           : ______    Snapshots written : ______

─── STAGE 3 — nightly allocation ────────────────────────────────────
  Scheduled (IST)        : 20:00
  Actual dispatch (IST)  : __:__:__      Actual completion : __:__:__
  Outcome                : ☐ planned  ☐ BLOCKED_UPSTREAM  ☐ failed
  If blocked, blocked_by : ____________________
  Business date used     : ____-__-__    Plan date (target): ____-__-__
  Managers planned       : ______   Cases allocated : ______
  Agents planned         : ______   Runs FAILED     : ______

─── WINDOW ──────────────────────────────────────────────────────────
  19:30 → 20:00 elapsed  : ______ min
  Any stage overran its slot?      ☐ no ☐ yes — which: ____________
  Longest stage                    : ____________ ( ______ min )

─── ERRORS, BLOCKS AND RECOVERY ─────────────────────────────────────
  Errors observed:
    ______________________________________________________________
  Stages blocked (and whether correctly):
    ______________________________________________________________
  Recovery actions taken:
    ______________________________________________________________
  Recovery sweep fired during the window? ☐ yes ☐ no   count: ____

─── POST-CONDITIONS ─────────────────────────────────────────────────
  Batch final status     : ______________  (LOADED / PARTIAL / other)
  Rows accepted / quarantined / skipped : ______ / ______ / ______
  Duplicate ACTIVE placements (MUST be 0): ______
  Beats visible to agents next morning   ☐ yes ☐ no ☐ n/a
  Raw-PII DATA_EXPORT rows during window : ______ (expected: 0)

─── EVIDENCE ────────────────────────────────────────────────────────
  Beat log excerpt saved at   : ____________________
  Worker log excerpt saved at : ____________________
  allocation_runs query output: ____________________
  Screenshots / ticket ref    : ____________________

─── SIGN-OFF ────────────────────────────────────────────────────────
  Observation outcome : ☐ all three stages ran as scheduled
                        ☐ ran, with a correctly-blocked stage
                        ☐ did NOT run as scheduled — investigate
  Observer            : ____________  Signature: ____________  Date: ______
  Reviewed by         : ____________  Signature: ____________  Date: ______
═══════════════════════════════════════════════════════════════════════
```

## How to collect the evidence

```bash
C="docker compose"

# beat's dispatch decisions across the window
$C logs --since 60m celery_beat | grep -E "Sending due task (feed-cutoff|nightly-)"

# the worker's execution of each stage
$C logs --since 60m celery_worker | grep -E "feed_cutoff|repayment_scoring|nightly_allocation"
```

```sql
-- what allocation recorded for the night
select plan_date, status,
       summary_metadata->>'outcome'      as outcome,
       summary_metadata->'blocked_by'    as blocked_by,
       total_cases_allocated, total_agents_planned, created_at
from planning.allocation_runs
where created_at >= now() - interval '1 day'
order by created_at desc;

-- the batch the night read
select id, business_date, status, rows_total, rows_accepted,
       rows_quarantined, rows_skipped, loaded_at
from lending.bank_feed_batches
where business_date = :business_date;

-- scoring's own evidence
select as_of_date, count(*) as snapshots
from ml.repayment_score_snapshots
where as_of_date = :business_date group by 1;

-- the invariant that must always hold
select loan_id, count(*) from collections.placements
where status = 'ACTIVE' group by 1 having count(*) > 1;
```

## Acceptance

At least **one** night must be recorded with all three stages dispatched at
their scheduled IST times before the pilot takes a real feed.

A night where allocation correctly **blocked** (because no feed arrived) proves
the schedule and the gate, but **not** the full success path. Record it, then
observe a second night with a feed present.

| Field | Value |
|---|---|
| Nights observed | **0** |
| First successful full-path night | ☐ not yet |
| Gate F status | **NOT VERIFIED** |
