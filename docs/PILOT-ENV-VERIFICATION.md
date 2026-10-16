# Pilot-environment verification plan

**Prepared:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`

**STATUS: THIS IS A DOCUMENTED PROCEDURE, NOT A RECORD OF EXECUTION.**
Nothing below has been run in a pilot environment. Every result column is
empty. Phase 8 and 9 executed the equivalent checks on the **dev stack** and on
an isolated `fieldops_phase8` database; that evidence is cited where it exists,
and it is **not** a substitute for running these here.

Do not deploy to production or ingest live bank data by following this document
alone — the deployment procedure is `docs/DEPLOY.md` §5, and it requires its own
approval.

---

## Step 0 — Identify the database BEFORE anything else

**Do not skip this.** Every later step writes or restarts something. Confirm
which database you are pointed at first.

```bash
# What the application will actually use:
docker compose exec -T api sh -lc 'python -c "
from app.core.config import settings
import re
print(re.sub(r\":[^:@/]+@\", \":***@\", settings.DATABASE_URL))"'

# What the server reports:
docker compose exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select current_database();"'
```

| Field | Value |
|---|---|
| Environment name | ____________________ |
| Expected database name | ____________________ |
| Reported `current_database()` | ____________________ |
| Match? | ☐ yes ☐ **no → STOP** |
| Verified by / date | ____________________ |

If these disagree, stop. Everything after this assumes you are on the intended
database.

## Step 1 — Backups and the deployment procedure

Do not restart or deploy before a restorable backup exists.

| # | Item | Reference | Result |
|---|---|---|---|
| 1.1 | Backup script installed on the host | `docs/DEPLOY.md` §6 | ☐ |
| 1.2 | A backup has been taken **today** | §6 | ☐ |
| 1.3 | The backup has been **test-restored** at least once | §6 | ☐ |
| 1.4 | Pre-migration dump taken (`deploy-backup.sh`) | §5 | ☐ |
| 1.5 | Deployment procedure reviewed and approved for this release | §5 | ☐ |
| 1.6 | Rollback procedure read and understood | §7 | ☐ |
| 1.7 | Alembic head matches the release (`alembic current` = `alembic heads`) | — | ☐ |

A backup that has never been restored is an assumption, not a backup.

## Step 2 — Restart worker and beat, and confirm registration

**This step is mandatory, not optional.** Celery applies its `include` list at
worker boot, so a task added by a release is **not dispatchable until the worker
restarts** — beat will fire at 19:30 and the dispatch will fail `NotRegistered`.

```bash
# 2a. restart, per the existing operational procedure (DEPLOY.md §8)
docker compose restart celery_worker celery_beat

# 2b. all pipeline tasks registered? expect all five lines
docker compose exec -T celery_worker \
  celery -A app.workers.celery_app inspect registered --timeout 20 \
  | grep -E 'feed_cutoff|feed_ingestion|repayment_scoring|run_nightly_allocation'

# 2c. beat holds the IST schedule
docker compose exec -T celery_beat python -c "
from app.workers.celery_app import celery_app as c
s = c.conf.beat_schedule
for k in ('feed-cutoff-check','nightly-repayment-scoring','nightly-ml-allocation','bank-feed-recovery'):
    print(k, s[k]['schedule'])
print('tz:', c.conf.timezone)"
```

| # | Expected | Dev-stack evidence | Pilot result |
|---|---|---|---|
| 2.1 | worker + beat `running` | PASS (Phase 8) | ☐ |
| 2.2 | `feed_cutoff.check_feed_cutoff` registered | PASS (Phase 8) | ☐ |
| 2.3 | `feed_ingestion.ingest_feed_batch` registered | PASS | ☐ |
| 2.4 | `feed_ingestion.recover_stranded_batches` registered | PASS | ☐ |
| 2.5 | `repayment_scoring…` + `run_nightly_allocation` registered | PASS | ☐ |
| 2.6 | schedule reads 19:30 / 19:45 / 20:00, tz `Asia/Kolkata` | PASS | ☐ |
| 2.7 | `bank-feed-recovery` is `*/15` | PASS | ☐ |

## Step 3 — Confirm the recovery sweep actually executes

Config is not execution. Watch across a quarter-hour boundary:

```bash
docker compose logs -f --since 1m celery_worker | grep -i recover_stranded
```

Expect one `feed_ingestion.recovery` line per 15 minutes with
`reclaimed_stale` / `dispatched` counts.

| # | Item | Dev-stack evidence | Pilot result |
|---|---|---|---|
| 3.1 | Sweep fired on a `:00/:15/:30/:45` boundary | PASS — `04:45:00.000Z` = 10:15 IST, 24 ms | ☐ |
| 3.2 | Two consecutive boundaries observed | ☐ not done | ☐ |
| 3.3 | `STALE_CLAIM_MINUTES` still 60 | PASS (asserted by test) | ☐ |

## Step 4 — IST business date and the nightly stages

| # | Item | Dev evidence | Pilot result |
|---|---|---|---|
| 4.1 | `business_date()` returns the IST date | PASS | ☐ |
| 4.2 | Container TZ vs IST understood (`date -u` vs `TZ=Asia/Kolkata date`) | PASS | ☐ |
| 4.3 | 19:30 cutoff logs a verdict | ☐ not observed live | ☐ |
| 4.4 | 19:45 scoring runs, or skips with a reason | ☐ not observed live | ☐ |
| 4.5 | 20:00 allocation runs, or blocks with a reason | ☐ not observed live | ☐ |
| 4.6 | A full night observed end to end at the real times | ☐ **not done** | ☐ |

4.6 is the one check that cannot be simulated. **Run it on a non-production
dataset before the first real feed.**

## Step 5 — Tenant isolation and PII authorization

Use two synthetic tenants. **Never real borrower data.**

| # | Item | Isolated-DB evidence | Pilot result |
|---|---|---|---|
| 5.1 | Cross-bank feed history hidden | PASS | ☐ |
| 5.2 | Cross-bank batch/row = uniform 404 | PASS | ☐ |
| 5.3 | Cannot release another bank's row | PASS | ☐ |
| 5.4 | `bank_feed.pii.read` held by BANK_ADMIN only | PASS | ☐ |
| 5.5 | BANK_TECHOPS / SERVICE get no `raw` | PASS | ☐ |
| 5.6 | Successful raw read persists a `DATA_EXPORT` row | PASS | ☐ |
| 5.7 | Audit metadata carries **no** borrower values | PASS | ☐ |
| 5.8 | Audit failure withholds the PII (fail closed) | PASS | ☐ |
| 5.9 | Safe quarantine metadata still served without the grant | PASS | ☐ |

Confirm 5.6 against the real database:

```sql
select created_at, user_id, details->>'batch_id' as batch,
       details->>'rows_disclosed' as rows
from audit.audit_logs
where action = 'DATA_EXPORT' and details->>'access' = 'raw_pii_read'
order by created_at desc limit 20;
```

## Step 6 — Feed to allocation, on approved test data

Requires the completed `docs/PILOT-FEED-CONTRACT.md` and a sanitized sample.

| # | Step | Expected | Pilot result |
|---|---|---|---|
| 6.1 | `POST /bank/feed/validate` | reports rows/headers, **writes nothing** | ☐ |
| 6.2 | `POST /bank/feed` | `201`, status `RECEIVED`, `queued: true` | ☐ |
| 6.3 | Loan book immediately after upload | **unchanged** | ☐ |
| 6.4 | Re-upload the identical file | `duplicate: true`, same batch | ☐ |
| 6.5 | Ingestion completes | `LOADED` or `PARTIAL` (never silently `RECEIVED`) | ☐ |
| 6.6 | Quarantine listing | each held row shows its reason | ☐ |
| 6.7 | Release an eligible row | placement `source=FEED` + a case | ☐ |
| 6.8 | Release an `UNKNOWN_BRANCH` row | `NOT_RELEASABLE`, row untouched | ☐ |
| 6.9 | Scoring for the business date | snapshots written | ☐ |
| 6.10 | Allocation with both stages ready | plans; no `BLOCKED_UPSTREAM` | ☐ |
| 6.11 | Allocation with a stage missing | **refuses**, records the reason | ☐ |
| 6.12 | No duplicate ACTIVE placements afterwards | 0 rows | ☐ |

## Step 7 — Rollback and escalation

| # | Item | Reference | Result |
|---|---|---|---|
| 7.1 | Rollback procedure rehearsed, not just read | `docs/DEPLOY.md` §7 | ☐ |
| 7.2 | Previous image tag recorded before deploying | §5 | ☐ |
| 7.3 | Pre-deploy dump location recorded | §6 | ☐ |
| 7.4 | Who may authorise a rollback | — | ____________ |
| 7.5 | Who is escalated to for a data-integrity incident | — | ____________ |
| 7.6 | Where incidents are recorded | — | ____________ |

### Rollback decision rule

Roll back if any of these is true after a release:

- duplicate ACTIVE placements appear (runbook §3 query 5) — **immediate**
- a batch reaches `LOADED` while rows are missing from the loan book
- allocation plans on a business date with no finished feed
- raw PII is served without a matching `DATA_EXPORT` row

Do **not** roll back for a quarantined batch or a `REJECTED` feed: both are the
system working, and nothing entered the book.

### Escalation, by symptom

| Symptom | First action | Escalate to |
|---|---|---|
| Duplicate ACTIVE placements | stop releasing; capture the query output | ____________ |
| Batch stuck at `RECEIVED` | runbook §2 then §5 — **do not re-upload** | ____________ |
| Allocation blocked before 06:00 IST | runbook §5 "Allocation was blocked" | ____________ |
| Raw PII served with no audit row | treat as a security incident | ____________ |
| Feed rejected | fix the file, upload a corrected one | ____________ |

---

## Appendix — command sheet with expected outputs (Phase 11)

Every command below was **executed on the dev stack in Phase 11** to confirm it
runs and to capture the shape of a healthy result. The "expected" column is
therefore real output, not an invention. Running them **in the pilot
environment** is Gate E and has **not** been done.

Set these once per session:

```bash
cd /path/to/TIQCollect          # the deployment directory
C="docker compose"              # or: docker compose -f docker-compose.prod.yml
```

### Step 0 — database identity (run first, every time)

```bash
$C exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select current_database(), current_user;"'
```

Dev-stack output: `fieldops|fieldops`

Expected: the **intended pilot database name**. Anything else → **STOP**.
Record the value in Step 0's table; do not proceed on a mismatch.

### Step 1.7 — migration parity

```bash
$C exec -T api sh -lc 'cd /app && \
  echo "current=$(alembic current 2>/dev/null | tail -1)"; \
  echo "heads=$(alembic heads  2>/dev/null | tail -1)"'
```

Dev-stack output:
```
current=v2_0040 (head)
heads=v2_0040 (v2) (head)
```

Expected: the same revision in both lines, and `(head)` present. A `current`
behind `heads` means migrations are pending — resolve before continuing.

### Step 2 — restart, then prove registration

```bash
$C restart celery_worker celery_beat
sleep 20

$C exec -T celery_worker \
  celery -A app.workers.celery_app inspect registered --timeout 20 \
  | grep -E 'feed_cutoff|feed_ingestion|repayment_scoring|run_nightly_allocation'
```

Dev-stack output — **all five lines must appear**:
```
    * app.workers.tasks.allocation.run_nightly_allocation
    * app.workers.tasks.feed_cutoff.check_feed_cutoff
    * app.workers.tasks.feed_ingestion.ingest_feed_batch
    * app.workers.tasks.feed_ingestion.recover_stranded_batches
    * app.workers.tasks.repayment_scoring.run_nightly_repayment_scoring
```

A missing `feed_cutoff` line means the worker is running pre-Phase-7 code: beat
will fire at 19:30 and the dispatch will fail `NotRegistered`. Restart again and
confirm the image tag.

```bash
# schedule and timezone
$C exec -T celery_beat python -c "
from app.workers.celery_app import celery_app as c
s = c.conf.beat_schedule
for k in ('feed-cutoff-check','nightly-repayment-scoring','nightly-ml-allocation','bank-feed-recovery'):
    print(k, s[k]['schedule'])
print('tz:', c.conf.timezone)"
```

Expected:
```
feed-cutoff-check         <crontab: 30 19 * * * (m/h/dM/MY/d)>
nightly-repayment-scoring <crontab: 45 19 * * * (m/h/dM/MY/d)>
nightly-ml-allocation     <crontab: 0 20 * * * (m/h/dM/MY/d)>
bank-feed-recovery        <crontab: */15 * * * * (m/h/dM/MY/d)>
tz: Asia/Kolkata
```

### Step 3 — the recovery sweep actually executes

```bash
$C logs -f --since 1m celery_worker | grep -i recover_stranded
```

Expected, once per quarter hour (dev-stack sample):
```
[...: INFO/MainProcess] Task ...recover_stranded_batches[...] received
[...] feed_ingestion.recovery  dispatched=0 found=0 reclaimed_stale=0
[...] Task ...recover_stranded_batches[...] succeeded in 0.02s
```

Watch **two** consecutive boundaries. Silence across both → beat is not
scheduling or the worker is not consuming.

### Step 4 — IST business date

```bash
$C exec -T api sh -lc 'date -u "+UTC %Y-%m-%d %H:%M"; TZ=Asia/Kolkata date "+IST %Y-%m-%d %H:%M"'

$C exec -T api sh -lc 'cd /app && python -c "
from app.services.pipeline_readiness import business_date
print(\"business_date():\", business_date())"'
```

Expected: `business_date()` equals the **IST** calendar date, which differs from
the UTC date between 18:30 and 24:00 UTC.

### Step 5 — PII authorization and audit persistence (read-only)

```bash
$C exec -T api sh -lc 'cd /app && python -c "
from app.core.permissions import role_capabilities
from app.models.user import UserRole
for r in UserRole:
    if \"bank_feed.pii.read\" in role_capabilities(r): print(\"holds:\", r.value)"'
```

Expected: exactly `holds: BANK_ADMIN`.

After an authorized raw read has been performed with synthetic data:

```sql
select created_at, user_id, details->>'batch_id' as batch,
       details->>'rows_disclosed' as rows, details->>'endpoint' as endpoint
from audit.audit_logs
where action = 'DATA_EXPORT' and details->>'access' = 'raw_pii_read'
order by created_at desc limit 20;
```

Expected: one row per authorized raw read, carrying **no borrower values**.

### Step 6 — fault injection and destructive tests

**Confined to a dedicated test database, never the pilot database.** The Phase 8
suite enforces this itself: its fixture asserts `current_database()` equals
`fieldops_phase8` and aborts otherwise.

```bash
# create the dedicated database once
$C exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE fieldops_phase8;"'

$C exec -T api sh -lc 'cd /app && export DATABASE_URL="$(python -c "
from app.core.config import settings
print(settings.DATABASE_URL.rsplit(chr(47),1)[0] + chr(47) + \"fieldops_phase8\")")" \
  && alembic upgrade head'

# 71 tests: concurrency, worker-kill, PII authorization, end to end
$C exec -T api sh -lc 'cd /app && PHASE8_DB=1 python -m pytest tests/phase8 -q -p no:cacheprovider'
```

Expected: `71 passed`. Dev-stack / isolated-DB result in Phase 11: **71 passed**.

Do **not** run `tests/phase8` against the pilot database. It kills workers,
injects failures and backdates timestamps.

### Result recording

| Step | Command run? | Output matched expected? | Date / operator |
|---|---|---|---|
| 0 database identity | ☐ | ☐ | ____________ |
| 1.7 migration parity | ☐ | ☐ | ____________ |
| 2 restart + registration | ☐ | ☐ | ____________ |
| 2 schedule + timezone | ☐ | ☐ | ____________ |
| 3 recovery sweep (2 boundaries) | ☐ | ☐ | ____________ |
| 4 IST business date | ☐ | ☐ | ____________ |
| 5 PII holders + audit rows | ☐ | ☐ | ____________ |
| 6 phase8 suite on the test DB | ☐ | ☐ | ____________ |

**Every box above is unticked. Gate E is NOT VERIFIED.**

---

## What this plan does NOT establish

- **No step has been executed in a pilot environment.** The evidence columns
  cite the dev stack and the isolated test database only.
- A full 19:30 → 20:00 night has **never** been observed at the real clock times
  (4.6).
- A real 60-minute stale-claim expiry has never been waited out; the recovery
  logic was verified with backdated timestamps.
- No backup has been test-restored by me (1.3).
- Volumes above 2,000 rows are unmeasured.

| Field | Value |
|---|---|
| Executed in environment | ____________________ |
| Executed by / date | ____________________ |
| All steps PASS? | ☐ yes ☐ no — outstanding: ____________ |
| Approved to proceed to a pilot feed | ____________________ |
