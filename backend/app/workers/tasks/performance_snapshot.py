from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.performance_snapshot.take_monthly_snapshot", bind=True)
def take_monthly_snapshot(self):
    from datetime import date, datetime, time, timedelta, timezone
    from sqlalchemy import func
    from app.core.database import SessionLocal
    from app.models.agent import Agent, AgentPerformance, AgentTier
    from app.models.case import Case, CaseStatus
    from app.models.payment import Payment, PaymentStatus
    from app.models.ptp import PTP, PTPStatus
    from app.models.visit import Visit
    import uuid

    db = SessionLocal()
    try:
        last_month = (date.today().replace(day=1) - timedelta(days=1))
        month_start = last_month.replace(day=1)
        month_end = date.today().replace(day=1)
        # Instants for the timestamp columns; the DATE columns compare to the
        # dates themselves (2026-09-24: they were compared as ISO strings).
        start_at = datetime.combine(month_start, time.min, tzinfo=timezone.utc)
        end_at = datetime.combine(month_end, time.min, tzinfo=timezone.utc)

        agents = db.query(Agent).all()
        snapshotted = 0

        for agent in agents:
            payments = db.query(Payment).filter(
                Payment.agent_id == agent.id,
                Payment.payment_date >= start_at,
                Payment.payment_date < end_at,
                Payment.status == PaymentStatus.VERIFIED,
            ).all()

            total_collected = sum(p.amount for p in payments)
            cases_assigned = db.query(Case).filter(
                Case.agent_id == agent.id,
                Case.allocation_date >= month_start,
                Case.allocation_date < month_end,
            ).count()

            # 2026-08-25: keyed on committed_date, not created_at, to match
            # _live_monthly_metrics in endpoints/manager.py. created_at is
            # `server_default now()` — the row-insert timestamp, not a business
            # date — so on a seeded book every PTP landed in the month the
            # database was created and every other month archived a zero.
            #
            # This table is read by GET /manager/reports/monthly and by the AI
            # narrative, neither of which goes through the live helper. Leaving
            # the two keyed differently meant the monthly report and the
            # Analytics page quoted different PTP conversion rates for the same
            # month, with nothing on either screen to say why.
            #
            # No upper maturity bound is needed here: this task runs on the 1st
            # for the month that has just ENDED, so every promise dated inside
            # that month has already come due. The live helper needs the bound
            # only because it also reports the month in progress.
            ptps_set = db.query(PTP).filter(
                PTP.agent_id == agent.id,
                PTP.committed_date >= month_start,
                PTP.committed_date < month_end,
            ).count()
            # Bounded to PTPs DUE in this month. Without the upper bound and
            # the start filter this counted every honoured PTP the agent had
            # ever set against one month's total — a number that only climbs,
            # and can exceed ptps_set.
            ptps_honored = db.query(PTP).filter(
                PTP.agent_id == agent.id,
                PTP.committed_date >= month_start,
                PTP.committed_date < month_end,
                PTP.status == PTPStatus.HONORED,
            ).count()

            # Target = target_amount over the DISTINCT cases the agent visited
            # in the month — the same definition as _live_monthly_metrics() in
            # endpoints/manager.py, so a month archived here is directly
            # comparable to the live months the Agents page computes.
            #
            # This previously read total_collected / agent.current_month_collections
            # — collected divided by collected, which is not a rate. It fed the
            # collection-rate gauge, the sparkline, the ranking score and the
            # tier assignment, and tier feeds case allocation.
            visited_case_ids = [
                r[0] for r in db.query(Visit.case_id).filter(
                    Visit.agent_id == agent.id,
                    Visit.check_in_time >= start_at,
                    Visit.check_in_time < end_at,
                ).distinct().all()
            ]
            total_target = float(
                db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
                .filter(Case.id.in_(visited_case_ids))
                .scalar() or 0.0
            ) if visited_case_ids else 0.0

            collection_rate = (total_collected / total_target) if total_target > 0 else 0.0

            # Compute ranking score (0–100)
            ranking = min(100.0, (collection_rate * 50) + (agent.current_month_visits / max(cases_assigned, 1) * 30) + (ptps_honored / max(ptps_set, 1) * 20))

            tier = AgentTier.TIER_1 if ranking >= 70 else (AgentTier.TIER_2 if ranking >= 40 else AgentTier.TIER_3)

            snap = AgentPerformance(
                id=str(uuid.uuid4()),
                agent_id=agent.id,
                month=month_start,
                total_visits=agent.current_month_visits,
                customer_met=0,
                total_collected=total_collected,
                ptps_set=ptps_set,
                ptps_honored=ptps_honored,
                collection_rate=collection_rate,
                ranking_score=ranking,
                tier=tier,
            )
            db.add(snap)

            # Update live agent ranking
            agent.ranking_score = ranking
            agent.tier = tier
            agent.current_month_visits = 0
            agent.current_month_collections = 0.0
            agent.current_month_ptps_set = 0
            agent.current_month_ptps_honored = 0
            snapshotted += 1

        db.commit()
        logger.info("monthly_snapshot.complete", agents=snapshotted, month=month_str)
        return {"snapshotted": snapshotted, "month": month_str}
    finally:
        db.close()
