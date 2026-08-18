from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.performance_snapshot.take_monthly_snapshot", bind=True)
def take_monthly_snapshot(self):
    from datetime import date, timedelta
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
        month_str = last_month.strftime("%Y-%m")
        month_start = last_month.replace(day=1)
        month_end = date.today().replace(day=1)

        agents = db.query(Agent).all()
        snapshotted = 0

        for agent in agents:
            payments = db.query(Payment).filter(
                Payment.agent_id == agent.id,
                Payment.payment_date >= month_start.isoformat(),
                Payment.payment_date < month_end.isoformat(),
                Payment.status == PaymentStatus.VERIFIED,
            ).all()

            total_collected = sum(p.amount for p in payments)
            cases_assigned = db.query(Case).filter(
                Case.agent_id == agent.id,
                Case.allocation_date >= month_start.isoformat(),
                Case.allocation_date < month_end.isoformat(),
            ).count()

            ptps_set = db.query(PTP).filter(
                PTP.agent_id == agent.id,
                PTP.created_at >= month_start.isoformat(),
                PTP.created_at < month_end.isoformat(),
            ).count()
            # Bounded to PTPs RAISED in this month. Without the upper bound and
            # the start filter this counted every honoured PTP the agent had
            # ever set against one month's total — a number that only climbs,
            # and can exceed ptps_set.
            ptps_honored = db.query(PTP).filter(
                PTP.agent_id == agent.id,
                PTP.created_at >= month_start.isoformat(),
                PTP.created_at < month_end.isoformat(),
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
                    Visit.check_in_time >= month_start.isoformat(),
                    Visit.check_in_time < month_end.isoformat(),
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
                month=month_str,
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
