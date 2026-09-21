# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-21 — New file. Leave had a calendar and no door.
#
#   `beats.is_leave_day / leave_type / leave_remarks` have existed since the
#   seed, and the Analytics Team Duty calendar and Leave summary read them —
#   but the ONLY writer was scripts/seed_data.py. No screen, endpoint or task
#   could record a real absence, `AgentStatus.ON_LEAVE` was never set by any
#   code path, and `AuditAction.AGENT_STATUS_CHANGED` was declared and never
#   written. Every leave the manager saw was the seed's invention.
#
#   A request is its own object because a pending request has no beat: it may
#   be rejected, and writing a leave beat at request time would show
#   unapproved leave as leave. The beats are written ON APPROVAL, in exactly
#   the seed's shape, so every reader of leave stays as it is.
#
#   ONE VOCABULARY. `LeaveType` here is the four strings the seed has always
#   written to `beats.leave_type`; the beat column stays a String so no enum
#   migration is needed, and services/leave_service.py is the one place that
#   copies the value across.
# ───────────────────────────────────────────────────────────────────────────
import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class LeaveType(str, enum.Enum):
    SICK_LEAVE = "SICK_LEAVE"
    CASUAL_LEAVE = "CASUAL_LEAVE"
    EARNED_LEAVE = "EARNED_LEAVE"
    ABSENT = "ABSENT"            # manager-marked no-show; an agent cannot request it


# What an agent may ask for. ABSENT is a manager's word about an agent, never
# an agent's word about themselves.
REQUESTABLE_LEAVE_TYPES = frozenset({LeaveType.SICK_LEAVE, LeaveType.CASUAL_LEAVE, LeaveType.EARNED_LEAVE})


class LeaveStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"      # withdrawn by the agent while REQUESTED, or revoked by the manager after APPROVED


# The two states a request can still move out of.
OPEN_LEAVE_STATUSES = frozenset({LeaveStatus.REQUESTED, LeaveStatus.APPROVED})


class LeaveRequest(Base, UUIDPrimaryKey, TimestampMixin):
    """One agent's request (or a manager's record) to be off the field for a
    date range. Inclusive dates. Approval writes one leave Beat per day and
    records their ids in `beat_ids`, so revoking removes exactly those."""
    __tablename__ = "leave_requests"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    # Denormalised from the agent at write time, like Agent.manager_user_id:
    # the manager router scopes on it directly.
    manager_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    leave_type: Mapped[LeaveType] = mapped_column(SAEnum(LeaveType, name="leave_type_enum"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[LeaveStatus] = mapped_column(
        SAEnum(LeaveStatus, name="leave_status_enum"), default=LeaveStatus.REQUESTED, nullable=False, index=True
    )
    # Who filed it: the agent's user for a request, the manager's for a mark.
    requested_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The leave beats approval wrote, so a revoke undoes exactly them.
    beat_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    agent = relationship("Agent", foreign_keys=[agent_id])

    __table_args__ = (
        Index("ix_leave_agent_dates", "agent_id", "from_date", "to_date"),
        Index("ix_leave_manager_status", "manager_user_id", "status"),
    )
