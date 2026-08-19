"""Nightly sweep that ages out the agent location trail.

Movement history is employee-monitoring data, so it is kept for a bounded
window (settings.LOCATION_RETENTION_DAYS) rather than indefinitely. SOS fixes
are exempt: they are the record of a safety incident and outlive the routine
trail they sit inside.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.location_retention.prune_location_trail", bind=True)
def prune_location_trail(self):
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.agent_location import AgentLocation

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.LOCATION_RETENTION_DAYS)
    db = SessionLocal()
    try:
        deleted = (
            db.query(AgentLocation)
            .filter(
                AgentLocation.recorded_at < cutoff,
                # Never prune an SOS point. An incident must remain reviewable
                # long after the ordinary trail around it has been discarded.
                AgentLocation.is_sos.is_(False),
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info("location_retention.pruned", deleted=deleted,
                    cutoff=cutoff.isoformat(),
                    retention_days=settings.LOCATION_RETENTION_DAYS)
        return {"deleted": deleted, "cutoff": cutoff.isoformat()}
    finally:
        db.close()
