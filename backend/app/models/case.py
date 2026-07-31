import enum
from sqlalchemy import String, Float, Integer, Boolean, Enum as SAEnum, ForeignKey, Index, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class CaseStatus(str, enum.Enum):
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PTP_SET = "PTP_SET"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"
    WRITTEN_OFF = "WRITTEN_OFF"


class CasePriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EscalationReason(str, enum.Enum):
    CUSTOMER_HOSTILE = "CUSTOMER_HOSTILE"
    CUSTOMER_ABSCONDED = "CUSTOMER_ABSCONDED"
    DISPUTED_AMOUNT = "DISPUTED_AMOUNT"
    LEGAL_NOTICE_REQUIRED = "LEGAL_NOTICE_REQUIRED"
    PROPERTY_DISPUTE = "PROPERTY_DISPUTE"
    OTHER = "OTHER"


class Case(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "cases"

    case_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    loan_id: Mapped[str] = mapped_column(ForeignKey("loans.id"), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    assigned_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    status: Mapped[CaseStatus] = mapped_column(
        SAEnum(CaseStatus, name="case_status_enum"), default=CaseStatus.UNASSIGNED, nullable=False
    )
    priority: Mapped[CasePriority] = mapped_column(
        SAEnum(CasePriority, name="case_priority_enum"), default=CasePriority.MEDIUM, nullable=False
    )

    # Financial targets
    target_amount: Mapped[float] = mapped_column(Float, nullable=False)
    collected_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    waiver_approved: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Allocation metadata
    allocation_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    allocation_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_ml_allocated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_visits_allowed: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Resolution
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bank-provided collection metadata (sent with the portfolio)
    collection_stage: Mapped[str] = mapped_column(
        String(30), default="FIELD", nullable=False
    )  # SOFT_CALL / FIELD / PRE_LEGAL / LEGAL / NPA_RECOVERY / WRITTEN_OFF_RECOVERY
    bank_ptp_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    bank_ptp_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    bank_ptp_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ACTIVE / HONORED / BROKEN / EXPIRED
    bank_agent_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Agent handover (Phase 1D)
    handover_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Escalation
    is_escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalation_reason: Mapped[EscalationReason | None] = mapped_column(
        SAEnum(EscalationReason, name="escalation_reason_enum"), nullable=True
    )
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    customer: Mapped["Customer"] = relationship("Customer", back_populates="cases")  # type: ignore[name-defined]  # noqa: F821
    loan: Mapped["Loan"] = relationship("Loan", back_populates="cases")  # type: ignore[name-defined]  # noqa: F821
    agent: Mapped["Agent | None"] = relationship("Agent", back_populates="cases")  # type: ignore[name-defined]  # noqa: F821
    visits: Mapped[list["Visit"]] = relationship("Visit", back_populates="case", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="case", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    ptps: Mapped[list["PTP"]] = relationship("PTP", back_populates="case", lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_case_agent_status", "agent_id", "status"),
        Index("ix_case_status_priority", "status", "priority"),
        Index("ix_case_allocation_date", "allocation_date"),
    )
