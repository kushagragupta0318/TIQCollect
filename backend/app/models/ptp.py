import enum
from datetime import datetime, date
from sqlalchemy import String, Float, Boolean, Enum as SAEnum, ForeignKey, Index, Text, Date, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class PTPStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    HONORED = "HONORED"
    BROKEN = "BROKEN"
    PARTIALLY_HONORED = "PARTIALLY_HONORED"
    EXPIRED = "EXPIRED"
    RESCHEDULED = "RESCHEDULED"


class PTP(Base, UUIDPrimaryKey, TimestampMixin):
    """Promise to Pay — customer's commitment to pay by a specific date."""
    __tablename__ = "ptps"

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    visit_id: Mapped[str | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)

    committed_amount: Mapped[float] = mapped_column(Float, nullable=False)
    committed_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    actual_paid_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    status: Mapped[PTPStatus] = mapped_column(
        SAEnum(PTPStatus, name="ptp_status_enum"), default=PTPStatus.ACTIVE, nullable=False
    )

    # Customer's verbal/written reason for PTP
    customer_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Follow-up
    follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Rescheduling
    reschedule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parent_ptp_id: Mapped[str | None] = mapped_column(ForeignKey("ptps.id"), nullable=True)

    case: Mapped["Case"] = relationship("Case", back_populates="ptps")  # type: ignore[name-defined]  # noqa: F821
    visit: Mapped["Visit | None"] = relationship("Visit", back_populates="ptp")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_ptp_committed_date", "committed_date", "status"),
        Index("ix_ptp_agent", "agent_id", "status"),
    )
