"""
Nightly ML case allocation task.
Runs at 8 PM, assigns cases to agents for next business day,
generates geo-optimised beat plans.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.allocation.run_nightly_allocation", bind=True, max_retries=3)
def run_nightly_allocation(self):
    from app.core.database import SessionLocal
    from app.ml.allocator import CaseAllocator

    logger.info("nightly_allocation.start")
    db = SessionLocal()
    try:
        allocator = CaseAllocator(db)
        result = allocator.run()
        logger.info("nightly_allocation.complete", **result)
        return result
    except Exception as exc:
        logger.error("nightly_allocation.error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()
