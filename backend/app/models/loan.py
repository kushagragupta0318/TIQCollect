# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B05) — lending.loans (docs/DATA-MODEL-V2.md §4.2).
#   - bank_id replaces the free-text bank_name ("ABC Bank" on 1,478/1,478
#     demo loans). `bank_name` survives as a read-only property over the
#     bank's display name, because borrower SMS text reads it.
#   - branch_code stays a code, now with a composite natural-key FK
#     (bank_id, branch_code) → branches. The design proposed a surrogate
#     branch_id plus a read-only branch_code property; the natural key gives
#     the same integrity and leaves every reader (the ML adapter reads
#     branch_code) untouched. Recorded as a departure in the design's §0.
#   - money is NUMERIC(14,2) (read as float); the four dates are DATEs.
#   - customer_id is NO ACTION (was CASCADE; this said RESTRICT — corrected
#     2026-09-24, see base.uuid_fk): deleting a borrower must never
#     silently delete their loans.
#   - dpd_as_of says how old the current dpd is; npa_since splits NPA-Sub
#     from Doubtful for the Monte Carlo state space.
#   - legal_status / settlement_status are FKs to lookup tables.
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date

from sqlalchemy import (
    Boolean, CheckConstraint, Date, Enum as SAEnum, Float, ForeignKey, ForeignKeyConstraint, Index,
    Integer, SmallInteger, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import PUBLIC, Base, Money, Rate, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


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


def dpd_bucket_for(dpd: int | float | None) -> DPDBucket:
    """The bucket a DPD falls in — the thresholds commented above, as code.

    2026-09-07 — added because SIX copies of this rule existed and two of them
    disagreed with the comments above:

        scripts/seed_data.py            no CURRENT branch  (0 DPD -> BUCKET_1)
        workers/tasks/demo_daily_feed.py  no CURRENT and no BUCKET_1 branch
                                          (5 DPD -> BUCKET_2)

    NEITHER WAS LIVE. seed_data draws DPD from a list whose minimum is 35, and
    demo_daily_feed from one whose minimum is 32, so the missing branches were
    unreachable and every value those two actually produce was already correct.
    Verified exhaustively over 0..400 in tests/test_dpd_bucket.py, which is why
    this consolidation is a refactor and not a behaviour change.

    It is worth doing anyway because the trap springs the moment somebody widens
    a DPD range — and it would spring quietly.
    EmpiricalBayesAgentAdjuster is KEYED on this bucket, so two spellings of the
    boundary put an agent's evidence in one cell and the lookup in another, and
    nothing would fail.
    """
    d = int(dpd or 0)
    if d <= 0:
        return DPDBucket.CURRENT
    if d <= 30:
        return DPDBucket.BUCKET_1
    if d <= 60:
        return DPDBucket.BUCKET_2
    if d <= 90:
        return DPDBucket.BUCKET_3
    return DPDBucket.NPA


class LoanStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    WRITTEN_OFF = "WRITTEN_OFF"
    SETTLED = "SETTLED"
    NPA = "NPA"


LOAN_TYPE_SQL = SAEnum(LoanType, name="loan_type_enum", schema=PUBLIC, metadata=Base.metadata)
DPD_BUCKET_SQL = SAEnum(DPDBucket, name="dpd_bucket_enum", schema=PUBLIC, metadata=Base.metadata)
LOAN_STATUS_SQL = SAEnum(LoanStatus, name="loan_status_enum", schema=PUBLIC, metadata=Base.metadata)
RECOVERY_POTENTIAL_SQL = SAEnum(RecoveryPotential, name="recovery_potential_enum", schema=PUBLIC,
                                metadata=Base.metadata)


class Loan(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "loans"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    loan_account_number: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_id: Mapped[str] = mapped_column(UUIDType, nullable=False)

    loan_type: Mapped[LoanType] = mapped_column(LOAN_TYPE_SQL, nullable=False)
    branch_code: Mapped[str] = mapped_column(String(20), nullable=False)

    # Amounts
    sanctioned_amount: Mapped[float] = mapped_column(Money, nullable=False)
    disbursed_amount: Mapped[float] = mapped_column(Money, nullable=False)
    outstanding_principal: Mapped[float] = mapped_column(Money, nullable=False)
    total_outstanding: Mapped[float] = mapped_column(Money, nullable=False)  # principal + interest + charges
    overdue_amount: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)
    emi_amount: Mapped[float] = mapped_column(Money, nullable=False)

    # Dates
    disbursement_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_payment_date: Mapped[date | None] = mapped_column(Date)
    next_due_date: Mapped[date | None] = mapped_column(Date)

    # DPD tracking — CURRENT state, still overwritten by the feed. History is
    # lending.loan_dpd_history; dpd_as_of says how old this reading is.
    dpd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dpd_as_of: Mapped[date | None] = mapped_column(Date)
    dpd_bucket: Mapped[DPDBucket] = mapped_column(DPD_BUCKET_SQL, default=DPDBucket.CURRENT, nullable=False)
    status: Mapped[LoanStatus] = mapped_column(LOAN_STATUS_SQL, default=LoanStatus.ACTIVE, nullable=False)

    # Interest breakdown (bank sends these separately)
    interest_rate: Mapped[float] = mapped_column(Rate, nullable=False)
    outstanding_interest: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)
    penal_charges: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)
    tenure_months: Mapped[int | None] = mapped_column(SmallInteger)

    # Payment history
    last_payment_amount: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)

    # NPA / legal / settlement (bank-reported flags)
    npa_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    npa_since: Mapped[date | None] = mapped_column(Date)
    legal_status: Mapped[str] = mapped_column(
        String(30), ForeignKey("lending.legal_statuses.code"), default="NONE", nullable=False
    )
    # The BANK's settlement flag. Our settlement workflow is its own table.
    settlement_status: Mapped[str] = mapped_column(
        String(30), ForeignKey("lending.settlement_statuses.code"), default="NONE",
        nullable=False,
    )

    # Bank's own risk score for this loan (probability of default / NPA risk)
    bank_risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Priority score — computed by TIQCollect ML
    collection_priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # How much of this loan we expect to get back — HIGH / MEDIUM / LOW.
    #
    # 2026-08-24 — this comment used to read "Daily recovery tag pushed by
    # Command Centre". That was never true: nothing has ever pushed it, and the
    # bank's daily feed carries 46 columns of which this is not one
    # (scripts/sample_daily_feed.csv). The claim mattered because it was the only
    # thing in the codebase suggesting the label arrived from outside, and it
    # sent anyone asking "do we compute this or does the bank send it?" to the
    # wrong answer. Corrected rather than deleted so the change is visible.
    # (2026-09-24: this comment said "45 columns"; the sample feed has 46.)
    #
    # WE compute it, in ml/recovery_scorecard.py, banded on the 90-day expected
    # recovery rate. Written by exactly one site — RepaymentService.
    # _apply_recovery_label — and only when RECOVERY_WRITE_LABEL is on, which it
    # is not by default. Until then this column holds whatever it held before and
    # the computed label is read from the snapshot instead.
    #
    # It was filled by random.choices() until that date, by two separate writers
    # with different weights, while nothing read it.
    recovery_potential: Mapped[RecoveryPotential | None] = mapped_column(RECOVERY_POTENTIAL_SQL)

    # Joined on id ONLY (2026-09-24, coordinator audit): inferred from the
    # composite (customer_id, bank_id) FK the join also compared bank_id, so a
    # bank mismatch made loan.customer silently None and the ML adapter
    # dropped cibil/age/city — the 2026-09-08 "silent failure" pattern. The
    # tenant listener refuses a mismatch at flush instead.
    customer: Mapped["Customer"] = relationship("Customer", back_populates="loans", primaryjoin="Loan.customer_id == Customer.id", foreign_keys="[Loan.customer_id]")  # type: ignore[name-defined]  # noqa: F821
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="loan", lazy="noload", primaryjoin="Loan.id == Case.loan_id", foreign_keys="[Case.loan_id]")  # type: ignore[name-defined]  # noqa: F821
    bank: Mapped["Bank"] = relationship("Bank", lazy="joined", foreign_keys="[Loan.bank_id]", primaryjoin="Loan.bank_id == Bank.id", viewonly=True)  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint("bank_id", "loan_account_number"),
        UniqueConstraint("id", "bank_id"),
        ForeignKeyConstraint(["customer_id", "bank_id"], ["lending.customers.id", "lending.customers.bank_id"]),
        ForeignKeyConstraint(["bank_id", "branch_code"], ["tenancy.branches.bank_id", "tenancy.branches.branch_code"]),
        CheckConstraint("dpd >= 0", name="dpd_non_negative"),
        Index(None, "bank_id", "status", "dpd_bucket"),
        Index(None, "customer_id", "status"),
        Index(None, "bank_id", "overdue_amount"),
        Index(None, "bank_id", "branch_code"),
        {"schema": "lending"},
    )

    @property
    def bank_name(self) -> str | None:
        """The lending bank's display name — what v1 stored as a literal."""
        bank = self.bank
        return bank.display_name if bank is not None else None
