# Bank feed pipeline — operator runbook

Scope: the nightly feed chain and the quarantine release workflow (Phases 3–7).
Written for the operator on duty, not for the people who built it.

Verified on 2026-10-16 against real PostgreSQL. What was measured, and what was
not, is in `docs/PHASE8-ACCEPTANCE.md` — read that before a pilot.

---

## 1. The nightly chain

| IST | Task | What it does | If it does not run |
|---|---|---|---|
| 19:30 | `feed_cutoff.check_feed_cutoff` | **Checks** the day's feed arrived and finished. Fetches nothing. | No verdict logged. The 20:00 gate still protects the book. |
| 19:45 | `repayment_scoring.run_nightly_repayment_scoring` | Scores the book, writes snapshots. Skips if ingestion is not done. | Allocation blocks at 20:00 (scoring stage not ready). |
| 20:00 | `allocation.run_nightly_allocation` | Plans tomorrow per manager. **Refuses** if either upstream stage is missing. | Agents have no beats at 06:00. Check for `BLOCKED_UPSTREAM` rows. |
| every 15 min | `feed_ingestion.recover_stranded_batches` | Re-dispatches stranded `RECEIVED`; reclaims `VALIDATING` held > 60 min. | A lost dispatch sits unprocessed. |

Ingestion itself is **not** scheduled. Banks push a file to `POST /bank/feed`,
which ingests on arrival; `python -m scripts.ingest_daily` is the manual path.

---

## 2. After every deployment

Run these three, in order. The first is not optional: `include` is applied at
worker boot, so a newly added task is **not dispatchable until the worker
restarts**, even though the code is on disk.

```bash
# 1. restart worker AND beat
docker compose restart celery_worker celery_beat

# 2. confirm the pipeline tasks registered (expect all four)
docker compose exec -T celery_worker \
  celery -A app.workers.celery_app inspect registered --timeout 20 \
  | grep -E 'feed_cutoff|feed_ingestion|repayment_scoring|run_nightly_allocation'

# 3. confirm beat holds the IST schedule
docker compose exec -T celery_beat python -c "
from app.workers.celery_app import celery_app as c
s = c.conf.beat_schedule
for k in ('feed-cutoff-check','nightly-repayment-scoring','nightly-ml-allocation','bank-feed-recovery'):
    print(k, s[k]['schedule'])
print('tz:', c.conf.timezone)"
```

Step 2 must list `app.workers.tasks.feed_cutoff.check_feed_cutoff`. If it does
not, beat will fire at 19:30 and the dispatch will fail with
`NotRegistered` — the worker is running old code.

### Confirming the recovery sweep runs on cadence

```bash
# fires at :00 :15 :30 :45. Watch across one boundary:
docker compose logs -f --since 1m celery_worker | grep -i recover_stranded
```

Expect one `feed_ingestion.recovery` line per quarter hour, with
`reclaimed_stale` and `dispatched` counts. Silence across two boundaries means
beat is not running or the worker is not consuming.

---

## 3. Monitoring queries

Every query below was run against real PostgreSQL with a row deliberately put
into the state it hunts (`tests/phase8/test_monitoring_queries.py`). Replace
`:b` with the bank id, or drop that clause to sweep all banks.

**There is no alerting.** These are queries an operator or a scheduled job must
run; nothing in the product pages anyone. See the gap in
`docs/PHASE8-ACCEPTANCE.md` §Defects.

### A batch stuck at RECEIVED (dispatch lost, > 30 min)

```sql
select id, business_date, received_at,
       round(extract(epoch from (now() - received_at)) / 60) as minutes_waiting
from lending.bank_feed_batches
where status = 'RECEIVED' and received_at < now() - interval '30 minutes'
order by received_at;
```

Expected to be empty: the sweep re-dispatches within 15 minutes. A row older
than 30 minutes means the sweep is not running — check §2.

### A stale VALIDATING claim (worker died holding it)

```sql
select id,
       coalesce((dq_report->>'claimed_at')::timestamptz, received_at) as claimed_at,
       round(extract(epoch from (now() - coalesce((dq_report->>'claimed_at')::timestamptz,
                                                  received_at))) / 60) as minutes_held
from lending.bank_feed_batches
where status = 'VALIDATING'
  and coalesce((dq_report->>'claimed_at')::timestamptz, received_at)
      < now() - interval '60 minutes';
```

60 minutes is `STALE_CLAIM_MINUTES`. A row here should be reclaimed by the next
sweep; if it persists, the sweep is not running.

### Rejected batches, with the reason

```sql
select id, business_date, dq_report->>'ingestion_error' as failure
from lending.bank_feed_batches
where status = 'REJECTED'
order by received_at desc;
```

### A blocked or failed allocation night

```sql
select plan_date, manager_user_id,
       summary_metadata->>'outcome'   as outcome,
       summary_metadata->'blocked_by' as blocked_by,
       summary_metadata->>'error'     as error
from planning.allocation_runs
where status = 'FAILED'
order by created_at desc limit 50;
```

`outcome = 'BLOCKED_UPSTREAM'` → the night was **refused** before planning
(inputs missing). Anything else with an `error` → planning ran and crashed.
The two need different responses; see §5.

### Duplicate placements (must always be empty)

```sql
select loan_id, count(*) as active_placements
from collections.placements
where status = 'ACTIVE'
group by loan_id having count(*) > 1;
```

Any row is a **data-integrity incident**. Stop releasing, capture the rows, and
escalate — a loan placed with two agencies means two teams chasing one borrower.

### Quarantine backlog by reason

```sql
select coalesce(dq_errors->-1->>'reason','UNKNOWN') as reason, count(*) as rows_held
from lending.bank_feed_rows
where status = 'QUARANTINED'
group by 1 order by 2 desc;
```

### Stage durations — NOT AVAILABLE

No table records how long each stage took. `allocation_runs` has
`created_at`/`updated_at` only, which bound a run loosely and not per stage. The
nightly window has therefore never been measured in production. Treat any
statement about overrun as an estimate until this is instrumented.

---

## 4. Reading a batch's state

| Status | Means | In the loan book? |
|---|---|---|
| `RECEIVED` | Staged and recorded. Queued or awaiting the sweep. | **No** |
| `VALIDATING` | A worker holds it and is processing. | Partially, mid-run |
| `LOADED` | Finished; every row accepted. | Yes |
| `PARTIAL` | Finished; some rows skipped or quarantined. | Yes, minus held rows |
| `REJECTED` | Refused. Terminal. | **No** |

`RECEIVED` is never "ingested". The UI says "queued for processing" on purpose.

---

## 5. When something is wrong

### A batch is stuck at RECEIVED

1. Is the worker up? `docker compose ps celery_worker`
2. Are the tasks registered? §2 step 2.
3. Is the sweep firing? §2.
4. **Do not re-upload.** The row is the queue of record. Force it through:

```bash
docker compose exec -T celery_worker python -c "
from app.workers.tasks.feed_ingestion import ingest_feed_batch
print(ingest_feed_batch.delay('<batch-id>'))"
```

Re-uploading the identical file returns `duplicate: true` and the same batch —
harmless but it tells you nothing new. Re-uploading a *changed* file creates a
second batch for the same day, which is a real mess to unpick.

### A batch is REJECTED

Read the reason (§3). `REJECTED` is terminal and **safe**: nothing entered the
book. Fix the file and upload the corrected version — that is a new batch, and
the correct action.

### Rows are quarantined

Open Tech Ops → Loan Feed → the batch. Each row shows the recorded reason.

- A **placement refusal** (`NO_CONTRACT_IN_FORCE`, `CONTRACT_FULL`,
  `NOT_AUTHORISED`, `NO_AGENCY`, …) → fix the cause (sign the contract,
  authorise the slab), then **Release**. Release re-runs the real placement
  rules; a row whose cause is unfixed is refused again and stays held. Nothing
  is forced through.
- `UNKNOWN_BRANCH` → **not releasable**. The row never created a loan, so there
  is nothing to place. Add the branch to the bank, then re-ingest the file.

Releasing is safe to retry: a row already released reports
`ALREADY_RELEASED` and places nothing a second time (verified under concurrency).

### Allocation was blocked

Query §3. Then:

- `blocked_by: ["ingestion"]` → the feed never finished. Fix per above. Once a
  batch reaches `LOADED`/`PARTIAL`, re-run scoring then allocation.
- `blocked_by: ["scoring"]` → ingestion was fine, scoring did not run.
- Both → nothing ran that evening; check the worker was up at 19:30.

Re-running the night by hand, in order:

```bash
docker compose exec -T celery_worker python -c "
from app.workers.tasks.repayment_scoring import run_nightly_repayment_scoring
print(run_nightly_repayment_scoring.delay())"

# then, once snapshots exist:
docker compose exec -T celery_worker python -c "
from app.workers.tasks.allocation import run_nightly_allocation
print(run_nightly_allocation.delay())"
```

Both are gated, so they refuse rather than act on absent inputs. To override
deliberately (an operator decision, with the reason recorded in the ticket):
`run_nightly_allocation.delay(force=True)`.

Naming an explicit date — `run_nightly_allocation.delay(plan_date_str='2026-10-20')`
— also bypasses the gate, because a dated run is aimed at another day. Never
pass a date just to get past a block.

### A worker died mid-ingest

No action needed within the hour. The claim survives the crash, uncommitted
work is rolled back by Postgres, and the sweep reclaims the batch after 60
minutes and re-dispatches it. Re-processing is safe: rows upsert on business
keys, so a retry converges rather than duplicating (verified with a real
SIGKILL). To shorten the wait, reclaim by hand:

```sql
update lending.bank_feed_batches set status='RECEIVED'
where id = '<batch-id>' and status = 'VALIDATING';
```

Only do this when you have confirmed no worker still holds it
(`celery -A app.workers.celery_app inspect active`).

---

## 6. The daily manual check (one-bank pilot)

**There is no automated alerting.** For this pilot the accepted policy is a
daily manual pass through this checklist. It replaces alerting; if it is not
run, nobody finds out about a stuck feed until agents have no work.

Run it **once per working day, after 20:15 IST** — i.e. after allocation has
had its turn. Takes about five minutes.

| # | Check | Query / command | Expected |
|---|---|---|---|
| 1 | Worker and beat alive | `docker compose ps celery_worker celery_beat` | both `running` |
| 2 | `feed_cutoff` registered | §2 step 2 | listed |
| 3 | Recovery sweep ran | `docker compose logs --since 30m celery_worker \| grep -i recover_stranded` | ≥1 line per 15 min |
| 4 | No batch stuck at `RECEIVED` >30 min | §3 query 1 | 0 rows |
| 5 | No stale `VALIDATING` claim | §3 query 2 | 0 rows |
| 6 | No `REJECTED` batch today | §3 query 3 | 0 rows, or each one explained |
| 7 | Scoring ran for today | snapshot count for today's business date | >0 |
| 8 | No blocked/failed allocation night | §3 query 4 | 0 rows |
| 9 | No duplicate active placements | §3 query 5 | **0 rows — always** |
| 10 | Quarantine backlog reviewed | §3 query 6 | each reason triaged or accepted |

Record the date, who ran it, and the outcome of any non-zero result. A check
with no recorded outcome counts as not run.

### Escalation

1. **Check 9 returns any row** — a loan placed with two agencies. Stop releasing
   rows, capture the output, escalate immediately. This is a data-integrity
   incident, not a backlog item.
2. **Checks 4, 5 or 3 fail** — the worker or beat is not doing its job. Follow
   §2, then §5. Do not re-upload the feed.
3. **Check 8 returns a blocked night** — agents will have no beats tomorrow
   unless it is fixed before 06:00 IST. Follow §5 "Allocation was blocked".
4. **Check 6 returns a rejected batch** — nothing entered the book, so it is
   safe; fix the file and upload the corrected version.

### UNRESOLVED: no named operator

This runbook does **not** name who performs the daily check, who is on call, or
who is escalated to. No such role exists in the project today, and inventing a
name here would be worse than the gap.

**Status: PENDING OPERATIONAL INPUT.** Until the table below is completed, the
pilot's monitoring is **unassigned**. Because these manual checks *replace*
automated alerting for this pilot, an unassigned check means the pilot has no
monitoring at all — not weaker monitoring. This is a condition on starting, not
a detail to settle afterwards.

### Ownership assignment (to be completed before the first feed)

| Role | Name | Contact | Hours / timezone |
|---|---|---|---|
| **Primary daily-check operator** | ____________ | ____________ | ____________ |
| **Backup operator** (when the primary is away) | ____________ | ____________ | ____________ |
| Escalation — data-integrity incident (duplicate placements) | ____________ | ____________ | ____________ |
| Escalation — pipeline failure (stuck/rejected feed, blocked night) | ____________ | ____________ | ____________ |
| Authority to approve a rollback | ____________ | ____________ | ____________ |

| Item | Value |
|---|---|
| **Where each day's record is filed** (the §6a form) | ____________________ |
| Where incidents are recorded (ticket system / reference format) | ____________________ |
| Who reviews the records weekly, if anyone | ____________________ |
| What happens if the check is missed for a day | ____________________ |
| Agreed start date for daily checks | ____________________ |

**Assignment confirmed by**

| Field | Value |
|---|---|
| Name | ____________________ |
| Role / authority to assign operators | ____________________ |
| Date | ____________________ |
| Decision | ☐ Assignments above are confirmed ☐ Not yet assigned |

Three fields are mandatory before the pilot may start: **primary operator**,
**backup operator**, and **where each day's record is filed**. A primary with
no backup is a single point of failure on the pilot's only monitoring; a record
with no filing location cannot be audited or reviewed.

---

## 6a. Daily check record (copy one per day)

Copy this block, fill it in, and file it wherever §6 says the outcome is
recorded. A check with no completed record **counts as not run** — the record
is the only evidence the pilot has monitoring at all.

```
───────────────────────────────────────────────────────────────────────
TIQCollect feed pipeline — daily check record

Date run (IST)        : ____-__-__   Time: __:__
Business date checked : ____-__-__
Primary operator      : ____________________
Backup present        : ____________________

 #  Check                                        Result        Notes
 1  Worker + beat running                        PASS / FAIL   ____________
 2  feed_cutoff registered                       PASS / FAIL   ____________
 3  Recovery sweep ran (>=1 per 15 min)          PASS / FAIL   ____________
 4  RECEIVED batches older than 30 min           count: ____   ____________
 5  Stale VALIDATING claims (>60 min)            count: ____   ____________
 6  REJECTED batches today                       count: ____   ____________
 7  Scoring ran for the business date            PASS / FAIL   ____________
 8  Blocked/failed allocation nights             count: ____   ____________
 9  Duplicate ACTIVE placements (MUST be 0)      count: ____   ____________
10  Quarantine backlog triaged                   PASS / FAIL   ____________

Non-zero or FAIL results — action taken:
  ________________________________________________________________
  ________________________________________________________________

Escalated?            ☐ no  ☐ yes -> to: ____________ at __:__
Incident reference    : ____________________
Raw-PII reads today   : count ____  (runbook §7 query; unexpected? ☐ no ☐ yes)

Completed by          : ____________________  Signature: ____________
───────────────────────────────────────────────────────────────────────
```

**Check 9 is the stop-the-line one.** Any non-zero count means a loan is placed
with two agencies. Stop releasing rows, capture the query output, escalate
immediately, and record the incident reference above.

---

## 7. Raw borrower PII

The quarantine screens show the business keys a reviewer needs (account number,
customer ref, case number, reason). The borrower's **name, phone, address and
masked PAN** are a separate thing: they come back only when
`include_raw=true` is requested **and** the caller holds `bank_feed.pii.read`.

- That capability is held by **BANK_ADMIN only**. `BANK_TECHOPS` and the
  `SERVICE` account do **not** hold it.
- Tech Ops keeps the entire review and release workflow. They simply never
  receive borrower contact details.
- **Every successful raw read writes a `DATA_EXPORT` audit row** naming the
  actor, the bank, the batch, the number of rows disclosed and which fields
  were permitted — never the values.
- If the audit row cannot be written, the raw data is **withheld** (fail
  closed). The response says `raw_pii_withheld_reason: "audit_unavailable"`.

To review who has read raw borrower data:

```sql
select created_at, user_id, details->>'batch_id' as batch,
       details->>'rows_disclosed' as rows, details->>'endpoint' as endpoint
from audit.audit_logs
where action = 'DATA_EXPORT' and details->>'access' = 'raw_pii_read'
order by created_at desc limit 100;
```

Do not grant `bank_feed.pii.read` to a role to "unblock" someone mid-incident.
Reviewing a held row never requires it.

---

## 8. Never do these

- **Never** change `STALE_CLAIM_MINUTES` to speed up a recovery. 60 minutes is
  deliberately longer than the task's own ceiling so a slow-but-alive worker is
  never robbed mid-ingest.
- **Never** flip a batch to `LOADED` by hand to make a gate open. It lies to
  every downstream reader, and allocation will plan on a book that was never
  written.
- **Never** run two CLI ingests of the same file at once — they race on the
  staging rows and most will fail (DEFECT-8.1). One at a time.
- **Never** enable `DEMO_OTP_ECHO` anywhere a borrower or outsider can reach.
