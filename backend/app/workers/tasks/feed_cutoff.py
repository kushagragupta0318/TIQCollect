"""19:30 IST — the day's feed cutoff. Checks; never fetches.

WHY THIS FETCHES NOTHING. There is no scheduled input to fetch. `run_ingestion`
takes a file path, `core/config.py` has no SFTP host, feed directory or
credentials, and the product's actual intake is the bank pushing a file to
POST /bank/feed — which already dispatches ingestion on arrival
(services/bank/feed_upload_service._dispatch, workers/tasks/feed_ingestion).
Inventing a fetcher here would mean inventing a file source the deployment does
not have.

WHAT IT IS FOR. 19:30 is the moment the day's intake is supposed to be over, so
it is the right moment to ASK whether it is, while there is still half an hour
before allocation. Without this the first sign of a missing feed was agents
finding no beats the next morning — an absence, which is exactly the failure
mode AllocationRunStatus.FAILED was introduced to end.

It records the verdict and returns it. It does not raise on a missing feed:
raising would retry a condition no retry can fix (a bank has not sent a file),
and the gate at 19:45 and 20:00 is what actually stops unsafe work. This task's
job is to make the problem VISIBLE at 19:30 rather than at 06:00.

CLI AND MANUAL RECOVERY ARE UNCHANGED. `python -m scripts.ingest_daily` still
ingests a file whenever it is run, and recover_stranded_batches still sweeps
uploaded batches every 15 minutes. This task reads their results; it never
gates, delays or replaces either.
"""
from __future__ import annotations

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    name="app.workers.tasks.feed_cutoff.check_feed_cutoff",
    bind=True,
    # No retries. Every outcome is a fact about the day, not a transient fault:
    # a feed that has not arrived does not arrive because the task ran again.
    max_retries=0,
)
def check_feed_cutoff(self, business_date_str: str | None = None) -> dict:
    """Report whether the day's feed is in and finished.

    Idempotent and side-effect-free apart from its log line, so a duplicate
    delivery or a manual re-run is harmless — it re-reads the same rows and
    reports the same verdict.
    """
    from datetime import date

    from app.core.database import SessionLocal
    from app.services.pipeline_readiness import business_date, ingestion_status

    on = date.fromisoformat(business_date_str) if business_date_str else business_date()
    db = SessionLocal()
    try:
        status = ingestion_status(db, on)
        # as_json() already carries business_date in its detail, so this is one
        # dict, not a duplicated keyword.
        payload = {"business_date": str(on), **status.as_json()}
        if status.ready:
            logger.info("feed_cutoff.ready", **payload)
        else:
            # WARNING, not ERROR: nothing has failed yet. Allocation at 20:00
            # is what refuses, and it records its own row when it does.
            logger.warning("feed_cutoff.not_ready", **payload)
        return payload
    finally:
        db.close()
