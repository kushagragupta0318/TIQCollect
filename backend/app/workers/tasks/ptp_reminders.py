from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.ptp_reminders.send_ptp_reminders", bind=True)
def send_ptp_reminders(self):
    from datetime import date
    from app.core.database import SessionLocal
    from app.models.ptp import PTP, PTPStatus

    db = SessionLocal()
    try:
        today = date.today()
        ptps = db.query(PTP).filter(
            PTP.committed_date == today,
            PTP.status == PTPStatus.ACTIVE,
            PTP.reminder_sent == False,  # noqa: E712
        ).all()

        sent = 0
        for ptp in ptps:
            # In production: send SMS via SMS gateway
            ptp.reminder_sent = True
            from datetime import datetime, timezone
            ptp.reminder_sent_at = datetime.now(timezone.utc)
            sent += 1

        db.commit()
        logger.info("ptp_reminders.sent", count=sent)
        return {"ptps_reminded": sent}
    finally:
        db.close()
