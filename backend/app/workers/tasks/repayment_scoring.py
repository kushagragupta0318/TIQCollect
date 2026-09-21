"""Nightly repayment scoring.

Runs at 19:45 IST — deliberately between the daily bank ingest (~19:30, see
scripts/ingest_daily.py) and the nightly allocation (20:00, celery_app.py). The
order matters: scoring reads the DPD and amounts ingest has just written, and
allocation reads whatever the scoring leaves behind.

Two things this task does NOT do, both on purpose:

  * It writes Customer.risk_score only when settings.REPAYMENT_WRITE_RISK_SCORE
    is true, and that setting defaults to FALSE. Wiring this task is not the
    same act as enabling it — turning the column live is a deployment decision,
    made once, deliberately. With the gate closed the task still scores the
    whole book and still writes snapshots, so the training set accrues from the
    day this ships rather than from the day someone opts in.

  * It never touches Case.priority. Priority is written once at case creation
    (seed_data.py:1577, ingest_daily.py:437) and has never been recomputed for
    an open case; doing so is new behaviour and belongs to its own decision.

Idempotent by construction: the snapshot grain is (loan_id, as_of_date) with a
unique constraint, so running twice in one evening updates in place rather than
duplicating. That is what makes it safe for ingest to also call the service
inline later without the two fighting.
"""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(
    name="app.workers.tasks.repayment_scoring.run_nightly_repayment_scoring",
    bind=True,
    max_retries=3,
)
def run_nightly_repayment_scoring(self, dry_run: bool = False):
    """Score every loan, roll up to customers, snapshot what changed.

    `dry_run=True` computes and reports the full distribution without writing
    anything at all — useful for inspecting the shift from a running worker
    before the write gate is opened.
    """
    from datetime import date

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.services.repayment_service import RepaymentService

    db = SessionLocal()
    try:
        logger.info(
            "repayment_scoring.start",
            dry_run=dry_run,
            write_risk_score_enabled=settings.REPAYMENT_WRITE_RISK_SCORE,
            reprice_open_cases_enabled=settings.REPAYMENT_REPRICE_OPEN_CASES,
        )
        service = RepaymentService(db)
        today = date.today()
        result = service.rescore(as_of=today, dry_run=dry_run)

        # Labelling and pruning run AFTER scoring and only on a real run.
        # Order matters: scoring may write today's snapshot, and the labeller
        # only ever touches rows older than the outcome horizon, so the two
        # cannot collide. Pruning last, so a row labelled this minute is never
        # a candidate for deletion in the same pass.
        if not dry_run:
            result["labelling"] = service.attach_outcomes(as_of=today)
            result["pruning"] = service.prune_snapshots(as_of=today)
            db.commit()
            # 2026-09-21 — re-derive the stored case priority from tonight's DPD.
            # Runs after the 19:30 ingest has moved `loan.dpd`; one UPDATE for the
            # open book. See services/case_priority_service.py.
            from app.services.case_priority_service import restamp
            stamped = restamp(db)
            result["priority_restamped"] = stamped["changed"]
            logger.info("case_priority.restamped", open_cases=stamped["open_cases"], changed=stamped["changed"])
        logger.info("repayment_scoring.complete", **{
            k: v for k, v in result.items() if k != "distribution"})
        return result
    except Exception as exc:                     # noqa: BLE001
        db.rollback()
        # Retry rather than lose the evening. Scoring is idempotent on
        # (loan_id, as_of_date), so a partial run followed by a retry converges
        # instead of double-writing.
        logger.error("repayment_scoring.failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()
