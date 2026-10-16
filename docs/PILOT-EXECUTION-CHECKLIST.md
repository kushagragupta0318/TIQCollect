# Controlled pilot — execution checklist

**For:** the authorized operator, to follow **after** every prerequisite in
`docs/PILOT-HANDOFF-SUMMARY.md` is satisfied.
**Branch:** `TIQCollect-app` · **HEAD at preparation:** `ded3c1a`

> **Do not begin this checklist until all seven gates are closed.** It assumes
> an approved PII policy, an approved migration decision, named operators, a
> bank-confirmed feed contract, an identified and authorized environment, and a
> tested backup. Starting early does not accelerate the pilot; it produces
> results nobody can rely on.

**Nothing in this document has been executed.** Every result column is empty by
design. Commands marked *(dry-run verified)* were run on the development stack
to confirm they work and to capture the shape of a healthy result — that is not
pilot evidence.

Companion documents, referenced rather than repeated:

| Need | Document |
|---|---|
| The commands, with real expected output | `PILOT-ENV-VERIFICATION.md` Appendix |
| Daily operation after go-live | `PILOT-RUNBOOK-FEED.md` §6, §6a |
| The scheduled-night record form | `PILOT-NIGHT-RUN-RECORD.md` |
| Feed format rules | `PILOT-FEED-CONTRACT.md` |
| Deploy, backup, rollback | `DEPLOY.md` §5–§7 |

---

## Stage 0 — Identity and authorization (STOP GATE)

Run before anything else. Every later stage writes or restarts something.

| # | Check | Expected | Result |
|---|---|---|---|
| 0.1 | Written authorization to operate in this environment exists | named, dated | ☐ |
| 0.2 | Environment name recorded | not "dev", not the LAN demo host | ____________ |
| 0.3 | `current_database()` matches the intended pilot database | exact match | ____________ |
| 0.4 | `DEMO_MODE` is **false** | `false` | ☐ |
| 0.5 | `DEMO_OTP_ECHO` is **false** | `false` | ☐ |
| 0.6 | This is **not** the `fieldops_phase8` test database | confirmed | ☐ |

```bash
C="docker compose"   # or: docker compose -f docker-compose.prod.yml

$C exec -T postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select current_database(), current_user;"'

$C exec -T api sh -lc 'echo "DEMO_MODE=$DEMO_MODE DEMO_OTP_ECHO=$DEMO_OTP_ECHO"'
```

**STOP** if 0.3 does not match, or if 0.4/0.5 are true. `DEMO_OTP_ECHO` hands
the borrower's payment OTP to the agent and must never be on where anyone
outside the team can reach it.

## Stage 1 — Configuration and secrets

| # | Check | Expected | Result |
|---|---|---|---|
| 1.1 | `deploy/.env.prod` is **not** the LAN-demo file | its header does not say "LAN demo" | ☐ |
| 1.2 | `SECRET_KEY` / `TOTP_ENC_KEY` are production values, not the examples | rotated | ☐ |
| 1.3 | `FORWARDED_ALLOW_IPS` set to the proxy's own address | set | ☐ |
| 1.4 | `.env.prod` permissions are `600` | `600` | ☐ |
| 1.5 | No demo switch enabled (`DEMO_MODE`, `DEMO_OTP_ECHO`, `DEMO_REHEARSAL_MODE`) | all false | ☐ |

**Why 1.3 matters:** behind a reverse proxy without it, every user shares one
login rate-limit bucket (`DEPLOY.md`).

**STOP** on 1.2 or 1.5. Either means this is not a production configuration.

## Stage 2 — Database and migration state

| # | Check | Expected | Result |
|---|---|---|---|
| 2.1 | `alembic current` equals `alembic heads` | same revision, `(head)` | ☐ |
| 2.2 | Exactly one head | one line | ☐ |
| 2.3 | Migration decision on `v2_0041` recorded | approved **or** explicitly deferred | ☐ |
| 2.4 | If approved: applied, and `test_the_seed_equals_the_registry` passes | pass | ☐ |
| 2.5 | If deferred: the expected failure is documented and understood | documented | ☐ |
| 2.6 | Pre-pilot backup taken **and test-restored** | both | ☐ |

```bash
$C exec -T api sh -lc 'cd /app && \
  echo "current=$(alembic current 2>/dev/null | tail -1)"; \
  echo "heads=$(alembic heads  2>/dev/null | tail -1)"'
```
*(dry-run verified: both report `v2_0040`)*

**STOP** on 2.1 (migrations pending) or 2.6 (an untested backup is an
assumption, not a backup).

## Stage 3 — Worker, scheduler and task registration

**Mandatory after any deployment.** Celery loads its task list at boot, so a
newly added task is not dispatchable until a restart — beat would fire at 19:30
and the dispatch would fail `NotRegistered`.

| # | Check | Expected | Result |
|---|---|---|---|
| 3.1 | Worker and beat restarted via the documented procedure | done | ☐ |
| 3.2 | `feed_cutoff.check_feed_cutoff` registered | listed | ☐ |
| 3.3 | `feed_ingestion.ingest_feed_batch` registered | listed | ☐ |
| 3.4 | `feed_ingestion.recover_stranded_batches` registered | listed | ☐ |
| 3.5 | `repayment_scoring…` and `run_nightly_allocation` registered | listed | ☐ |
| 3.6 | Beat schedule: 19:30 / 19:45 / 20:00, one entry each | exact | ☐ |
| 3.7 | Timezone is `Asia/Kolkata` | exact | ☐ |
| 3.8 | `bank-feed-recovery` is `*/15` | exact | ☐ |
| 3.9 | Recovery sweep observed firing across **two** quarter-hour boundaries | 2 observed | ☐ |
| 3.10 | `STALE_CLAIM_MINUTES` is 60 (unchanged) | 60 | ☐ |

Commands and real expected output: `PILOT-ENV-VERIFICATION.md` Appendix Steps 2–3.

**STOP** on any of 3.2–3.5. A missing task means the worker is running old code.

## Stage 4 — Access control and bank isolation

Use **two synthetic tenants**. Never live customer data.

| # | Check | Expected | Result |
|---|---|---|---|
| 4.1 | Another bank's feed history is not visible | hidden | ☐ |
| 4.2 | Another bank's batch/row returns the uniform 404 | identical to a nonexistent id | ☐ |
| 4.3 | Another bank's row cannot be released | 404, row untouched | ☐ |
| 4.4 | A read-only role (BANK_ANALYST) is refused review and release | 403 both | ☐ |
| 4.5 | `bank_feed.pii.read` is held by **BANK_ADMIN only** | exactly that | ☐ |
| 4.6 | BANK_TECHOPS and SERVICE receive **no** `raw` field | absent | ☐ |
| 4.7 | Error bodies leak no traceback, path or driver name | clean | ☐ |

## Stage 5 — Raw-PII authorization and audit evidence

| # | Check | Expected | Result |
|---|---|---|---|
| 5.1 | `raw` absent by default, even for an authorized caller | absent | ☐ |
| 5.2 | An authorized raw read returns only allow-listed fields | 9 fields max | ☐ |
| 5.3 | Each successful raw read writes one `DATA_EXPORT` row | 1:1 | ☐ |
| 5.4 | Audit rows carry field **names**, never borrower values | verified | ☐ |
| 5.5 | A withheld read writes **no** `DATA_EXPORT` row | none | ☐ |
| 5.6 | Audit-write failure withholds the PII (fail closed) | withheld | ☐ |

```sql
select created_at, user_id, details->>'batch_id' as batch,
       details->>'rows_disclosed' as rows, details->>'endpoint' as endpoint
from audit.audit_logs
where action = 'DATA_EXPORT' and details->>'access' = 'raw_pii_read'
order by created_at desc limit 20;
```

**STOP** on 5.4 or 5.6. Either is a privacy control failing.

## Stage 6 — Feed validation and ingestion (approved test data only)

Requires the completed `PILOT-FEED-CONTRACT.md` and its sanitized sample.
**Do not use the protected `backend/scripts/sample_daily_feed.csv`.**

| # | Check | Expected | Result |
|---|---|---|---|
| 6.1 | Dry-run validation writes nothing | no batch created | ☐ |
| 6.2 | Upload returns `201`, status `RECEIVED`, `queued: true` | exact | ☐ |
| 6.3 | Loan book **unchanged** immediately after upload | unchanged | ☐ |
| 6.4 | Re-uploading the identical file returns `duplicate: true`, same batch | same id | ☐ |
| 6.5 | A duplicate upload enqueues **no** second ingestion | 1 dispatch total | ☐ |
| 6.6 | Ingestion reaches `LOADED` or `PARTIAL` — never silently `RECEIVED` | terminal | ☐ |
| 6.7 | Row outcomes match the contract's §8 expectations | as agreed | ☐ |
| 6.8 | Redelivering the task after completion changes nothing | book unchanged | ☐ |

**STOP** on 6.3. If the book changes at upload time, the staging boundary is broken.

## Stage 7 — Quarantine review and permitted release

| # | Check | Expected | Result |
|---|---|---|---|
| 7.1 | Each held row shows its recorded reason | reason present | ☐ |
| 7.2 | Counts cover the whole batch, not the page shown | DB counts | ☐ |
| 7.3 | An `UNKNOWN_BRANCH` row is **not releasable** | `NOT_RELEASABLE` | ☐ |
| 7.4 | Attempting to release it leaves the row untouched | still `QUARANTINED` | ☐ |
| 7.5 | A row whose cause is unfixed is refused **again** | `STILL_REFUSED` | ☐ |
| 7.6 | A genuinely eligible row releases into a placement + case | `source=FEED` | ☐ |
| 7.7 | Releasing twice does not place twice | `ALREADY_RELEASED` | ☐ |
| 7.8 | `released_by` / `released_at` record the acting user | populated | ☐ |
| 7.9 | A `PARTIAL` batch stays `PARTIAL` after a release | unchanged | ☐ |

**STOP** on 7.5 or 7.7. Either means release is forcing rows through.

## Stage 8 — Scoring readiness and allocation gating

| # | Check | Expected | Result |
|---|---|---|---|
| 8.1 | Scoring uses the **IST** business date | IST, not UTC | ☐ |
| 8.2 | With no finished feed, scoring **skips** and says why | skipped + reason | ☐ |
| 8.3 | With a finished feed, scoring writes snapshots | snapshots > 0 | ☐ |
| 8.4 | A partially-completed scoring run is **not** reported ready | blocked | ☐ |
| 8.5 | With a stage missing, allocation **refuses** | `blocked: true` | ☐ |
| 8.6 | A refusal records one row per manager with `outcome: BLOCKED_UPSTREAM` | rows present | ☐ |
| 8.7 | A blocked night creates **no** beats | none | ☐ |
| 8.8 | With both stages ready, allocation plans normally | planned | ☐ |
| 8.9 | **No loan carries two ACTIVE placements** | 0 rows | ☐ |

8.4 is the control added by DEFECT-A1 (`docs/FINAL-AUDIT.md`): a scoring run
that crashed early must not let allocation plan on an unscored book.

```sql
select loan_id, count(*) from collections.placements
where status = 'ACTIVE' group by 1 having count(*) > 1;
```

**STOP IMMEDIATELY** on 8.9. A loan placed with two agencies means two teams
chasing one borrower. This is a data-integrity incident.

## Stage 9 — The scheduled night (the one that cannot be simulated)

| # | Check | Expected | Result |
|---|---|---|---|
| 9.1 | 19:30 cutoff dispatched at its scheduled IST time | observed | ☐ |
| 9.2 | 19:45 scoring dispatched at its scheduled IST time | observed | ☐ |
| 9.3 | 20:00 allocation dispatched at its scheduled IST time | observed | ☐ |
| 9.4 | Each stage's reported status matches the database | truthful | ☐ |
| 9.5 | The run record is completed and signed | signed | ☐ |
| 9.6 | A second night observed with a feed present (full success path) | observed | ☐ |

Use `PILOT-NIGHT-RUN-RECORD.md`. **A night where allocation correctly blocks is
a successful observation** — it proves the schedule and the gate. Record it,
then observe 9.6 for the success path.

**Never adjust the schedule or bypass a gate to obtain a green result.** The
purpose is to find out what actually happens.

## Stage 10 — Operational handover

| # | Check | Expected | Result |
|---|---|---|---|
| 10.1 | Primary operator briefed on the daily check | briefed | ☐ |
| 10.2 | Backup operator briefed | briefed | ☐ |
| 10.3 | First daily check completed and filed | filed | ☐ |
| 10.4 | Escalation contacts confirmed reachable | confirmed | ☐ |
| 10.5 | Rollback authority and procedure confirmed | confirmed | ☐ |
| 10.6 | Bank-side feed contact confirmed reachable | confirmed | ☐ |

## Stop conditions and escalation

**Halt the pilot immediately** on any of these:

| Condition | Why | Action |
|---|---|---|
| Duplicate ACTIVE placements (8.9) | two agencies chasing one borrower | stop releases, capture output, escalate, consider rollback |
| Raw PII served with no matching audit row (5.3/5.6) | privacy control failed | treat as a security incident |
| Cross-bank data visible (4.1–4.3) | tenant isolation failed | halt, escalate |
| A batch `LOADED` while rows are missing from the book | ingestion lying about state | halt, rollback |
| Allocation planned on a business date with no finished feed (8.5) | the gate failed | halt, do not use the plan |
| `DEMO_OTP_ECHO` true anywhere reachable (0.5) | OTP leaked to agents | disable immediately |

**Do not halt for:** a `REJECTED` feed, a `PARTIAL` batch, or a correctly
blocked night. All three are the system working as designed — nothing entered
the loan book incorrectly.

### If a check fails

1. Record which check, the actual output, and the time.
2. Consult `PILOT-RUNBOOK-FEED.md` §5 ("When something is wrong").
3. **Do not re-upload the feed** to clear a stuck batch — the batch row is the
   queue of record; force the task instead (runbook §5).
4. Escalate per the names in the runbook's ownership table.
5. Do not continue past a STOP gate on the assumption it will resolve itself.

---

## Sign-off

| Field | Value |
|---|---|
| Environment | ____________________ |
| Operator | ____________________ |
| Start date / time | ____________________ |
| Stages 0–10 all PASS? | ☐ yes ☐ no — outstanding: ____________ |
| Authorized to accept live bank feeds | ____________________ |
| Date | ____________________ |

**Until this table is signed, the pilot is in preparation, not in operation.**
