# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. photo-upload-url/photos/recording-upload-url/
#   recording-urls/transcribe/transcribe-audio moved out of endpoints/agent.py
#   as MediaService — a direct extraction, logic unchanged, not a rewrite.
#   _photo_entry/_photos_from_visits moved here too (also used by
#   get_case_detail in agent.py, which imports MediaService.photos_from_visits
#   for that one call rather than duplicating the logic). Full detail + why:
#   /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
Photo/recording upload URLs, playback URLs, and transcription for visits.

Router in endpoints/agent.py stays thin: fetch the current agent, delegate
to MediaService, return the result. Same router/service split already
applied to CaseService/VisitService/AuthService.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.scope import agent_case_or_404
from app.core import storage
from app.core.config import settings
from app.models.agent import Agent
from app.models.case import Case
from app.models.visit import Visit


class MediaService:
    _SUBJECT_MAP = {"agent": "AGENT_SELFIE", "borrower": "BORROWER", "object": "VEHICLE_ASSET", "signature": "SIGNATURE"}
    _VALID_SUBJECTS = set(_SUBJECT_MAP.keys())
    # signature is a PNG canvas capture (customer consent artifact), not a JPEG camera photo
    _SUBJECT_CONTENT_TYPE = {"signature": ("image/png", "png")}

    def __init__(self, db: Session):
        self.db = db

    def get_photo_upload_url(self, agent: Agent, case_id: str, subject: str) -> dict:
        if subject not in self._VALID_SUBJECTS:
            raise HTTPException(status_code=400, detail=f"subject must be one of: {', '.join(self._VALID_SUBJECTS)}")
        case = agent_case_or_404(self.db, agent, case_id)   # A03: the one rule

        photo_type_str = self._SUBJECT_MAP[subject]
        content_type, ext = self._SUBJECT_CONTENT_TYPE.get(subject, ("image/jpeg", "jpg"))
        key = storage.photo_key(case_id, photo_type_str, ext=ext)
        upload_url = storage.presigned_upload_url(key, content_type=content_type, expires_minutes=15)
        return {"upload_url": upload_url, "key": key, "photo_type": photo_type_str}

    def get_case_photos(self, agent: Agent, case_id: str) -> list[dict]:
        """Return latest geo-tagged photo per type for this case, extracted from visits."""
        from sqlalchemy.orm import joinedload
        case = agent_case_or_404(self.db, agent, case_id, options=(joinedload(Case.visits),))   # A03
        return self.photos_from_visits(case.visits)

    def get_recording_upload_url(self, agent: Agent, case_id: str, recorder: str) -> dict:
        if recorder not in ("agent", "borrower"):
            raise HTTPException(status_code=400, detail="recorder must be 'agent' or 'borrower'")
        case = agent_case_or_404(self.db, agent, case_id)   # A03: the one rule

        key = storage.recording_key(case_id, recorder, ext="webm")
        upload_url = storage.presigned_upload_url(key, content_type="audio/webm", expires_minutes=30)
        return {"upload_url": upload_url, "key": key}

    def get_recording_playback_urls(self, agent: Agent, visit_id: str) -> dict:
        visit = self.db.query(Visit).filter(Visit.id == visit_id, Visit.agent_id == agent.id).first()
        if not visit:
            raise HTTPException(status_code=404, detail="Visit not found")

        result: dict = {}
        if visit.agent_recording_key:
            result["agent_recording_url"] = storage.presigned_download_url(visit.agent_recording_key, expires_minutes=60)
        if visit.borrower_recording_key:
            result["borrower_recording_url"] = storage.presigned_download_url(visit.borrower_recording_key, expires_minutes=60)
        return result

    def queue_visit_transcription(self, agent: Agent, visit_id: str, recorder: str) -> dict:
        """Enqueue transcription; returns immediately with a task id.

        Runs as a background task rather than inline — self-hosted Whisper
        (TRANSCRIPTION_PROVIDER=local) can take meaningfully longer than the
        hosted API, and this endpoint shouldn't block on inference either way.
        Poll the visit/case detail endpoints for agent_recording_transcript /
        borrower_recording_transcript once the task completes.
        """
        if recorder not in ("agent", "borrower", "both"):
            raise HTTPException(status_code=400, detail="recorder must be 'agent', 'borrower', or 'both'")
        if settings.TRANSCRIPTION_PROVIDER == "openai" and not settings.OPENAI_API_KEY:
            raise HTTPException(status_code=503, detail="Transcription service not configured (missing OPENAI_API_KEY)")

        visit = self.db.query(Visit).filter(Visit.id == visit_id, Visit.agent_id == agent.id).first()
        if not visit:
            raise HTTPException(status_code=404, detail="Visit not found")

        has_target = (
            (recorder in ("agent", "both") and visit.agent_recording_key)
            or (recorder in ("borrower", "both") and visit.borrower_recording_key)
        )
        if not has_target:
            raise HTTPException(status_code=404, detail="No recordings found to transcribe")

        from app.workers.tasks.transcription import transcribe_visit_recording_task
        task = transcribe_visit_recording_task.delay(visit_id, recorder)
        return {"status": "queued", "task_id": task.id}

    @staticmethod
    def transcribe_audio_adhoc(audio_bytes: bytes, filename: str) -> str:
        """Transcribe (and translate to English) a short voice-note clip, synchronously.

        Unlike queue_visit_transcription, this runs inline: voice notes are
        short (seconds, not a full visit recording), the caller is a UI
        element actively waiting on the result, and there's no Visit row yet
        to attach a queued task to.
        """
        from app.core.transcription import transcribe as run_transcription
        return run_transcription(audio_bytes, filename)

    @staticmethod
    def photo_entry(visit: Visit, subject: str) -> dict | None:
        """Build a photo metadata dict for one subject ('agent'/'borrower'/'object') from a visit row."""
        key = getattr(visit, f"{subject}_photo_key", None)
        if not key:
            return None
        try:
            view_url = storage.presigned_download_url(key, expires_minutes=60)
        except Exception:
            view_url = None
        captured_raw = getattr(visit, f"{subject}_photo_captured_at", None)
        return {
            "photo_type": {"agent": "AGENT_SELFIE", "borrower": "BORROWER", "object": "VEHICLE_ASSET"}[subject],
            "storage_key": key,
            "view_url": view_url,
            "latitude": getattr(visit, f"{subject}_photo_lat", None),
            "longitude": getattr(visit, f"{subject}_photo_lon", None),
            "accuracy_metres": getattr(visit, f"{subject}_photo_accuracy", None),
            "altitude_metres": getattr(visit, f"{subject}_photo_altitude", None),
            "captured_at": captured_raw.isoformat() if captured_raw else None,
            "sha256": getattr(visit, f"{subject}_photo_sha256", None),
            "device_id": visit.device_id,
            "visit_id": visit.id,
            "agent_id": visit.agent_id,
            "case_id": visit.case_id,
            "status": "UPLOADED",
            "visit_date": visit.check_in_time.isoformat(),
        }

    @classmethod
    def photos_from_visits(cls, visits: list[Visit]) -> list[dict]:
        """Return latest photo per type across all visits, most-recent visit first."""
        sorted_visits = sorted(visits, key=lambda v: v.check_in_time, reverse=True)
        seen: set[str] = set()
        result: list[dict] = []
        for v in sorted_visits:
            for subject in ("agent", "borrower", "object"):
                photo_type = {"agent": "AGENT_SELFIE", "borrower": "BORROWER", "object": "VEHICLE_ASSET"}[subject]
                if photo_type not in seen:
                    entry = cls.photo_entry(v, subject)
                    if entry:
                        entry["is_latest"] = True
                        seen.add(photo_type)
                        result.append(entry)
        return result
