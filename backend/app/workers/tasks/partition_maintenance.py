# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B12) — NEW. Daily partition maintenance (design §7.3), 01:30 IST:
#   before the 02:00 beat_reconciliation reads yesterday's trail. The policy
#   and every operation live in core/partitions.py; this is only the schedule.
#   The 03:00 row-delete location sweep stays for now: dropping whole month
#   partitions replaces it only once this has run in production.
# ────────────────────────────────────────────────────────────────────────────
"""Pre-create month partitions, report DEFAULT rows, apply decided retention."""
import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.partition_maintenance.maintain_partitions", bind=True)
def maintain_partitions(self):
    from app.core.database import engine
    from app.core.partitions import maintain

    result = maintain(engine)
    if result["default_rows"]:
        # A row in a DEFAULT partition has a key outside every month (a device
        # clock years off, a bad feed date). It blocks creating that month's
        # partition, so it is said loudly, never silently.
        logger.error("partitions.default_rows", **{k.replace(".", "_"): v for k, v in result["default_rows"].items()})
    logger.info("partitions.maintained", created=len(result["created"]), dropped=len(result["dropped"]),
                references_nulled=result["references_nulled"])
    return result
