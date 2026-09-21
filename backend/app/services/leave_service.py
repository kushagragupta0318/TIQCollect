# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-21 — New file. See models/leave_request.py for why leave needed a
#   door. This is the one place a leave request becomes leave beats, an
#   agent status and an audit row; the agent and manager endpoints only call
#   it, and the 00:10 housekeeping task calls `sync_statuses`.
#
#   THE SHAPE OF A LEAVE BEAT is the seed's, verbatim (scripts/seed_data.py
#   ~2979): status CANCELLED, is_leave_day True, leave_type, leave_remarks,
#   ordered_case_ids []. Every reader — the Analytics Team Duty calendar, the
#   Leave summary, GET /manager/analytics/team-attendance, the agent's own
#   availability calendar — already understands that shape, so approval
#   changes nothing downstream.
#
#   WHAT APPROVAL MAY MOVE, AND WHAT IT MAY NOT. A PLANNED beat on a leave day
#   is cancelled and its cases go back to the pool (agent_id cleared, status
#   UNASSIGNED) so the 20:00 run routes them to teammates — that is the
#   release-to-pool path, the ONLY way this service touches Case.agent_id, and
#   a test pins it. A beat with visits on it is the past and is never touched:
#   approving leave over a day that already happened is refused.
#
#   A rejected request writes nothing. A cancelled (revoked) approval removes
#   exactly the beats it wrote — `beat_ids` — and no other.
# ───────────────────────────────────────────────────────────────────────────
"""Leave requests: agent asks, manager decides, the calendar follows."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import AppException, ErrorCode
from app.models.agent import Agent, AgentStatus
from app.models.audit_log import AuditAction
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CaseStatus
from app.models.leave_request import (
    OPEN_LEAVE_STATUSES, REQUESTABLE_LEAVE_TYPES, LeaveRequest, LeaveStatus, LeaveType,
)
from app.models.visit import Visit

logger = structlog.get_logger()

MAX_LEAVE_DAYS = 30
# Same-day leave is an emergency and only sick leave is one. Casual and earned
# leave start tomorrow at the earliest, so the night's plan is never built for
# an agent who then says they are not coming.
SAME_DAY_ALLOWED = frozenset({LeaveType.SICK_LEAVE})


def _days(from_date: date, to_date: date):
    d = from_date
    while d <= to_date:
        yield d
        d += timedelta(days=1)


def _overlaps(db: Session, agent_id: str, from_date: date, to_date: date, exclude_id: str | None = None) -> LeaveRequest | None:
    q = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.agent_id == agent_id,
                LeaveRequest.status.in_(list(OPEN_LEAVE_STATUSES)),
                LeaveRequest.from_date <= to_date, LeaveRequest.to_date >= from_date)
    )
    if exclude_id:
        q = q.filter(LeaveRequest.id != exclude_id)
    return q.first()


def _validate_range(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "to_date must be on or after from_date")
    if (to_date - from_date).days + 1 > MAX_LEAVE_DAYS:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"A single leave request may cover at most {MAX_LEAVE_DAYS} days")


def serialize(r: LeaveRequest, agent_name: str | None = None) -> dict:
    return {
        "id": r.id, "agent_id": r.agent_id, "agent_name": agent_name,
        "from_date": r.from_date.isoformat(), "to_date": r.to_date.isoformat(),
        "days": (r.to_date - r.from_date).days + 1,
        "leave_type": r.leave_type.value, "reason": r.reason,
        "status": r.status.value,
        "requested_by_id": r.requested_by_id, "decided_by_id": r.decided_by_id,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decision_note": r.decision_note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


class LeaveService:
    def __init__(self, db: Session):
        self.db = db

    # ── agent side ──────────────────────────────────────────────────────

    def request(self, agent: Agent, *, from_date: date, to_date: date, leave_type: LeaveType,
                reason: str | None, today: date | None = None) -> LeaveRequest:
        today = today or date.today()
        _validate_range(from_date, to_date)
        if leave_type not in REQUESTABLE_LEAVE_TYPES:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "ABSENT is recorded by a manager, not requested")
        if from_date < today:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "Leave cannot start in the past")
        if from_date == today and leave_type not in SAME_DAY_ALLOWED:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "Only sick leave can start today; casual and earned leave start tomorrow at the earliest")
        if leave_type in (LeaveType.SICK_LEAVE, LeaveType.EARNED_LEAVE) and not (reason or "").strip():
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "A reason is required for sick and earned leave")
        clash = _overlaps(self.db, agent.id, from_date, to_date)
        if clash:
            raise AppException(409, ErrorCode.VALIDATION_ERROR,
                               f"Overlaps an existing {clash.status.value.lower()} leave {clash.from_date} to {clash.to_date}")
        r = LeaveRequest(
            id=str(uuid.uuid4()), agent_id=agent.id, manager_user_id=agent.manager_user_id,
            from_date=from_date, to_date=to_date, leave_type=leave_type, reason=(reason or "").strip() or None,
            status=LeaveStatus.REQUESTED, requested_by_id=agent.user_id, beat_ids=[],
        )
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        logger.info("leave.requested", request_id=r.id, agent_id=agent.id, from_date=str(from_date), to_date=str(to_date), leave_type=leave_type.value)
        return r

    def withdraw(self, agent: Agent, request_id: str) -> LeaveRequest:
        r = self.db.query(LeaveRequest).filter(LeaveRequest.id == request_id, LeaveRequest.agent_id == agent.id).first()
        if not r:
            raise AppException(404, ErrorCode.NOT_FOUND, "Leave request not found")
        if r.status != LeaveStatus.REQUESTED:
            raise AppException(409, ErrorCode.VALIDATION_ERROR, f"A {r.status.value.lower()} request cannot be withdrawn; ask your manager")
        r.status = LeaveStatus.CANCELLED
        r.decided_at = datetime.now(timezone.utc)
        r.decision_note = "Withdrawn by the agent"
        self.db.commit()
        return r

    def list_for_agent(self, agent: Agent) -> list[LeaveRequest]:
        return (
            self.db.query(LeaveRequest).filter(LeaveRequest.agent_id == agent.id)
            .order_by(LeaveRequest.from_date.desc()).limit(50).all()
        )

    # ── manager side ────────────────────────────────────────────────────

    def list_for_manager(self, manager_user_id: str, status: str | None = None) -> list[LeaveRequest]:
        q = self.db.query(LeaveRequest).filter(LeaveRequest.manager_user_id == manager_user_id)
        if status:
            q = q.filter(LeaveRequest.status == LeaveStatus(status))
        return q.order_by(LeaveRequest.status.asc(), LeaveRequest.from_date.asc()).limit(200).all()

    def _get_for_manager(self, manager_user_id: str, request_id: str) -> LeaveRequest:
        r = self.db.query(LeaveRequest).filter(LeaveRequest.id == request_id, LeaveRequest.manager_user_id == manager_user_id).first()
        if not r:
            # 404, never 403: a 403 confirms the id exists to another tenant.
            raise AppException(404, ErrorCode.NOT_FOUND, "Leave request not found")
        return r

    def approve(self, manager_user_id: str, request_id: str, note: str | None = None, today: date | None = None) -> dict:
        today = today or date.today()
        r = self._get_for_manager(manager_user_id, request_id)
        if r.status != LeaveStatus.REQUESTED:
            raise AppException(409, ErrorCode.VALIDATION_ERROR, f"Request is already {r.status.value.lower()}")
        agent = self.db.get(Agent, r.agent_id)
        released = self._write_leave_beats(agent, r, today)
        r.status = LeaveStatus.APPROVED
        r.decided_by_id = manager_user_id
        r.decided_at = datetime.now(timezone.utc)
        r.decision_note = (note or "").strip() or None
        self._apply_status(agent, r, today)
        self.db.commit()
        write_audit(self.db, action=AuditAction.AGENT_STATUS_CHANGED, user_id=manager_user_id,
                    entity_type="Agent", entity_id=agent.id,
                    details={"event": "LEAVE_APPROVED", "leave_request_id": r.id, "leave_type": r.leave_type.value,
                             "from_date": r.from_date.isoformat(), "to_date": r.to_date.isoformat(),
                             "beats_written": len(r.beat_ids), "cases_released_to_pool": released,
                             "agent_status": agent.status.value})
        logger.info("leave.approved", request_id=r.id, agent_id=agent.id, beats=len(r.beat_ids), released=released)
        return {"request": r, "cases_released": released}

    def reject(self, manager_user_id: str, request_id: str, note: str | None = None) -> LeaveRequest:
        r = self._get_for_manager(manager_user_id, request_id)
        if r.status != LeaveStatus.REQUESTED:
            raise AppException(409, ErrorCode.VALIDATION_ERROR, f"Request is already {r.status.value.lower()}")
        r.status = LeaveStatus.REJECTED
        r.decided_by_id = manager_user_id
        r.decided_at = datetime.now(timezone.utc)
        r.decision_note = (note or "").strip() or None
        self.db.commit()
        write_audit(self.db, action=AuditAction.AGENT_STATUS_CHANGED, user_id=manager_user_id,
                    entity_type="Agent", entity_id=r.agent_id,
                    details={"event": "LEAVE_REJECTED", "leave_request_id": r.id, "leave_type": r.leave_type.value,
                             "from_date": r.from_date.isoformat(), "to_date": r.to_date.isoformat(), "note": r.decision_note})
        return r

    def mark(self, manager_user_id: str, agent: Agent, *, from_date: date, to_date: date, leave_type: LeaveType,
             reason: str | None, today: date | None = None) -> dict:
        """Manager-recorded leave (including ABSENT for a no-show). Created
        APPROVED in one step; back-dating is allowed for ABSENT only, and never
        over a day with visits."""
        today = today or date.today()
        _validate_range(from_date, to_date)
        if from_date < today and leave_type != LeaveType.ABSENT:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "Only ABSENT may be recorded for a past date")
        clash = _overlaps(self.db, agent.id, from_date, to_date)
        if clash:
            raise AppException(409, ErrorCode.VALIDATION_ERROR,
                               f"Overlaps an existing {clash.status.value.lower()} leave {clash.from_date} to {clash.to_date}")
        r = LeaveRequest(
            id=str(uuid.uuid4()), agent_id=agent.id, manager_user_id=manager_user_id,
            from_date=from_date, to_date=to_date, leave_type=leave_type, reason=(reason or "").strip() or None,
            status=LeaveStatus.REQUESTED, requested_by_id=manager_user_id, beat_ids=[],
        )
        self.db.add(r)
        self.db.flush()
        released = self._write_leave_beats(agent, r, today)
        r.status = LeaveStatus.APPROVED
        r.decided_by_id = manager_user_id
        r.decided_at = datetime.now(timezone.utc)
        r.decision_note = "Recorded by manager"
        self._apply_status(agent, r, today)
        self.db.commit()
        write_audit(self.db, action=AuditAction.AGENT_STATUS_CHANGED, user_id=manager_user_id,
                    entity_type="Agent", entity_id=agent.id,
                    details={"event": "LEAVE_MARKED", "leave_request_id": r.id, "leave_type": leave_type.value,
                             "from_date": from_date.isoformat(), "to_date": to_date.isoformat(),
                             "beats_written": len(r.beat_ids), "cases_released_to_pool": released,
                             "agent_status": agent.status.value})
        return {"request": r, "cases_released": released}

    def revoke(self, manager_user_id: str, request_id: str, note: str | None = None, today: date | None = None) -> LeaveRequest:
        """Cancel an APPROVED leave: remove exactly the beats it wrote (never one
        with visits), restore the agent's status if today was inside it."""
        today = today or date.today()
        r = self._get_for_manager(manager_user_id, request_id)
        if r.status != LeaveStatus.APPROVED:
            raise AppException(409, ErrorCode.VALIDATION_ERROR, f"Only an approved leave can be revoked (this one is {r.status.value.lower()})")
        removed = 0
        for bid in list(r.beat_ids or []):
            b = self.db.get(Beat, bid)
            if b is None or not b.is_leave_day:
                continue
            self.db.delete(b)
            removed += 1
        r.status = LeaveStatus.CANCELLED
        r.decided_by_id = manager_user_id
        r.decided_at = datetime.now(timezone.utc)
        r.decision_note = (note or "").strip() or "Revoked by manager"
        agent = self.db.get(Agent, r.agent_id)
        if agent.status == AgentStatus.ON_LEAVE and r.from_date <= today <= r.to_date:
            agent.status = AgentStatus.OFF_DUTY
        self.db.commit()
        write_audit(self.db, action=AuditAction.AGENT_STATUS_CHANGED, user_id=manager_user_id,
                    entity_type="Agent", entity_id=r.agent_id,
                    details={"event": "LEAVE_REVOKED", "leave_request_id": r.id, "beats_removed": removed,
                             "agent_status": agent.status.value})
        return r

    # ── the mechanics ───────────────────────────────────────────────────

    def _write_leave_beats(self, agent: Agent, r: LeaveRequest, today: date) -> int:
        """One leave beat per day of the range, in the seed's shape. A PLANNED
        beat on a leave day is replaced and its cases released to the pool;
        a day that already has visits is refused. Returns cases released."""
        # Refuse if any day in range already has field work recorded.
        existing = {b.beat_date: b for b in self.db.query(Beat).filter(
            Beat.agent_id == agent.id, Beat.beat_date >= r.from_date, Beat.beat_date <= r.to_date).all()}
        for d, b in existing.items():
            if b.is_leave_day:
                continue
            worked = self.db.query(Visit.id).filter(
                Visit.agent_id == agent.id, Visit.check_in_time >= datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
                Visit.check_in_time < datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)).first()
            if worked or b.status in (BeatStatus.IN_PROGRESS, BeatStatus.COMPLETED):
                raise AppException(409, ErrorCode.VALIDATION_ERROR, f"{d.isoformat()} already has field work recorded; leave cannot cover a day that happened")

        released = 0
        ids: list[str] = []
        for d in _days(r.from_date, r.to_date):
            if d.weekday() == 6:
                continue   # Sunday is the scheduled rest day; no beat exists to cancel and none is written
            b = existing.get(d)
            if b is not None and b.is_leave_day:
                ids.append(b.id)          # already a leave beat (e.g. seeded) — adopt it
                continue
            if b is not None:
                # A planned day: hand its cases back to the pool for the next run.
                for cid in list(b.ordered_case_ids or []):
                    c = self.db.get(Case, cid)
                    if c is not None and c.agent_id == agent.id and c.status not in (CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF):
                        c.agent_id = None
                        c.status = CaseStatus.UNASSIGNED
                        released += 1
                b.status = BeatStatus.CANCELLED
                b.is_leave_day = True
                b.leave_type = r.leave_type.value
                b.leave_remarks = r.reason
                b.ordered_case_ids = []
                b.total_cases = 0
                ids.append(b.id)
                continue
            nb = Beat(
                id=str(uuid.uuid4()), agent_id=agent.id, beat_date=d,
                beat_number=f"LEAVE-{agent.employee_code}-{d.strftime('%Y%m%d')}",
                ordered_case_ids=[], total_cases=0, estimated_distance_km=0.0, estimated_duration_minutes=0,
                total_target_amount=0.0, status=BeatStatus.CANCELLED, is_ml_generated=False,
                is_leave_day=True, leave_type=r.leave_type.value, leave_remarks=r.reason,
            )
            self.db.add(nb)
            ids.append(nb.id)
        r.beat_ids = ids
        return released

    def _apply_status(self, agent: Agent, r: LeaveRequest, today: date) -> None:
        if r.from_date <= today <= r.to_date and agent.status in (AgentStatus.ON_DUTY, AgentStatus.OFF_DUTY):
            agent.status = AgentStatus.ON_LEAVE

    # ── housekeeping (00:10) ────────────────────────────────────────────

    def sync_statuses(self, today: date | None = None) -> dict:
        """Set ON_LEAVE for agents whose approved leave covers today; clear it
        for agents whose leave has ended. Idempotent; runs nightly and after
        every approval so the two agree."""
        today = today or date.today()
        on_leave_ids = {
            r.agent_id for r in self.db.query(LeaveRequest.agent_id).filter(
                LeaveRequest.status == LeaveStatus.APPROVED, LeaveRequest.from_date <= today, LeaveRequest.to_date >= today).all()
        }
        started, ended = 0, 0
        for a in self.db.query(Agent).filter(Agent.status != AgentStatus.SUSPENDED).all():
            if a.id in on_leave_ids and a.status != AgentStatus.ON_LEAVE:
                a.status = AgentStatus.ON_LEAVE
                started += 1
            elif a.id not in on_leave_ids and a.status == AgentStatus.ON_LEAVE:
                a.status = AgentStatus.OFF_DUTY
                ended += 1
        self.db.commit()
        return {"today": today.isoformat(), "on_leave": len(on_leave_ids), "set_on_leave": started, "cleared": ended}
