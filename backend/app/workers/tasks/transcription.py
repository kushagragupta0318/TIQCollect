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

        # A recording that already has a transcript is not sent again: each call
        # is a paid speech-to-text minute, and the task can be queued twice for
        # one visit (a retried submit, a manual re-queue).
        for who in ("agent", "borrower"):
            if recorder not in (who, "both"):
                continue
            key = getattr(visit, f"{who}_recording_key")
            if not key:
                continue
            existing = getattr(visit, f"{who}_recording_transcript")
            if existing:
                result[f"{who}_transcript"] = existing
                result.setdefault("skipped", []).append(who)
                continue
            transcript = transcribe(storage.download_bytes(key), f"{who}.mp4")
            setattr(visit, f"{who}_recording_transcript", transcript)
            result[f"{who}_transcript"] = transcript

        db.commit()
        logger.info("transcription.completed", visit_id=visit_id, fields=list(result.keys()))
        return result
    except Exception as exc:
        logger.error("transcription.failed", visit_id=visit_id, error=str(exc))
        raise
    finally:
        db.close()
