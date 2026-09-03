# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-03 — Two faults, both found by asking why the 20:00 run on 2026-09-02
#   produced no plan.
#
#   ONE TRY/EXCEPT AROUND EVERY MANAGER. The loop planned for all managers
#   inside a single try, so the first one to raise ended the run for everybody
#   behind it. plan_next_day() raises ValueError by design whenever a beat for
#   the target date is already IN_PROGRESS or COMPLETED, which is a normal
#   condition for one team and has nothing to do with the others.
#
#   FAILURE LEFT NO TRACE. The except logged and re-raised into Celery's retry.
#   Nothing was written, so the only evidence a run had failed was a stack trace
#   in the worker's container logs:
#
#     [2026-09-02 14:30:00,004] Task run_nightly_allocation received
#     [2026-09-02 14:30:00,135] ERROR raised unexpected: AttributeError(
#         "type object 'AllocationOutcome' has no attribute 'DEFERRED_PTP'")
#
#   Dispatched exactly on time, dead 131ms later, and the manager simply found
#   no beats the next morning with nothing to explain why. (That particular
#   error was a stale worker process holding an import from before two enum
#   members were added — the members exist; the running process predated them.)
#
#   Now: each manager is planned independently, and a manager whose plan fails
#   gets a FAILED AllocationRun row carrying the error. The failure becomes a
#   row in the product instead of a line in a log file nobody reads.
# ───────────────────────────────────────────────────────────────────────────
"""
Nightly Case Allocation & Beat Planning Task.
Runs nightly at 8:00 PM IST (Mon-Sat, Sat plans Mon).
Assigns next-day field work per agency manager using PlannerService.
"""
import uuid
from datetime import date

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.allocation.run_nightly_allocation", bind=True, max_retries=3)
def run_nightly_allocation(self, strategy: str = "SMART", plan_date_str: str | None = None):
    from app.core.database import SessionLocal
    from app.models.allocation_run import AllocationRun, AllocationRunStatus
    from app.models.user import User, UserRole
    from app.services.planner_service import PlannerService, get_target_plan_date

    target_date = date.fromisoformat(plan_date_str) if plan_date_str else get_target_plan_date()
    logger.info("nightly_allocation.start", target_date=str(target_date), strategy=strategy)

    db = SessionLocal()
    results: dict[str, dict] = {}
    failures: dict[str, str] = {}
    try:
        try:
            managers = db.query(User).filter(
                User.role.in_([UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN]),
                User.is_active.is_(True),
            ).all()
        except Exception as exc:
            # Nothing manager-specific to record against — the run could not
            # even establish who to plan for, so this one IS worth retrying.
            logger.error("nightly_allocation.managers_unavailable", error=str(exc))
            raise self.retry(exc=exc, countdown=300)

        for manager in managers:
            try:
                run = PlannerService(db, manager_user_id=manager.id).plan_next_day(
                    plan_date=target_date,
                    strategy=strategy,
                    force_replan=True,
                )
                results[manager.email] = {
                    "run_id": run.id,
                    "allocated": run.total_cases_allocated,
                    "deferred": run.total_cases_deferred,
                    "blocked": run.total_cases_blocked,
                    "agents_planned": run.total_agents_planned,
                    "expected_recovery_total": run.expected_recovery_total,
                }
            except Exception as exc:
                failures[manager.email] = f"{type(exc).__name__}: {exc}"
                logger.error("nightly_allocation.manager_failed",
                             manager=manager.email, target_date=str(target_date),
                             error=str(exc), exc_info=True)
                _record_failure(db, AllocationRun, AllocationRunStatus,
                                manager, target_date, strategy, exc)

        logger.info("nightly_allocation.complete", target_date=str(target_date),
                    planned=len(results), failed=len(failures),
                    results=results, failures=failures)
        # Reported, never raised. Every failure is already a row, and retrying
        # the whole task would replan the managers that succeeded — force_replan
        # discards their beats and builds new ones for no reason.
        return {"target_date": str(target_date), "planned": results, "failed": failures}
    finally:
        db.close()


def _record_failure(db, AllocationRun, AllocationRunStatus,
                    manager, target_date: date, strategy: str, exc: Exception) -> None:
    """Write the failure as a row. Never let bookkeeping mask the real error.

    The session is rolled back first: plan_next_day may have died mid-flush, and
    a session in that state refuses every subsequent write, so the failure row
    itself would fail to save.
    """
    try:
        db.rollback()
        db.add(AllocationRun(
            id=str(uuid.uuid4()),
            manager_user_id=manager.id,
            plan_date=target_date,
            strategy=strategy,
            status=AllocationRunStatus.FAILED.value,
            total_cases_evaluated=0,
            total_cases_allocated=0,
            total_cases_deferred=0,
            total_cases_blocked=0,
            total_agents_planned=0,
            expected_recovery_total=0.0,
            summary_metadata={
                "error_type": type(exc).__name__,
                # Bounded: some SQLAlchemy errors carry the entire failing
                # statement, and this column is read by a UI.
                "error": str(exc)[:1000],
                "trigger": "nightly",
            },
        ))
        db.commit()
    except Exception as bookkeeping_exc:      # pragma: no cover - defensive
        logger.error("nightly_allocation.failure_row_not_written",
                     manager=manager.email, error=str(bookkeeping_exc))
        db.rollback()
