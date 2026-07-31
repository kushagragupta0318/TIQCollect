from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.beat_generation.push_morning_beats", bind=True)
def push_morning_beats(self):
    from datetime import date
    from app.core.database import SessionLocal
    from app.models.beat import Beat, BeatStatus
    from app.models.agent import Agent, AgentStatus

    db = SessionLocal()
    try:
        today = date.today()
        beats = db.query(Beat).filter(
            Beat.beat_date == today,
            Beat.status == BeatStatus.PLANNED,
        ).all()

        pushed = 0
        for beat in beats:
            agent = db.get(Agent, beat.agent_id)
            if agent and agent.fcm_token:
                # In production: send FCM push notification
                logger.info("beat_push.sent", agent_id=beat.agent_id, cases=beat.total_cases)
                pushed += 1

        logger.info("morning_beat_push.complete", beats_pushed=pushed)
        return {"beats_pushed": pushed}
    finally:
        db.close()
