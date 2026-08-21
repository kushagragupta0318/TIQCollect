# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 — Registered app.workers.tasks.transcription (below) so visit-
#   recording transcription runs as a background job instead of blocking the
#   POST /agent/visits/{visit_id}/transcribe request. Full detail + why:
#   /changelog.md
# ───────────────────────────────────────────────────────────────────────────
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "tiqcollect",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks.allocation",
        "app.workers.tasks.ptp_reminders",
        "app.workers.tasks.beat_generation",
        "app.workers.tasks.performance_snapshot",
        "app.workers.tasks.transcription",
        "app.workers.tasks.demo_daily_feed",
        "app.workers.tasks.location_retention",
        "app.workers.tasks.repayment_scoring",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    beat_schedule={
        # Repayment scoring at 7:45 PM IST — after the daily bank ingest
        # (~7:30 PM, scripts/ingest_daily.py) and before allocation at 8 PM.
        # Scoring reads what ingest just wrote; allocation reads what scoring
        # leaves. Fifteen minutes is the whole margin, which is why the task
        # bulk-loads rather than querying per loan.
        #
        # Writing Customer.risk_score is gated on REPAYMENT_WRITE_RISK_SCORE,
        # which defaults to False. Scheduling this task does NOT turn that on:
        # with the gate closed it scores the book and writes snapshots only, so
        # the training set builds from now while the live column stays put.
        "nightly-repayment-scoring": {
            "task": "app.workers.tasks.repayment_scoring.run_nightly_repayment_scoring",
            "schedule": crontab(hour=19, minute=45),
        },
        # ML allocation runs at 8 PM IST every day
        "nightly-ml-allocation": {
            "task": "app.workers.tasks.allocation.run_nightly_allocation",
            "schedule": crontab(hour=20, minute=0),
        },
        # DEMO_MODE only: fresh "bank" pool cases at 5:30 AM IST (before the beat
        # push) so opening the app on a new day shows new cases flowing in.
        # No-op when DEMO_MODE is off (guarded inside the task).
        "demo-daily-feed": {
            "task": "app.workers.tasks.demo_daily_feed.run_demo_daily_feed_task",
            "schedule": crontab(hour=5, minute=30),
        },
        # Beat plans pushed to agents at 6 AM IST
        "morning-beat-push": {
            "task": "app.workers.tasks.beat_generation.push_morning_beats",
            "schedule": crontab(hour=6, minute=0),
        },
        # PTP reminders at 9 AM IST
        "ptp-reminders": {
            "task": "app.workers.tasks.ptp_reminders.send_ptp_reminders",
            "schedule": crontab(hour=9, minute=0),
        },
        # Location trail retention sweep at 3 AM IST, off the back of the
        # quiet window between the nightly allocation and the morning beat push.
        "location-trail-retention": {
            "task": "app.workers.tasks.location_retention.prune_location_trail",
            "schedule": crontab(hour=3, minute=0),
        },
        # Monthly performance snapshot at midnight on 1st of each month
        "monthly-performance-snapshot": {
            "task": "app.workers.tasks.performance_snapshot.take_monthly_snapshot",
            "schedule": crontab(hour=0, minute=0, day_of_month=1),
        },
    },
)
