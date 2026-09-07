# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-06 — Stopped reporting pushes that were never sent.
#
#   This task counted every agent holding an fcm_token, logged "beat_push.sent"
#   per agent and returned {"beats_pushed": N} — under a comment reading
#   "In production: send FCM push notification". Nothing was ever pushed. The
#   log event name was the problem: `beat_push.sent` in a log aggregator is
#   indistinguishable from a real delivery, so the one signal that would have
#   revealed agents were not being notified read as confirmation that they were.
#
#   Less harmful than the PTP reminder stub, which also WROTE a flag that
#   suppressed future attempts (see ptp_reminders.py) — nothing here was
#   persisted. But it is the same class of claim, so it gets the same treatment:
#   report what exists, name what is missing, assert nothing that did not happen.
#
#   No FCM client is added. firebase-admin is not a dependency, FIREBASE_
#   CREDENTIALS_PATH points at a file that is not in the repo, and no agent row
#   carries an fcm_token because nothing collects one — the mobile client is a
#   browser PWA with no push registration. Wiring delivery is a feature, not a
#   fix.
# ───────────────────────────────────────────────────────────────────────────
"""Morning beat notification — currently a readiness report, and says so.

Runs at 06:00 IST. The beats themselves are built by the 20:00 allocation task;
this only concerns telling agents about them. Agents see their beat by opening
the app either way (GET /agent/beat), so nothing is blocked by the absence of
push — it is a convenience that does not exist yet.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.beat_generation.push_morning_beats", bind=True)
def push_morning_beats(self):
    from datetime import date
    from app.core.database import SessionLocal
    from app.models.agent import Agent
    from app.models.beat import Beat, BeatStatus

    db = SessionLocal()
    try:
        today = date.today()
        beats = db.query(Beat).filter(
            Beat.beat_date == today,
            Beat.status == BeatStatus.PLANNED,
        ).all()

        # How many COULD be pushed if a transport existed — not how many were.
        notifiable = 0
        for beat in beats:
            agent = db.get(Agent, beat.agent_id)
            if agent and agent.fcm_token:
                notifiable += 1

        logger.info(
            "morning_beat_push.report",
            beats_planned=len(beats),
            agents_with_push_token=notifiable,
            pushed=0,
            transport_configured=False,
            detail="No push transport is wired; agents see beats by opening the app.",
        )
        return {
            "beats_planned": len(beats),
            "agents_with_push_token": notifiable,
            "beats_pushed": 0,
            "transport_configured": False,
        }
    finally:
        db.close()
