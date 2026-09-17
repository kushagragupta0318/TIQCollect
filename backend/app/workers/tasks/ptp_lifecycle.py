# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-17 — New file. The nightly half of services/ptp_lifecycle_service.py.
#   Runs at 00:05 (celery_app.py). Per manager, like the 20:00 allocation task:
#   every write is scoped to one manager's agents, and agents with no manager
#   are counted and reported rather than silently processed or silently left.
# ───────────────────────────────────────────────────────────────────────────
"""Resolve ACTIVE promises whose committed date plus the grace day has passed."""
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


def run_for_all_managers(db, effective_date, *, source, dry_run=False) -> dict:
    """One `process_scope` call per manager. Returns the per-manager summaries
    and their totals. Shared by the task and the backfill script so the two
    iterate the tenants identically."""
    from app.models.agent import Agent
    from app.models.ptp import PTP, PTPStatus
    from app.models.user import User, UserRole
    from app.services import ptp_lifecycle_service as svc

    managers = (
        db.query(User)
        .filter(User.role.in_([UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN]), User.is_active == True)  # noqa: E712
        .order_by(User.id)
        .all()
    )
    per_manager = []
    totals = {"eligible": 0, "honored": 0, "partially_honored": 0, "broken": 0,
              "rescheduled_skipped": 0, "grace_period_skipped": 0, "failed": 0}
    for m in managers:
        agent_ids = [r[0] for r in db.query(Agent.id).filter(Agent.manager_user_id == m.id).all()]
        s = svc.process_scope(db, agent_ids=agent_ids, effective_date=effective_date,
                              source=source, dry_run=dry_run)
        per_manager.append({"manager_user_id": m.id, "agents": len(agent_ids), **s.as_dict(),
                            "transitions": s.transitions})
        for k in totals:
            totals[k] += getattr(s, k)

    # Visible, not processed: promises held by agents no manager owns. The
    # allocation task plans nothing for such agents either; leaving them out
    # keeps every write inside a tenant, and the count keeps it from being
    # a silent gap.
    orphan_overdue = int(
        db.query(PTP.id)
        .join(Agent, Agent.id == PTP.agent_id)
        .filter(Agent.manager_user_id.is_(None), PTP.status == PTPStatus.ACTIVE,
                PTP.committed_date <= svc.eligibility_cutoff(effective_date))
        .count()
    )
    return {"effective_date": effective_date.isoformat(), "cutoff": svc.eligibility_cutoff(effective_date).isoformat(),
            "dry_run": dry_run, "managers": len(managers), "per_manager": per_manager,
            "orphan_agent_overdue_skipped": orphan_overdue, **totals}


@celery_app.task(name="app.workers.tasks.ptp_lifecycle.resolve_expired_promises", bind=True)
def resolve_expired_promises(self, effective_date_str: str | None = None):
    from datetime import date
    from app.core.database import SessionLocal
    from app.services.ptp_lifecycle_service import SOURCE_NIGHTLY

    # The business date is the calendar date the task runs on — the same
    # convention as ptp_reminders and performance_snapshot. Overridable for a
    # replay, never guessed from elapsed hours.
    effective_date = date.fromisoformat(effective_date_str) if effective_date_str else date.today()
    db = SessionLocal()
    try:
        result = run_for_all_managers(db, effective_date, source=SOURCE_NIGHTLY)
        summary = {k: result[k] for k in ("effective_date", "cutoff", "managers", "eligible", "honored",
                                          "partially_honored", "broken", "rescheduled_skipped",
                                          "grace_period_skipped", "failed", "orphan_agent_overdue_skipped")}
        log = logger.error if result["failed"] else logger.info
        log("ptp_lifecycle.housekeeping", **summary)
        return summary
    finally:
        db.close()
