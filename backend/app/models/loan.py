import enum
from sqlalchemy import String, Float, Integer, Boolean, Enum as SAEnum, ForeignKey, Index, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class RecoveryPotential(str, enum.Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


class LoanType(str, enum.Enum):
    HOME = "HOME"
    AUTO = "AUTO"
    PERSONAL = "PERSONAL"
    BUSINESS = "BUSINESS"
    GOLD = "GOLD"
    CREDIT_CARD = "CREDIT_CARD"
    EDUCATION = "EDUCATION"
    MICROFINANCE = "MICROFINANCE"


class DPDBucket(str, enum.Enum):
    CURRENT = "CURRENT"       # 0 DPD
    BUCKET_1 = "BUCKET_1"    # 1–30 DPD
    BUCKET_2 = "BUCKET_2"    # 31–60 DPD
    BUCKET_3 = "BUCKET_3"    # 61–90 DPD
    NPA = "NPA"              # 90+ DPD (Non-Performing Asset)


class LoanStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    WRITTEN_OFF = "WRITTEN_OFF"
    SETTLED = "SETTLED"
    NPA = "NPA"


class Loan(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "loans"

    loan_account_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)

    loan_type: Mapped[LoanType] = mapped_column(SAEnum(LoanType, name="loan_type_enum"), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    branch_code: Mapped[str] = mapped_column(String(20), nullable=False)

    # Amounts
    sanctioned_amount: Mapped[float] = mapped_column(Float, nullable=False)
    disbursed_amount: Mapped[float] = mapped_column(Float, nullable=False)
    outstanding_principal: Mapped[float] = mapped_column(Float, nullable=False)
    total_outstanding: Mapped[float] = mapped_column(Float, nullable=False)  # principal + interest + charges
    overdue_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    emi_amount: Mapped[float] = mapped_column(Float, nullable=False)

    # Dates
    disbursement_date: Mapped[str] = mapped_column(String(10), nullable=False)
    maturity_date: Mapped[str] = mapped_column(String(10), nullable=False)
    last_payment_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    next_due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # DPD tracking
    dpd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dpd_bucket: Mapped[DPDBucket] = mapped_column(
        SAEnum(DPDBucket, name="dpd_bucket_enum"), default=DPDBucket.CURRENT, nullable=False
    )
    status: Mapped[LoanStatus] = mapped_column(
        SAEnum(LoanStatus, name="loan_status_enum"), default=LoanStatus.ACTIVE, nullable=False
    )

    # Interest breakdown (bank sends these separately)
    interest_rate: Mapped[float] = mapped_column(Float, nullable=False)
    outstanding_interest: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    penal_charges: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tenure_months: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Payment history
    last_payment_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # NPA / legal / settlement (bank-reported flags)
    npa_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    legal_status: Mapped[str] = mapped_column(
        String(30), default="NONE", nullable=False
    )  # NONE / NOTICE_SENT / SARFAESI / SUIT_FILED / DRT / ARBITRATION
    settlement_status: Mapped[str] = mapped_column(
        String(30), default="NONE", nullable=False
    )  # NONE / OFFERED / NEGOTIATING / ACCEPTED / REJECTED

    # Bank's own risk score for this loan (probability of default / NPA risk)
    bank_risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Priority score — computed by TIQCollect ML
    collection_priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # How much of this loan we expect to get back — HIGH / MEDIUM / LOW.
    #
    # 2026-08-24 — this comment used to read "Daily recovery tag pushed by
    # Command Centre". That was never true: nothing has ever pushed it, and the
    # bank's daily feed carries 45 columns of which this is not one
    # (scripts/sample_daily_feed.csv). The claim mattered because it was the only
    # thing in the codebase suggesting the label arrived from outside, and it
    # sent anyone asking "do we compute this or does the bank send it?" to the
    # wrong answer. Corrected rather than deleted so the change is visible.
    #
    # WE compute it, in ml/recovery_scorecard.py, banded on the 90-day expected
    # recovery rate. Written by exactly one site — RepaymentService.
    # _apply_recovery_label — and only when RECOVERY_WRITE_LABEL is on, which it
    # is not by default. Until then this column holds whatever it held before and
    # the computed label is read from the snapshot instead.
    #
    # It was filled by random.choices() until that date, by two separate writers
    # with different weights, while nothing read it.
    recovery_potential: Mapped[RecoveryPotential | None] = mapped_column(
        SAEnum(RecoveryPotential, name="recovery_potential_enum"), nullable=True
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="loans")  # type: ignore[name-defined]  # noqa: F821
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="loan", lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_loan_dpd_status", "dpd_bucket", "status"),
        Index("ix_loan_customer_status", "customer_id", "status"),
        Index("ix_loan_overdue_amount", "overdue_amount"),
    )
