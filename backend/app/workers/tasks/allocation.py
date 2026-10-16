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
def run_nightly_allocation(self, strategy: str = "SMART", plan_date_str: str | None = None,
                           force: bool = False):
    """Plan tomorrow for every active agency manager.

    `force=True` plans even when the evening's upstream stages have not
    finished. It exists for an operator re-running the night by hand; the
    scheduled 20:00 run never sets it, because planning on a book the feed
    never updated hands agents a day of work aimed at yesterday's balances.
    """
    from app.core.database import SessionLocal
    from app.models.allocation_run import AllocationRun, AllocationRunStatus
    from app.models.tenancy import Agency
    from app.models.user import User, UserRole
    from app.services.pipeline_readiness import allocation_gate, business_date, gate_summary
    from app.services.planner_service import PlannerService, get_target_plan_date

    # The IST business date the EVENING belongs to — what the feed and the
    # scores are stamped with. Distinct from target_date, which is the day
    # being planned FOR (tomorrow, or Monday from a Saturday).
    tonight = business_date()
    target_date = date.fromisoformat(plan_date_str) if plan_date_str else get_target_plan_date(tonight)
    logger.info("nightly_allocation.start", target_date=str(target_date),
                business_date=str(tonight), strategy=strategy)

    db = SessionLocal()
    results: dict[str, dict] = {}
    failures: dict[str, str] = {}
    try:
        # ── UPSTREAM GATE ───────────────────────────────────────────────────
        # Both evening stages must have finished for TONIGHT's business date.
        # Checked before a single manager is planned, and checked against
        # persisted state rather than task results, so a worker restart between
        # 19:45 and 20:00 does not make the chain look complete.
        #
        # Refused, NOT retried: neither a missing feed nor missing scores is a
        # transient fault, and `force_replan=True` below means a retry would
        # discard beats that did plan. The refusal becomes a row per manager —
        # the lesson of the 2026-09-02 run in this file's changelog, where a
        # failed night left nothing behind but a stack trace.
        # An EXPLICIT plan_date_str is an operator-chosen run (a re-plan, a
        # backfill, a named date from the Field Plan page). The gate asks
        # whether TONIGHT's feed landed, which is not the question for a run
        # aimed at some other date — so naming a date opts out, exactly as
        # force does. The scheduled 20:00 run passes neither.
        if not force and plan_date_str is None:
            ready, stages = allocation_gate(db, tonight)
            if not ready:
                summary = gate_summary(stages)
                logger.error("nightly_allocation.blocked_upstream_not_ready",
                             target_date=str(target_date), business_date=str(tonight), **summary)
                blocked = _record_blocked(db, AllocationRun, AllocationRunStatus,
                                          target_date, strategy, tonight, summary)
                return {"target_date": str(target_date), "business_date": str(tonight),
                        "blocked": True, "planned": {}, "failed": {},
                        "runs_recorded": blocked, **summary}
        try:
            # MED, coordinator audit on c041835: this used to select by role
            # and is_active alone, with no Agency join at all — a manager
            # whose agency had been SUSPENDED or OFFBOARDED was still
            # planned every night. Inner-joining Agency and requiring
            # ACTIVE means a manager whose agency_id is somehow NULL (should
            # not occur — ck_users_role_scope requires it) is excluded the
            # same way, rather than needing a separate check.
            managers = db.query(User).join(Agency, User.agency_id == Agency.id).filter(
                # AGENCY_MANAGER only. A field plan is built from a roster,
                # and agents report to a MANAGER — an agency admin has none of
                # their own, so planning for them produced an allocation run of
                # 0 cases / 0 agents every night (17 such rows on the dev book
                # before this). Admins read and generate a manager's plan
                # through the Field Plan page instead, naming whose it is.
                User.role == UserRole.AGENCY_MANAGER,
                User.is_active.is_(True),
                Agency.status == "ACTIVE",
            ).all()
        except Exception as exc:
            # Nothing manager-specific to record against — the run could not
            # even establish who to plan for, so this one IS worth retrying.
            logger.error("nightly_allocation.managers_unavailable", error=str(exc))
            raise self.retry(exc=exc, countdown=300)

        # A04 (standalone plan) — group by agency and plan each agency's
        # managers together, rather than one flat list spanning every
        # agency (and every bank) in the deployment. PlannerService itself
        # now binds its candidate pool to the manager's own agency_id
        # (services/planner_service.py), so this loop shape is not what
        # closes the cross-tenant leak — it makes the tenant boundary a
        # structural property of WHO gets planned together, rather than
        # something that only lived inside one query deep in the service,
        # so a future change to this task (a per-agency lock, a per-agency
        # summary, a partial retry) inherits the boundary instead of having
        # to remember it. The `by_agency` key can no longer be None in
        # practice — the inner join above already excludes a NULL
        # agency_id — but the dict grouping stays generic rather than
        # assuming that, the same way a defensive filter stays even once a
        # constraint makes its branch unreachable.
        by_agency: dict[str | None, list[User]] = {}
        for manager in managers:
            by_agency.setdefault(manager.agency_id, []).append(manager)

        for agency_id, agency_managers in by_agency.items():
            logger.info("nightly_allocation.agency_start", agency_id=agency_id,
                       target_date=str(target_date), managers=len(agency_managers))
            for manager in agency_managers:
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
                                 manager=manager.email, agency_id=agency_id,
                                 target_date=str(target_date),
                                 error=str(exc), exc_info=True)
                    _record_failure(db, AllocationRun, AllocationRunStatus,
                                    manager, target_date, strategy, exc)

        logger.info("nightly_allocation.complete", target_date=str(target_date),
                    agencies=len(by_agency), planned=len(results), failed=len(failures),
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


def _record_blocked(db, AllocationRun, AllocationRunStatus, target_date: date,
                    strategy: str, tonight: date, summary: dict) -> int:
    """One BLOCKED_UPSTREAM row per manager who would have been planned.

    WHY A ROW PER MANAGER, not one for the run. `allocation_runs` is keyed by
    manager (and the tenant listener fills bank_id/agency_id from that manager),
    and every surface that asks "what happened to my team tonight?" reads it
    that way. A single global row would be invisible to all of them — the same
    absence the 2026-09-02 incident in this file's changelog was about.

    Returns how many rows were written, so the task reports what it recorded
    rather than asserting it.
    """
    from app.models.tenancy import Agency
    from app.models.user import User, UserRole

    try:
        db.rollback()
        managers = db.query(User).join(Agency, User.agency_id == Agency.id).filter(
            User.role == UserRole.AGENCY_MANAGER,
            User.is_active.is_(True),
            Agency.status == "ACTIVE",
        ).all()
        for manager in managers:
            db.add(AllocationRun(
                id=str(uuid.uuid4()),
                manager_user_id=manager.id,
                plan_date=target_date,
                strategy=strategy,
                # FAILED, not a new status: ck_allocation_runs_status
                # (v2_0002_tables.py) permits only the four existing values, so
                # BLOCKED_UPSTREAM would need a migration to widen the CHECK.
                # The CAUSE is what distinguishes this from a planning crash,
                # and it is in summary_metadata below — queryable, and visible
                # on the surfaces that already read these rows.
                status=AllocationRunStatus.FAILED.value,
                total_cases_evaluated=0,
                total_cases_allocated=0,
                total_cases_deferred=0,
                total_cases_blocked=0,
                total_agents_planned=0,
                expected_recovery_total=0.0,
                summary_metadata={
                    "trigger": "nightly",
                    # The marker that tells a refused night from a crashed one.
                    "outcome": "BLOCKED_UPSTREAM",
                    "business_date": str(tonight),
                    "blocked_by": summary.get("blocked_by", []),
                    # The operator-facing reason, per stage, so the row explains
                    # itself without a log lookup.
                    "stages": summary.get("stages", []),
                },
            ))
        db.commit()
        return len(managers)
    except Exception as bookkeeping_exc:      # pragma: no cover - defensive
        logger.error("nightly_allocation.blocked_row_not_written", error=str(bookkeeping_exc))
        db.rollback()
        return 0
