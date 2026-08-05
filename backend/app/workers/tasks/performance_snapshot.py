from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.performance_snapshot.take_monthly_snapshot", bind=True)
def take_monthly_snapshot(self):
    from datetime import date, timedelta
    from app.core.database import SessionLocal
    from app.models.agent import Agent, AgentPerformance, AgentTier
    from app.models.case import Case, CaseStatus
    from app.models.payment import Payment, PaymentStatus
    from app.models.ptp import PTP, PTPStatus
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
            ).count()
            ptps_honored = db.query(PTP).filter(
                PTP.agent_id == agent.id,
                PTP.status == PTPStatus.HONORED,
            ).count()

            collection_rate = (total_collected / agent.current_month_collections) if agent.current_month_collections > 0 else 0.0

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
