import enum
from datetime import datetime
from sqlalchemy import String, Float, Boolean, Enum as SAEnum, ForeignKey, Index, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class PaymentMode(str, enum.Enum):
    CASH = "CASH"
    UPI = "UPI"
    NEFT = "NEFT"
    RTGS = "RTGS"
    CHEQUE = "CHEQUE"
    DD = "DD"
    ONLINE = "ONLINE"
    # 2026-09-09 — the borrower paid the BANK directly; no agent collected it.
    # Arrives through scripts/ingest_daily.py, never through the agent app.
    # It exists because the outcome labeller derives recovery exclusively from
    # VERIFIED Payment rows, and a direct payer had no row at all — so the
    # clearest possible positive outcome was being labelled NOT_RECOVERED.
    BANK_DIRECT = "BANK_DIRECT"


class PaymentStatus(str, enum.Enum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    REVERSED = "REVERSED"


class Payment(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "payments"

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    visit_id: Mapped[str | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    # NULLABLE since 2026-09-09, and the NULL means something specific: nobody
    # collected this. A direct bank payment is real money against the case, but
    # it is not evidence about any agent, so attributing it to one would inflate
    # that agent's collections, their leaderboard position and their
    # `affinity_score` — which feeds `eb_multiplier` and therefore the
    # allocator. Every agent-scoped aggregate filters `agent_id == x` or
    # `.in_(ids)`, so a NULL drops out of all of them by construction, exactly
    # as the absent row used to. `empirical_bayes` groups WITHOUT such a filter
    # and had to be guarded explicitly — see the note there.
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True)

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    mode: Mapped[PaymentMode] = mapped_column(SAEnum(PaymentMode, name="payment_mode_enum"), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum"), default=PaymentStatus.PENDING_VERIFICATION, nullable=False
    )

    # Unique receipt — prevents duplicate submissions
    receipt_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    upi_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cheque_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bank_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Proof document
    receipt_photo_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Timestamps
    payment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # SMS sent to customer after payment
    receipt_sms_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    case: Mapped["Case"] = relationship("Case", back_populates="payments")  # type: ignore[name-defined]  # noqa: F821
    visit: Mapped["Visit | None"] = relationship("Visit", back_populates="payment")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_payment_case_status", "case_id", "status"),
        Index("ix_payment_date", "payment_date"),
        Index("ix_payment_agent_date", "agent_id", "payment_date"),
        Index("ix_payment_agent_status", "agent_id", "status"),
    )
