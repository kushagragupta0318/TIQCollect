# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B06) — collections.ptps (docs/DATA-MODEL-V2.md §4.3): tenant
#   columns with composite FKs, money as NUMERIC(14,2), and (case_id,
#   created_at) indexed because that is the ML adapter's point-in-time query
#   (created_at is a PIT key and is copied verbatim by the v1→v2 transform).
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import datetime, date
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKeyConstraint, Index, SmallInteger, Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import PUBLIC, Base, Money, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


class PTPStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    HONORED = "HONORED"
    BROKEN = "BROKEN"
    PARTIALLY_HONORED = "PARTIALLY_HONORED"
    EXPIRED = "EXPIRED"
    RESCHEDULED = "RESCHEDULED"


PTP_STATUS_SQL = SAEnum(PTPStatus, name="ptp_status_enum", schema=PUBLIC, metadata=Base.metadata)


def promise_is_for_money(amount) -> bool:
    """The product's rule for a promise: it is for some money. The ONE
    definition of what ck_ptps_committed_positive enforces, rounded first to
    the column's 2 decimal places as NUMERIC(14,2) stores it (0.004 is stored
    as 0.00 and refused). The ledger simulator's panel and materialiser both
    filter through this (2026-09-28), so a promise the product cannot hold is
    in neither."""
    return round(float(amount), 2) > 0

class PTP(Base, UUIDPrimaryKey, TimestampMixin):
    """Promise to Pay — customer's commitment to pay by a specific date."""
    __tablename__ = "ptps"
    __tenant_parents__ = (("case_id", "Case"), ("agent_id", "Agent"))

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    visit_id: Mapped[str | None] = mapped_column(UUIDType)
    agent_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    # v2_0015 (P7 offline outbox): the client's id for this submission, so a replay hours later
    # returns the row it already made instead of writing a second one.
    client_submission_id: Mapped[str | None] = mapped_column(UUIDType, nullable=True)

    committed_amount: Mapped[float] = mapped_column(Money, nullable=False)
    committed_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_paid_amount: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)

    status: Mapped[PTPStatus] = mapped_column(PTP_STATUS_SQL, default=PTPStatus.ACTIVE, nullable=False)

    # Customer's verbal/written reason for PTP
    customer_reason: Mapped[str | None] = mapped_column(Text)
    agent_notes: Mapped[str | None] = mapped_column(Text)

    # Follow-up
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Rescheduling
    reschedule_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    parent_ptp_id: Mapped[str | None] = uuid_fk("collections.ptps.id", nullable=True)

    case: Mapped["Case"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Case", back_populates="ptps", primaryjoin="PTP.case_id == Case.id", foreign_keys="[PTP.case_id]")
    visit: Mapped["Visit | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Visit", back_populates="ptp", primaryjoin="PTP.visit_id == Visit.id", foreign_keys="[PTP.visit_id]")

    __table_args__ = (
        ForeignKeyConstraint(["case_id", "agency_id"], ["collections.cases.id", "collections.cases.agency_id"]),
        ForeignKeyConstraint(["visit_id", "case_id"], ["collections.visits.id", "collections.visits.case_id"]),
        ForeignKeyConstraint(["agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"]),
        CheckConstraint("committed_amount > 0", name="committed_positive"),   # == promise_is_for_money
        Index("ix_ptp_committed_date", "agency_id", "committed_date", "status"),
        Index("ix_ptp_agent", "agent_id", "status"),
        Index(None, "case_id", "created_at"),
        Index("uq_ptps_agent_id_client_submission_id", "agent_id", "client_submission_id", unique=True,
              postgresql_where=text("client_submission_id IS NOT NULL"),
              sqlite_where=text("client_submission_id IS NOT NULL")),
        {"schema": "collections"},
    )
