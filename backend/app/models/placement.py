# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B06) — New file: the collections tables with no v1 equivalent
#   (docs/DATA-MODEL-V2.md §4.3).
#
#   Placement is the core new concept of the standalone product: the BANK
#   places a delinquent loan with an AGENCY for a period; the agency's case is
#   its work item on that placement. At most one ACTIVE placement per loan.
#   The at-placement figures (exposure, DPD, the model's expected recovery)
#   are frozen here because they are what "Recovery vs Expected" — the
#   case-mix-adjusted agency ranking — divides by. expected_recovery_* are
#   model OUTPUTS and must never become model inputs (CLAUDE.md, scoring rules).
#
#   settlement_offers is OUR settlement workflow; loans.settlement_status stays
#   the BANK's flag. disputes gives disputes and complaints a lifecycle — until
#   now a dispute was only a visit outcome.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text as text  # noqa: F401
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Float, ForeignKeyConstraint, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonDoc, Money, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk
from app.models.loan import DPD_BUCKET_SQL, DPDBucket

SCHEMA = "collections"


def _in(column: str, values: tuple[str, ...], nullable: bool = False) -> str:
    body = f"{column} IN ({', '.join(repr(v) for v in values)})"
    return f"{column} IS NULL OR {body}" if nullable else body


PLACEMENT_SOURCES = ("MANUAL", "ENGINE", "RE_PLACEMENT", "TRANSFORM", "FEED")
PLACEMENT_STATUSES = ("ACTIVE", "RECALLED", "RETURNED", "EXPIRED", "RESOLVED", "TRANSFERRED")


class Placement(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "placements"
    __tenant_parents__ = (("contract_id", "AgencyContract"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    contract_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    placement_run_id: Mapped[str | None] = uuid_fk("planning.placement_runs.id", nullable=True)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ACTIVE")
    placed_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_end_on: Mapped[date | None] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)
    end_reason: Mapped[str | None] = mapped_column(String(30))
    placed_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    ended_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    dpd_at_placement: Mapped[int] = mapped_column(Integer, nullable=False)
    dpd_bucket_at_placement: Mapped[DPDBucket] = mapped_column(DPD_BUCKET_SQL, nullable=False)
    exposure_at_placement: Mapped[float] = mapped_column(Money, nullable=False)   # total_outstanding
    overdue_at_placement: Mapped[float] = mapped_column(Money, nullable=False)
    expected_recovery_prob: Mapped[float | None] = mapped_column(Float)          # a model OUTPUT
    expected_recovery_inr: Mapped[float | None] = mapped_column(Money)
    model_prediction_id: Mapped[str | None] = mapped_column(UUIDType)
    model_prediction_as_of: Mapped[date | None] = mapped_column(Date)
    sla_first_visit_due: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("id", "agency_id"),
        UniqueConstraint("id", "bank_id"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["contract_id", "agency_id"],
                             ["tenancy.agency_contracts.id", "tenancy.agency_contracts.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["model_prediction_id", "model_prediction_as_of"],
                             ["ml.model_predictions.id", "ml.model_predictions.as_of_date"],
                             ondelete="SET NULL"),
        CheckConstraint(_in("source", PLACEMENT_SOURCES), name="source"),
        CheckConstraint(_in("status", PLACEMENT_STATUSES), name="status"),
        CheckConstraint("(model_prediction_id IS NULL) = (model_prediction_as_of IS NULL)",
                        name="prediction_pair"),
        # At most one ACTIVE placement per loan.
        Index("uq_placements_active_loan", "loan_id", unique=True,
              postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'")),
        Index(None, "agency_id", "status", "placed_on"),
        Index(None, "bank_id", "status", "placed_on"),
        Index(None, "contract_id"),
        {"schema": SCHEMA},
    )


OFFER_STATUSES = (
    "DRAFT", "PENDING_BANK_APPROVAL", "APPROVED", "OFFERED", "ACCEPTED", "DECLINED_BY_BORROWER",
    "REJECTED_BY_BANK", "EXPIRED", "FULFILLED", "DEFAULTED", "WITHDRAWN",
)
LIVE_OFFER_STATUSES = ("PENDING_BANK_APPROVAL", "APPROVED", "OFFERED", "ACCEPTED")


class SettlementOffer(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "settlement_offers"
    __tenant_parents__ = (("case_id", "Case"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    placement_id: Mapped[str | None] = mapped_column(UUIDType)
    outstanding_at_offer: Mapped[float] = mapped_column(Money, nullable=False)
    offered_amount: Mapped[float] = mapped_column(Money, nullable=False)
    policy_floor_amount: Mapped[float | None] = mapped_column(Money)
    payment_plan: Mapped[list | None] = mapped_column(JsonDoc)          # [{due_date, amount}]
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    valid_until: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    acceptance_probability: Mapped[float | None] = mapped_column(Float)  # a model OUTPUT
    model_prediction_id: Mapped[str | None] = mapped_column(UUIDType)
    model_prediction_as_of: Mapped[date | None] = mapped_column(Date)
    proposed_by: Mapped[str] = uuid_fk("tenancy.users.id")
    approved_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    borrower_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(["case_id", "agency_id"], ["collections.cases.id", "collections.cases.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["placement_id", "agency_id"],
                             ["collections.placements.id", "collections.placements.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["model_prediction_id", "model_prediction_as_of"],
                             ["ml.model_predictions.id", "ml.model_predictions.as_of_date"],
                             ondelete="SET NULL"),
        CheckConstraint(_in("status", OFFER_STATUSES), name="status"),
        CheckConstraint("source IN ('AGENT', 'MANAGER', 'BANK', 'MODEL')", name="source"),
        CheckConstraint("offered_amount > 0 AND offered_amount <= outstanding_at_offer", name="amount"),
        CheckConstraint("approved_by IS NULL OR approved_by <> proposed_by", name="four_eyes"),
        Index("uq_settlement_offers_live_case", "case_id", unique=True,
              postgresql_where=text("status IN ('PENDING_BANK_APPROVAL', 'APPROVED', 'OFFERED', 'ACCEPTED')"),
              sqlite_where=text("status IN ('PENDING_BANK_APPROVAL', 'APPROVED', 'OFFERED', 'ACCEPTED')")),
        Index(None, "agency_id", "status"),
        Index(None, "bank_id", "status", "created_at"),
        {"schema": SCHEMA},
    )


DISPUTE_CATEGORIES = (
    "AMOUNT_DISPUTED", "ALREADY_PAID", "FRAUD_CLAIM", "NOT_MY_LOAN", "AGENT_CONDUCT", "HARASSMENT",
    "PRIVACY", "OTHER",
)
DISPUTE_STATUSES = (
    "OPEN", "UNDER_REVIEW", "RESOLVED_UPHELD", "RESOLVED_REJECTED", "WITHDRAWN", "ESCALATED_TO_BANK",
)


class Dispute(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "disputes"
    __tenant_parents__ = (("case_id", "Case"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)             # DISPUTE / COMPLAINT
    raised_via: Mapped[str] = mapped_column(String(10), nullable=False)
    visit_id: Mapped[str | None] = mapped_column(UUIDType)
    call_log_id: Mapped[str | None] = uuid_fk("collections.call_logs.id", nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    holds_collection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(["case_id", "agency_id"], ["collections.cases.id", "collections.cases.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["visit_id", "case_id"], ["collections.visits.id", "collections.visits.case_id"],
                             ondelete="RESTRICT"),
        CheckConstraint("kind IN ('DISPUTE', 'COMPLAINT')", name="kind"),
        CheckConstraint("raised_via IN ('VISIT', 'CALL', 'BANK', 'BORROWER', 'PORTAL')", name="raised_via"),
        CheckConstraint(_in("category", DISPUTE_CATEGORIES), name="category"),
        CheckConstraint(_in("status", DISPUTE_STATUSES), name="status"),
        Index(None, "agency_id", "status", "raised_at"),
        Index(None, "case_id"),
        Index(None, "bank_id", "kind", "raised_at"),
        {"schema": SCHEMA},
    )
