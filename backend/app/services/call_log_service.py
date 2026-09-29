"""Recording a phone call to a borrower (POST /agent/cases/{id}/call-log).

2026-09-29 (I02): moved out of endpoints/agent.py unchanged, then given the
offline outbox's replay path: a repeated client_submission_id returns the row
already stored, and a late call is judged at its capture time
(services/capture_time.py, docs/adr/0011-offline-outbox.md).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.errors import AppException, ErrorCode
from app.models.call_log import CallLog
from app.models.case import Case
from app.services.borrower_stance import check_call_stance
from app.services.capture_time import judge_capture, note_delivered
from app.services.scope import agent_case_or_404


class CallLogService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def log_call(self, agent, case_id: str, req, *, token_device_id: str | None = None) -> dict:
        csid = req.client_submission_id
        if csid:
            stored = self._by_submission(agent, csid)
            if stored is not None:
                return self._repeat(stored, case_id)
        capture = judge_capture(self.db, agent, captured_at=req.captured_at, device_seq=req.device_seq,
                                item_device_id=req.device_id, token_device_id=token_device_id,
                                now=datetime.now(timezone.utc))
        case = agent_case_or_404(self.db, agent, case_id,
                                 options=(joinedload(Case.customer), joinedload(Case.loan)),
                                 on_day=capture.day if capture.late else None)
        # ML-1: a stance only on an answered call — nobody said anything otherwise.
        check_call_stance(req.borrower_disposition, outcome=req.outcome)

        log = CallLog(
            case_id=case.id,
            agent_id=agent.id,
            customer_id=case.customer_id,
            called_at=capture.at,
            client_submission_id=csid,
            duration_seconds=req.duration_seconds,
            outcome=req.outcome,
            phone_used=req.phone_used,
            customer_response_notes=req.customer_response_notes,
            visit_feasible_today=req.visit_feasible_today,
            best_time_to_visit=req.best_time_to_visit,
            available_from=req.available_from,
            available_until=req.available_until,
            blocked_until_date=req.blocked_until_date,
            alternate_location_hint=req.alternate_location_hint,
            payment_intent_signalled=req.payment_intent_signalled,
            verbal_payment_date=req.verbal_payment_date,
            ai_intel_summary=req.ai_intel_summary,
            borrower_disposition=req.borrower_disposition,
        )
        self.db.add(log)
        note_delivered(capture)
        try:
            self.db.commit()
        except IntegrityError:
            # Two deliveries of one key raced and the other landed first.
            self.db.rollback()
            stored = self._by_submission(agent, csid) if csid else None
            if stored is None:
                raise
            return self._repeat(stored, case_id)
        self.db.refresh(log)
        return self._response(log)

    def _by_submission(self, agent, client_submission_id: str) -> CallLog | None:
        return (self.db.query(CallLog)
                .filter(CallLog.agent_id == agent.id, CallLog.client_submission_id == client_submission_id)
                .first())

    def _repeat(self, stored: CallLog, case_id: str) -> dict:
        if str(stored.case_id) != str(case_id):
            raise AppException(409, ErrorCode.IDEMPOTENCY_KEY_REUSED,
                               "This submission id was already used for another case.")
        return self._response(stored)

    @staticmethod
    def _response(log: CallLog) -> dict:
        return {"id": log.id, "called_at": log.called_at.isoformat(), "outcome": log.outcome}
