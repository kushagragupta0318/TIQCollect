"""A two-stage request to reverse a mistaken collection (plan §payments, #2).

A reversal is its own object, not a flag on the payment: the two-stage lifecycle
has states a payment row cannot hold (awaiting the agency's approval, then the
bank's; a REJECTED one that never touched the ledger), and the request must
survive its own rejection for the audit trail.

Two-stage agency→bank (owner, 2026-10-01): the agency raises and approves its own
side (payment.reversal.request → AGENCY_MANAGER), then it routes to the BANK, whose
sign-off (payment.reversal.approve.bank → a bank role) is fiduciary and final — the
ledger unwinds only then. The agency cannot both initiate and bless a reversal. The
bank stage is cross-tenant and goes through l8's scoped RequestContext. A DB
CheckConstraint holds the separation: the bank approver is never an agency actor.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, Enum as SAEnum, ForeignKeyConstraint, Index, Text, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PUBLIC, Base, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


class ReversalStatus(str, enum.Enum):
    # Two-stage agency→bank (owner, 2026-10-01). The agency raises and approves its
    # own side; the BANK gives fiduciary final sign-off, and only then does the
    # ledger unwind. The agency cannot both initiate and bless a reversal.
    PENDING_AGENCY = "PENDING_AGENCY"   # requested, awaiting the agency's own approval
    PENDING_BANK = "PENDING_BANK"       # agency-approved, awaiting the bank's final sign-off
    APPROVED = "APPROVED"               # bank signed off; the ledger was unwound in that transaction
    REJECTED = "REJECTED"               # declined at either stage; nothing moved


REVERSAL_STATUS_SQL = SAEnum(ReversalStatus, name="reversal_status_enum", schema=PUBLIC, metadata=Base.metadata)
# A reversal is live while it could still reach the bank, be signed off, or already has.
OPEN_REVERSAL_STATUSES = frozenset(
    {ReversalStatus.PENDING_AGENCY, ReversalStatus.PENDING_BANK, ReversalStatus.APPROVED})


class PaymentReversalRequest(Base, UUIDPrimaryKey, TimestampMixin):
    """One request to reverse one payment. APPROVED is terminal and means the
    unwind ran; REJECTED is terminal and means nothing moved."""
    __tablename__ = "payment_reversal_requests"
    __tenant_parents__ = (("payment_id", "Payment"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    payment_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str] = mapped_column(UUIDType, nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)       # why: a mis-entry needs stating
    status: Mapped[ReversalStatus] = mapped_column(
        REVERSAL_STATUS_SQL, default=ReversalStatus.PENDING_AGENCY,
        server_default=text("'PENDING_AGENCY'"), nullable=False)

    # Agency stage: the manager raises it and the agency approves its own side.
    agency_requested_by_id: Mapped[str] = uuid_fk("tenancy.users.id")
    agency_approved_by_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    agency_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bank stage: fiduciary final sign-off. The unwind commits only when this is set.
    bank_approved_by_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    bank_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)   # a rejection's reason

    __table_args__ = (
        # Single-column FK: payments has no UNIQUE(id, bank_id), so a composite FK
        # can't reference it. payment_id → the PK is enough; bank_id is denormalised
        # and the service sets it from the payment, consistent with isolation being
        # service-enforced today.
        ForeignKeyConstraint(["payment_id"], ["collections.payments.id"], ondelete="CASCADE"),
        # Each stage, once passed, names who passed it and when. The agency approval
        # exists by PENDING_BANK; the bank approval by APPROVED.
        CheckConstraint(
            "(status IN ('PENDING_AGENCY', 'REJECTED')) OR "
            "(agency_approved_by_id IS NOT NULL AND agency_approved_at IS NOT NULL)",
            name="agency_stage_named_once_passed"),
        CheckConstraint(
            "(status <> 'APPROVED') OR (bank_approved_by_id IS NOT NULL AND bank_approved_at IS NOT NULL)",
            name="bank_stage_named_when_approved"),
        # The fiduciary separation: the bank sign-off is never one of the agency
        # actors. (Cross-tenant makes this true anyway; the DB states it.)
        CheckConstraint(
            "bank_approved_by_id IS NULL OR "
            "(bank_approved_by_id <> agency_requested_by_id AND bank_approved_by_id <> agency_approved_by_id)",
            name="bank_approver_is_not_the_agency"),
        # At most one live (not-REJECTED) reversal per payment: no double reversal,
        # no two requests racing to the bank.
        Index("uq_one_open_reversal_per_payment", "payment_id", unique=True,
              postgresql_where=text("status <> 'REJECTED'")),
        Index(None, "agency_id", "status"),
        {"schema": "collections"},
    )
