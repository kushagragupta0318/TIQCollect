"""
Nightly Case Allocation & Beat Planning Task.
Runs nightly at 8:00 PM IST (Mon-Sat, Sat plans Mon).
Assigns next-day field work per agency manager using PlannerService.
"""
from app.workers.celery_app import celery_app
import structlog
from datetime import date

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.allocation.run_nightly_allocation", bind=True, max_retries=3)
def run_nightly_allocation(self, strategy: str = "SMART", plan_date_str: str | None = None):
    from app.core.database import SessionLocal
    from app.models.user import User, UserRole
    from app.services.planner_service import PlannerService, get_target_plan_date

    target_date = date.fromisoformat(plan_date_str) if plan_date_str else get_target_plan_date()
    logger.info("nightly_allocation.start", target_date=str(target_date), strategy=strategy)

    db = SessionLocal()
    results = {}
    try:
        managers = db.query(User).filter(
            User.role.in_([UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN]),
            User.is_active.is_(True),
        ).all()

        for manager in managers:
            planner = PlannerService(db, manager_user_id=manager.id)
            run = planner.plan_next_day(
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

        logger.info("nightly_allocation.complete", target_date=str(target_date), results=results)
        return results
    except Exception as exc:
        logger.error("nightly_allocation.error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()

