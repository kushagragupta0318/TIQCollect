# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-06 — Stopped recording reminders that were never sent.
#
#   This task selected every PTP due today with reminder_sent == False, set
#   reminder_sent = True and reminder_sent_at = now, and returned a count — under
#   a comment reading "In production: send SMS via SMS gateway". No message was
#   ever sent by any line of it.
#
#   The flag is what made that harmful rather than merely useless. Because the
#   query filters on reminder_sent == False, marking the row REMOVED it from
#   every future run: once "reminded", a borrower could never be reminded again,
#   so wiring a real gateway later would have silently skipped every promise the
#   stub had already ticked off. The column recorded a fact that never happened
#   and then prevented the real thing from happening.
#
#   Now the task reports what is due and marks nothing, because nothing is sent.
#   Deliberately NOT wired to NotificationService here: that would start sending
#   real SMS to real borrowers the moment Twilio credentials are present, which
#   is a product decision about contacting people, not a bug fix. What it needs
#   is below.
# ───────────────────────────────────────────────────────────────────────────
"""Promise-to-pay reminders — currently a reporting stub, and says so.

TO ACTUALLY SEND, three things are needed and none is a line of this file:
  1. Reminder copy approved for borrower contact (the receipt and visit
     templates in payment_service/visit_service are the precedent).
  2. A decision that outbound reminders are wanted at all — RBI contact-hour
     rules apply to an automated 09:00 send exactly as they do to a visit, and
     `is_within_contact_hours` should gate it.
  3. Marking reminder_sent ONLY on a confirmed send, so a failed send is retried
     rather than swallowed the way the previous version swallowed all of them.
"""
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
        due = db.query(PTP).filter(
            PTP.committed_date == today,
            PTP.status == PTPStatus.ACTIVE,
            PTP.reminder_sent == False,  # noqa: E712
        ).count()

        # No write. reminder_sent stays False so these rows remain visible to a
        # real sender when one exists — see the module docstring.
        logger.info(
            "ptp_reminders.due",
            count=due,
            sent=0,
            transport_configured=False,
            detail="No reminder transport is wired; nothing was sent or marked.",
        )
        return {"ptps_due": due, "ptps_reminded": 0, "transport_configured": False}
    finally:
        db.close()
