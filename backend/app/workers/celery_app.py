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
        "app.workers.tasks.ptp_lifecycle",
        "app.workers.tasks.leave_housekeeping",
        "app.workers.tasks.transcription",
        "app.workers.tasks.demo_daily_feed",
        "app.workers.tasks.location_retention",
        "app.workers.tasks.repayment_scoring",
        "app.workers.tasks.beat_reconciliation",
        "app.workers.tasks.model_outcomes",
        "app.workers.tasks.model_retraining",
        "app.workers.tasks.partition_maintenance",
        "app.workers.tasks.analytics_refresh",
        "app.workers.tasks.llm_usage",
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
    # Every task has a wall-clock ceiling, so a hung call (OSRM, an LLM, a DB
    # lock) cannot hold one of the worker's slots through the nightly window.
    # The soft limit raises SoftTimeLimitExceeded inside the task, so its
    # except/finally still run; the hard limit kills the child 5 minutes later.
    task_soft_time_limit=900,
    task_time_limit=1200,
    # Longer ceilings where the work is long by nature. Tighten once stage
    # timings exist on the stress profile (RESTRUCTURE-PLAN 2.9).
    task_annotations={
        "app.workers.tasks.allocation.run_nightly_allocation":
            {"soft_time_limit": 1800, "time_limit": 2100},
        "app.workers.tasks.model_retraining.run_candidate_training":
            {"soft_time_limit": 3600, "time_limit": 3900},
    },
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
        # Label matured model predictions at 19:15, BEFORE the 19:30 ingest.
        # ingest_daily applies bank actions (SETTLED / WRITTEN_OFF / RECALL /
        # DECEASED) to cases; running after it would censor a prediction on an
        # action that landed after its own outcome window had already closed.
        "model-outcome-labelling": {
            "task": "app.workers.tasks.model_outcomes.attach_model_outcomes",
            "schedule": crontab(hour=19, minute=15),
        },
        # 20:30 — analytics MV refresh (B13): after ingest, scoring and the 20:00
        # allocation; it also waits for allocation runs to leave RUNNING.
        "analytics-refresh": {
            "task": "app.workers.tasks.analytics_refresh.refresh_analytics",
            "schedule": crontab(hour=20, minute=30),
        },
        # 01:30 — partition maintenance (B12, core/partitions.py): pre-create
        # month partitions, report DEFAULT rows, apply DECIDED retention. Before
        # the 02:00 reconciliation, which reads yesterday's trail.
        "partition-maintenance": {
            "task": "app.workers.tasks.partition_maintenance.maintain_partitions",
            "schedule": crontab(hour=1, minute=30),
        },
        # Reconcile yesterday's beats — planned route against the GPS trail and
        # the visit timestamps that actually happened. At 2 AM, after the trail
        # for the day has finished arriving and before the 3 AM retention sweep
        # ages any of it out. Order matters: run it after the prune and the
        # evidence it measures would already be gone.
        "beat-reconciliation": {
            "task": "app.workers.tasks.beat_reconciliation.reconcile_beats",
            "schedule": crontab(hour=2, minute=0),
        },
        # Resolve promises whose date has passed, at 00:05 — after the day has
        # turned (so "two calendar days ago" is judged against the new date),
        # before the 02:00 reconciliation, 03:00 sweep and 06:00 beat push, and
        # well before the 09:00 reminders, which must not chase a promise the
        # calendar has already resolved. One grace day; see
        # services/ptp_lifecycle_service.py for the rule.
        "ptp-lifecycle-housekeeping": {
            "task": "app.workers.tasks.ptp_lifecycle.resolve_expired_promises",
            "schedule": crontab(hour=0, minute=5),
        },
        # Agent.status follows approved leave: ON_LEAVE while it covers today,
        # back to OFF_DUTY the day after. 00:10, after the PTP lifecycle.
        "leave-housekeeping": {
            "task": "app.workers.tasks.leave_housekeeping.sync_leave_statuses",
            "schedule": crontab(hour=0, minute=10),
        },
        # Monthly performance snapshot at midnight on 1st of each month
        "monthly-performance-snapshot": {
            "task": "app.workers.tasks.performance_snapshot.take_monthly_snapshot",
            "schedule": crontab(hour=0, minute=0, day_of_month=1),
        },
    },
)


# 2026-09-28 (B14): Celery worker processes run nightly jobs that legitimately
# take minutes; the API's 15 s statement timeout would kill them. Each worker
# process switches to JOB_STATEMENT_TIMEOUT_MS as it starts.
from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _use_job_timeouts(**_kwargs):
    from app.core.database import use_job_statement_timeout
    use_job_statement_timeout()
