# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-21 — New file. Keeps Agent.status in step with approved leave:
#   ON_LEAVE while a leave covers today, OFF_DUTY the day after it ends. The
#   planner already excludes ON_LEAVE, so this is what stops a nightly plan
#   being built for an agent whose leave started today, and what puts them
#   back on the roster when it ends. Idempotent; 00:10, after the 00:05 PTP
#   lifecycle and well before the 20:00 plan.
# ───────────────────────────────────────────────────────────────────────────
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.leave_housekeeping.sync_leave_statuses", bind=True)
def sync_leave_statuses(self):
    from app.core.database import SessionLocal
    from app.services.leave_service import LeaveService
    db = SessionLocal()
    try:
        out = LeaveService(db).sync_statuses()
        logger.info("leave.sync_statuses", **out)
        return out
    finally:
        db.close()
