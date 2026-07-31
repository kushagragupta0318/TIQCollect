# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 — New file. Moves visit-recording transcription off the
#   synchronous request path: POST /agent/visits/{visit_id}/transcribe now
#   enqueues transcribe_visit_recording_task instead of blocking on
#   inference inline. Self-hosted Whisper (TRANSCRIPTION_PROVIDER=local) is
#   likely slower than the hosted API, so the endpoint that used to be fine
#   running sync (final_changes.md §7.4 already flagged this as an existing
#   risk even with the fast hosted API) needed this before local transcription
#   becomes a real option. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.transcription.transcribe_visit_recording_task", bind=True)
def transcribe_visit_recording_task(self, visit_id: str, recorder: str = "both"):
    from app.core import storage
    from app.core.database import SessionLocal
    from app.core.transcription import transcribe
    from app.models.visit import Visit

    db = SessionLocal()
    try:
        visit = db.query(Visit).filter(Visit.id == visit_id).first()
        if not visit:
            logger.warning("transcription.visit_not_found", visit_id=visit_id)
            return {"error": "visit not found"}

        result: dict = {}

        if recorder in ("agent", "both") and visit.agent_recording_key:
            audio_bytes = storage.download_bytes(visit.agent_recording_key)
            transcript = transcribe(audio_bytes, "agent.mp4")
            visit.agent_recording_transcript = transcript
            result["agent_transcript"] = transcript

        if recorder in ("borrower", "both") and visit.borrower_recording_key:
            audio_bytes = storage.download_bytes(visit.borrower_recording_key)
            transcript = transcribe(audio_bytes, "borrower.mp4")
            visit.borrower_recording_transcript = transcript
            result["borrower_transcript"] = transcript

        db.commit()
        logger.info("transcription.completed", visit_id=visit_id, fields=list(result.keys()))
        return result
    except Exception as exc:
        logger.error("transcription.failed", visit_id=visit_id, error=str(exc))
        raise
    finally:
        db.close()
