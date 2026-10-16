"""Process an uploaded bank feed batch: RECEIVED -> VALIDATING -> LOADED/PARTIAL/REJECTED.

THE HANDOFF THIS CLOSES. `POST /bank/feed` stored the file and wrote a batch at
RECEIVED, and nothing consumed it — the loan book did not change until somebody
ran `python -m scripts.ingest_daily` by hand. This task is the missing consumer.

WHAT IT IS NOT. It does not re-implement ingestion: `scripts/ingest_daily.
run_ingestion` stays the one engine, shared with the CLI, and this module only
claims a batch, hands the engine its file, and records the outcome. A second
pipeline is exactly what would drift from the first.

DELIVERY IS AT-LEAST-ONCE, NOT EXACTLY-ONCE. celery_app sets
`task_acks_late=True`, so a task whose worker dies is redelivered. Safety comes
from two things, not from the broker:

  * THE CLAIM is a single atomic UPDATE ... WHERE status = 'RECEIVED'. Whichever
    worker's UPDATE reports one row changed owns the batch; every other delivery
    sees zero rows and returns `already_claimed`. No lock, no SELECT-then-write
    race — the same pattern the upload's IntegrityError dedup already relies on.
  * THE ENGINE converges. run_ingestion keys every write on the loan account
    number and updates in place, and reuses the batch row for a given
    (bank, feed_type, business_date, sha256), so processing one file twice ends
    in the same state as processing it once. That is what makes a redelivery
    after a crash safe, and it is a property of the engine — if it ever stops
    being true, this task's retry policy has to be revisited with it.

RETRIES ARE BOUNDED AND TYPED. A transient fault (storage unreachable, database
dropped) retries up to MAX_RETRIES with backoff, leaving the batch claimable
again. A permanent fault (the file is gone, the rows are bad) is terminal
immediately: REJECTED, with no retry, because retrying a malformed file forever
is how a queue fills up with work that can never succeed.

NO BORROWER DATA LEAVES THIS MODULE. The batch carries counts; the log carries
ids, counts and a truncated error class. File content is never logged.
"""
from datetime import datetime, timezone

import structlog
from celery.exceptions import SoftTimeLimitExceeded

from app.workers.celery_app import celery_app

logger = structlog.get_logger()

# A claimed batch older than this is assumed to belong to a worker that died
# between the claim and the result. Deliberately longer than the task's own
# hard ceiling (time_limit below), so a slow-but-alive run is never stolen.
STALE_CLAIM_MINUTES = 60

# Per sweep, so a backlog drains over successive runs rather than enqueueing
# everything at once. 50 against a 15-minute schedule is 200/hour, far above
# any realistic daily-feed volume (one file per bank per day).
SWEEP_LIMIT = 50

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 60


@celery_app.task(
    name="app.workers.tasks.feed_ingestion.ingest_feed_batch",
    bind=True,
    max_retries=MAX_RETRIES,
    # Ingestion of a full daily book is long by nature; the module docstring in
    # celery_app explains why a ceiling exists at all.
    soft_time_limit=1800,
    time_limit=2100,
)
def ingest_feed_batch(self, batch_id: str):
    """Ingest one uploaded batch, by id. The bank is read from the BATCH ROW —
    never from the task arguments — so a task cannot be made to ingest a file
    into another bank's book by passing a different id."""
    import tempfile
    from pathlib import Path

    from sqlalchemy import update

    from app.core.database import SessionLocal
    from app.models.lending import BankFeedBatch
    from app.models.tenancy import Bank

    db = SessionLocal()
    tmp_path: Path | None = None
    try:
        # ── 1. Claim ────────────────────────────────────────────────────────
        # One atomic UPDATE. The WHERE clause is the lock: only a RECEIVED row
        # can be claimed, so a terminal batch (LOADED/PARTIAL/REJECTED) and one
        # already in flight (VALIDATING) are both ineligible, and a duplicate
        # delivery of this same task changes nothing.
        claimed = db.execute(
            update(BankFeedBatch)
            .where(BankFeedBatch.id == batch_id, BankFeedBatch.status == "RECEIVED")
            .values(status="VALIDATING")
        ).rowcount
        db.commit()

        if claimed:
            # Stamp WHEN this claim happened, so stale recovery measures the
            # claim rather than the upload. Separate statement on purpose: the
            # conditional UPDATE above is what decides ownership, and keeping
            # it a pure status transition keeps that contract obvious. A failure
            # here is not worth losing the claim over — the batch simply falls
            # back to received_at for staleness, as it did before.
            try:
                b = db.query(BankFeedBatch).filter(BankFeedBatch.id == batch_id).first()
                report = dict(b.dq_report or {})
                report["claimed_at"] = datetime.now(timezone.utc).isoformat()
                b.dq_report = report
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("feed_ingestion.claim_stamp_failed", batch_id=batch_id)

        if not claimed:
            # Not an error: the common causes are a redelivered task and a
            # batch another worker already owns.
            current = db.query(BankFeedBatch.status).filter(BankFeedBatch.id == batch_id).scalar()
            logger.info("feed_ingestion.not_claimable", batch_id=batch_id, status=current)
            return {"batch_id": batch_id, "claimed": False, "status": current}

        batch = db.query(BankFeedBatch).filter(BankFeedBatch.id == batch_id).first()
        bank = db.query(Bank).filter(Bank.id == batch.bank_id).first()

        # ── 2. Permanent failures: terminal, never retried ──────────────────
        if bank is None:
            return _reject(db, batch, "The batch's bank no longer exists.")
        if not batch.storage_key:
            # upload_feed records the batch even when staging the object failed,
            # so this is a real state: there is nothing on disk to ingest.
            return _reject(db, batch, "The uploaded file was never stored; re-upload it.")

        # ── 3. Fetch the exact stored object ────────────────────────────────
        from app.core import storage
        data = storage.download_bytes(batch.storage_key)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
            fh.write(data)
            tmp_path = Path(fh.name)

        # ── 4. Hand it to the one engine ────────────────────────────────────
        # `scripts` sits beside `app` at the repo root, which is on sys.path
        # under pytest (rootdir insertion) and for the CLI (`python -m`), but
        # NOT inside the Celery worker: measured against the running worker,
        # this import raised ModuleNotFoundError and every uploaded feed
        # retried three times and was rejected. The suite could not catch it —
        # it imports the same module successfully under pytest. Resolved here
        # rather than by relying on how the process was launched.
        import sys
        from pathlib import Path

        repo_root = str(Path(__file__).resolve().parents[3])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)

        from scripts.ingest_daily import IngestionError, run_ingestion

        try:
            # business_day is the BATCH's own date, not today's: a batch
            # uploaded for a past date, or picked up after midnight, must land
            # on its own row rather than opening a second one.
            result = run_ingestion(
                tmp_path,
                dry_run=False,
                bank_code=bank.code,
                business_day=batch.business_date,
            )
        except IngestionError as exc:
            # The engine's own typed refusal: the file cannot be ingested as it
            # stands. Terminal — retrying will refuse it identically.
            return _reject(db, batch, str(exc)[:300])

        # ── 5. Record the outcome ───────────────────────────────────────────
        # run_ingestion sets status, counts and loaded_at on the batch itself.
        # Re-read rather than assume, and only fill a terminal status in if the
        # engine left the row mid-flight (it reuses its own batch lookup, which
        # can resolve to a different row than the one claimed here).
        db.expire_all()
        batch = db.query(BankFeedBatch).filter(BankFeedBatch.id == batch_id).first()
        if batch.status == "VALIDATING":
            batch.status = "PARTIAL" if (result.rows_quarantined or result.rows_skipped) else "LOADED"
            batch.rows_total = result.rows_read
            batch.rows_skipped = result.rows_skipped
            batch.rows_quarantined = result.rows_quarantined
            batch.rows_accepted = result.rows_read - result.rows_skipped - result.rows_quarantined
            batch.loaded_at = datetime.now(timezone.utc)
            db.commit()

        logger.info("feed_ingestion.done", batch_id=batch_id, bank_id=batch.bank_id,
                    status=batch.status, rows_read=result.rows_read,
                    rows_skipped=result.rows_skipped, rows_quarantined=result.rows_quarantined)
        return {"batch_id": batch_id, "claimed": True, "status": batch.status,
                "rows_read": result.rows_read, "rows_quarantined": result.rows_quarantined}

    except SoftTimeLimitExceeded:
        # The 1800s soft limit fired: this run is out of time, and a retry gets
        # the SAME file and the same ceiling, so it would time out again —
        # three times over, holding a worker slot for 90 minutes on work that
        # cannot finish. Terminal instead, with the reason said plainly.
        #
        # Caught BEFORE `except Exception` on purpose: celery_app raises it
        # inside the task (see its comment at the task_soft_time_limit
        # setting), so the broad handler below would otherwise treat a
        # timeout as a transient fault and retry it.
        logger.error("feed_ingestion.timed_out", batch_id=batch_id)
        db.rollback()
        b = db.query(BankFeedBatch).filter(BankFeedBatch.id == batch_id).first()
        if b is not None and b.status == "VALIDATING":
            _reject(db, b, "Ingestion exceeded its time limit. Split the file and re-upload.")
        return {"batch_id": batch_id, "claimed": True, "status": "REJECTED", "reason": "timeout"}

    except Exception as exc:
        # ── Transient: release the claim and retry with backoff ─────────────
        # Storage unreachable, database dropped mid-run. The batch goes back to
        # RECEIVED so the retry (or the stale-claim sweep) can pick it up.
        _release(db, batch_id)
        logger.warning("feed_ingestion.retrying", batch_id=batch_id,
                       error_type=type(exc).__name__, error=str(exc)[:200],
                       attempt=self.request.retries + 1, max_retries=MAX_RETRIES)
        if self.request.retries >= MAX_RETRIES:
            # Out of attempts. Terminal, and SAID so rather than left at
            # RECEIVED looking like work nobody has started.
            db2 = SessionLocal()
            try:
                b = db2.query(BankFeedBatch).filter(BankFeedBatch.id == batch_id).first()
                if b is not None and b.status == "RECEIVED":
                    _reject(db2, b, f"Ingestion failed after {MAX_RETRIES} attempts "
                                    f"({type(exc).__name__}).")
            finally:
                db2.close()
            logger.error("feed_ingestion.exhausted", batch_id=batch_id,
                         error_type=type(exc).__name__)
            return {"batch_id": batch_id, "claimed": True, "status": "REJECTED"}
        raise self.retry(exc=exc, countdown=RETRY_BACKOFF_SECONDS * (2 ** self.request.retries))
    finally:
        if tmp_path is not None:
            # The file holds borrower rows; it does not outlive the run.
            tmp_path.unlink(missing_ok=True)
        db.close()


def _claimed_at(batch):
    """When this batch was claimed, or None if it predates claim stamping.

    None means "fall back to received_at", which is what the sweep did before
    the stamp existed — a batch claimed by an older build still recovers, just
    on the older (upload-time) clock.
    """
    raw = (batch.dq_report or {}).get("claimed_at")
    if not raw:
        return _aware(batch.received_at)
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return _aware(batch.received_at)
    return _aware(parsed)


def _aware(value):
    """UTC-aware, whatever the driver returned.

    The cutoff this is compared against is aware, and comparing it to a naive
    datetime raises TypeError. Postgres hands back an aware `received_at`
    (TIMESTAMPTZ) but SQLite does not, so normalising at the one place every
    path returns through is what keeps the sweep from dying on the comparison
    instead of recovering the batch.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reject(db, batch, reason: str) -> dict:
    """Terminal refusal. The reason is operator-facing and carries no row data."""
    batch.status = "REJECTED"
    report = dict(batch.dq_report or {})
    report["ingestion_error"] = reason
    batch.dq_report = report
    db.commit()
    logger.warning("feed_ingestion.rejected", batch_id=batch.id, bank_id=batch.bank_id,
                   reason=reason[:200])
    return {"batch_id": batch.id, "claimed": True, "status": "REJECTED", "reason": reason}


def _release(db, batch_id: str) -> None:
    """Hand a claimed batch back, so a retry can take it. Best effort: the
    session may already be unusable, and the stale-claim sweep is the backstop."""
    from sqlalchemy import update

    from app.models.lending import BankFeedBatch
    try:
        db.rollback()
        db.execute(
            update(BankFeedBatch)
            .where(BankFeedBatch.id == batch_id, BankFeedBatch.status == "VALIDATING")
            .values(status="RECEIVED")
        )
        db.commit()
    except Exception:
        logger.warning("feed_ingestion.release_failed", batch_id=batch_id)


@celery_app.task(name="app.workers.tasks.feed_ingestion.recover_stranded_batches", bind=True)
def recover_stranded_batches(self):
    """The recovery path for the two windows a claim cannot cover by itself.

    1. A batch COMMITTED AT RECEIVED whose dispatch never happened — the upload
       endpoint commits the row before it enqueues, so a crash in between (or a
       broker that is down) leaves work nobody was told about. This is the
       answer to "the batch is committed but dispatch failed": the row IS the
       queue of record, and this sweep drains it.
    2. A batch stuck in VALIDATING because the worker holding it died. After
       STALE_CLAIM_MINUTES it is returned to RECEIVED and re-dispatched.

    Idempotent by construction: it only ever moves a row to RECEIVED and
    enqueues, and the claim decides the winner.
    """
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.database import SessionLocal
    from app.models.lending import BankFeedBatch

    db = SessionLocal()
    try:
        # Staleness is measured from WHEN THE BATCH WAS CLAIMED, not when the
        # file was uploaded. received_at is upload time: a file uploaded this
        # morning and claimed now would already be "older than the cutoff" the
        # instant a worker took it, and this sweep would reclaim it out from
        # under that live worker and run the ingestion twice concurrently.
        # The claim stamps `claimed_at` into dq_report (no migration); a batch
        # without one is treated as claimed at received_at, which is the
        # pre-existing behaviour for anything claimed before this change.
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_CLAIM_MINUTES)
        stale = 0
        for b in db.query(BankFeedBatch).filter(BankFeedBatch.status == "VALIDATING").all():
            claimed_at = _claimed_at(b)
            if claimed_at is not None and claimed_at >= cutoff:
                continue                      # still legitimately in flight
            b.status = "RECEIVED"
            stale += 1
        db.commit()

        # BOUNDED. Oldest first, at most SWEEP_LIMIT per run: a backlog drains
        # over successive sweeps instead of one task enqueueing thousands at
        # once. Oldest-first is what makes that fair — without an ORDER BY, the
        # same arbitrary page could be re-dispatched every 15 minutes while
        # older batches starved.
        pending = [b.id for b in db.query(BankFeedBatch.id)
                   .filter(BankFeedBatch.status == "RECEIVED")
                   .order_by(BankFeedBatch.received_at.asc())
                   .limit(SWEEP_LIMIT).all()]
        dispatched = 0
        for bid in pending:
            try:
                ingest_feed_batch.delay(bid)
                dispatched += 1
            except Exception as exc:
                # The broker is down. Stop rather than spin through the rest:
                # the batch stays RECEIVED, so the next sweep finds it again.
                # Recoverability lives in the row, never in this loop.
                logger.warning("feed_ingestion.recovery_dispatch_failed",
                               batch_id=str(bid), error_type=type(exc).__name__)
                break

        logger.info("feed_ingestion.recovery", reclaimed_stale=stale,
                    found=len(pending), dispatched=dispatched)
        return {"reclaimed_stale": stale, "dispatched": dispatched}
    finally:
        db.close()
