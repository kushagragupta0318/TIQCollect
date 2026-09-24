# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B06) — collections.payments (docs/DATA-MODEL-V2.md §4.3).
#   - bank_id / agency_id / loan_id, denormalised from the case on purpose:
#     the labeller and the collections views group by loan, and every tenant
#     query leads with agency_id. Filled from the case by the one listener.
#   - (visit_id, case_id) → visits(id, case_id): a payment's visit must belong
#     to the same case.
#   - amount is NUMERIC(14,2) with CHECK > 0; receipt_number unique per bank.
#   - ck_payments_bank_direct_unattributed: a BANK_DIRECT payment has no
#     agent, by construction (see the 2026-09-09 note on agent_id).
#   - ck_payments_no_online: ONLINE stays in the enum type (removing a
#     Postgres enum member needs a migration) but nothing may write it.
#   - bank_action_id links a BANK_DIRECT row to the bank action that caused it;
#     settlement_offer_id links a payment to the offer it fulfils.
#   - The two agent indexes declared here since v1 but missing from the
#     database (design §1.3) are created by the v2 baseline.
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import datetime
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum as SAEnum, ForeignKeyConstraint, Index, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import PUBLIC, Base, Money, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


class PaymentMode(str, enum.Enum):
    CASH = "CASH"
    UPI = "UPI"
    NEFT = "NEFT"
    RTGS = "RTGS"
    CHEQUE = "CHEQUE"
    DD = "DD"
    # 2026-09-17 — NOT SELECTABLE IN THE PRODUCT. A prototype catch-all that
    # no agent-facing screen offers (RecordVisitPage: Cash / UPI / Cheque /
    # NEFT-IMPS / RTGS) and no service writes. Every row that ever carried it
    # came from demo tooling; those 124 were re-split across UPI/NEFT/RTGS on
    # 2026-09-17 and the writers corrected. Kept only because removing a
    # member of a Postgres enum type needs a migration; nothing may write it —
    # tests/test_payment_modes.py pins that for the demo scripts, and since
    # 2026-09-24 ck_payments_no_online makes the database refuse it too.
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


PAYMENT_MODE_SQL = SAEnum(PaymentMode, name="payment_mode_enum", schema=PUBLIC, metadata=Base.metadata)
PAYMENT_STATUS_SQL = SAEnum(PaymentStatus, name="payment_status_enum", schema=PUBLIC, metadata=Base.metadata)


class Payment(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "payments"
    __tenant_parents__ = (("case_id", "Case"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    visit_id: Mapped[str | None] = mapped_column(UUIDType)
    # NULLABLE since 2026-09-09, and the NULL means something specific: nobody
    # collected this. A direct bank payment is real money against the case, but
    # it is not evidence about any agent, so attributing it to one would inflate
    # that agent's collections, their leaderboard position and their
    # `affinity_score` — which feeds `eb_multiplier` and therefore the
    # allocator. Every agent-scoped aggregate filters `agent_id == x` or
    # `.in_(ids)`, so a NULL drops out of all of them by construction, exactly
    # as the absent row used to. `empirical_bayes` groups WITHOUT such a filter
    # and had to be guarded explicitly — see the note there.
    agent_id: Mapped[str | None] = mapped_column(UUIDType)

    amount: Mapped[float] = mapped_column(Money, nullable=False)
    mode: Mapped[PaymentMode] = mapped_column(PAYMENT_MODE_SQL, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        PAYMENT_STATUS_SQL, default=PaymentStatus.PENDING_VERIFICATION, nullable=False
    )

    # Unique receipt (per bank) — prevents duplicate submissions
    receipt_number: Mapped[str] = mapped_column(String(50), nullable=False)
    upi_reference: Mapped[str | None] = mapped_column(String(100))
    cheque_number: Mapped[str | None] = mapped_column(String(20))
    bank_reference: Mapped[str | None] = mapped_column(String(100))

    # Proof document
    receipt_photo_key: Mapped[str | None] = mapped_column(String(500))

    # Timestamps
    payment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    # SMS sent to customer after payment
    receipt_sms_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    settlement_offer_id: Mapped[str | None] = uuid_fk("collections.settlement_offers.id", nullable=True)
    bank_action_id: Mapped[str | None] = uuid_fk("lending.bank_actions.id", nullable=True)

    case: Mapped["Case"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Case", back_populates="payments", primaryjoin="Payment.case_id == Case.id",
        foreign_keys="[Payment.case_id]")
    visit: Mapped["Visit | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Visit", back_populates="payment", primaryjoin="Payment.visit_id == Visit.id",
        foreign_keys="[Payment.visit_id]")

    __table_args__ = (
        UniqueConstraint("bank_id", "receipt_number"),
        ForeignKeyConstraint(["case_id", "agency_id"], ["collections.cases.id", "collections.cases.agency_id"]),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"]),
        ForeignKeyConstraint(["visit_id", "case_id"], ["collections.visits.id", "collections.visits.case_id"]),
        ForeignKeyConstraint(["agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"]),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("mode <> 'BANK_DIRECT' OR agent_id IS NULL", name="bank_direct_unattributed"),
        CheckConstraint("mode <> 'ONLINE'", name="no_online"),
        Index("ix_payment_case_status", "case_id", "status"),
        Index("ix_payment_agent_date", "agent_id", "payment_date"),
        Index("ix_payment_agent_status", "agent_id", "status"),
        Index(None, "agency_id", "status", "payment_date"),
        Index(None, "loan_id", "status", "payment_date"),
        Index(None, "bank_id", "payment_date"),
        {"schema": "collections"},
    )
