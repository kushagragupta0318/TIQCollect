# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B06) — collections.cases (docs/DATA-MODEL-V2.md §4.3).
#   - bank_id / agency_id: a case is an agency's work item and NEVER changes
#     agency (re-placing a loan ends the old placement and case and opens new
#     ones). Composite FKs (agent_id, agency_id) → agents make it impossible
#     at the database level to assign a case across agencies.
#   - placement_id: the bank placed the loan with this agency. Nullable in P1
#     only because ~30 test constructors predate placements; every production
#     creator (ingest, demo feed, generator, transform) sets it, and a test
#     pins that.
#   - money is NUMERIC(14,2), allocation_date / bank_ptp_date are DATEs.
#   - closure_reason: the enum CLAUDE.md asked for — RECALL used to be
#     detectable only as a free-text prefix of resolution_notes.
#   - collection_stage is an FK to a lookup.
#   - The per-agency unassigned pool has its own partial index: that is the
#     query the fix for cross-agency leaks 1 and 2 (plan §1) runs.
#   - DEFERRED to B23: moving the escalation columns to collections.escalations
#     (creating that table before its readers move would be two sources).
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date, datetime

from sqlalchemy import text as text  # noqa: F401
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, Float, ForeignKey, ForeignKeyConstraint,
    Index, Integer, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import PUBLIC, Base, Money, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


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


# Cases where the collection question is closed: no next visit, no further
# money expected, nothing left to rank or predict.
#
# ONE DEFINITION, HERE. It was written out twice — case_service (which withholds
# the repayment score) and visit_priority_service (which withholds the visit
# score) — each with a comment saying it matched the other. Two copies of one
# policy held in step by a comment is the drift this codebase already documents
# elsewhere; merged 2026-08-28.
#
# ESCALATED is deliberately absent. It is open, visitable, and the work a manager
# most wants surfaced — grouping it here would quietly bury the hardest cases.
RESOLVED_STATUSES = frozenset({
    CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF,
})


class CasePriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def priority_for(dpd: int | float | None) -> CasePriority:
    """The stored case priority a DPD implies — ONE definition. 2026-09-21.

    Three copies existed and disagreed: scripts/seed_data.py (HIGH above 90,
    MEDIUM otherwise — no CRITICAL, no LOW), workers/tasks/demo_daily_feed.py
    (this ladder), and scripts/ingest_daily.py (a score mixing DPD with the
    outstanding balance, so a large 40-DPD loan read CRITICAL). And nothing
    ever re-derived the value after creation, so on 2026-09-21 248 of ~1,270
    open cases carried a priority their current DPD contradicted (32 stored
    MEDIUM past 90 days; 44 stored CRITICAL back under 60).

    Same bands as the DPD buckets (`loan.dpd_bucket_for`), by design: this is
    the AGEING label of the case, and nothing more. Balance and recoverability
    are the visit-priority scorecard's job, never this column's — mixing them
    in was how the ingest's version drifted from the other two. Re-stamped
    nightly by services/case_priority_service.restamp; a test asserts nobody
    restates the ladder.
    """
    d = float(dpd or 0)
    if d > 90:
        return CasePriority.CRITICAL
    if d > 60:
        return CasePriority.HIGH
    if d > 30:
        return CasePriority.MEDIUM
    return CasePriority.LOW


class EscalationReason(str, enum.Enum):
    CUSTOMER_HOSTILE = "CUSTOMER_HOSTILE"
    CUSTOMER_ABSCONDED = "CUSTOMER_ABSCONDED"
    DISPUTED_AMOUNT = "DISPUTED_AMOUNT"
    LEGAL_NOTICE_REQUIRED = "LEGAL_NOTICE_REQUIRED"
    PROPERTY_DISPUTE = "PROPERTY_DISPUTE"
    OTHER = "OTHER"


class ClosureReason(str, enum.Enum):
    """Why a case stopped being worked (design §4.3). Stored as a CHECKed
    VARCHAR, not a native enum: a code-owned state list (§2.3)."""
    PAID = "PAID"
    PAID_DIRECT = "PAID_DIRECT"
    SETTLED = "SETTLED"
    WRITTEN_OFF = "WRITTEN_OFF"
    RECALLED = "RECALLED"
    DECEASED = "DECEASED"
    TRANSFERRED = "TRANSFERRED"
    RETURNED = "RETURNED"
    OTHER = "OTHER"


CASE_STATUS_SQL = SAEnum(CaseStatus, name="case_status_enum", schema=PUBLIC, metadata=Base.metadata)
CASE_PRIORITY_SQL = SAEnum(CasePriority, name="case_priority_enum", schema=PUBLIC, metadata=Base.metadata)
ESCALATION_REASON_SQL = SAEnum(EscalationReason, name="escalation_reason_enum", schema=PUBLIC,
                               metadata=Base.metadata)


class Case(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "cases"
    # Tenant columns filled from these parents when not given (models/tenancy_listener.py).
    __tenant_parents__ = (("placement_id", "Placement"), ("loan_id", "Loan"))

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    placement_id: Mapped[str | None] = mapped_column(UUIDType)

    case_number: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(UUIDType)
    assigned_by_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    status: Mapped[CaseStatus] = mapped_column(CASE_STATUS_SQL, default=CaseStatus.UNASSIGNED, nullable=False)
    priority: Mapped[CasePriority] = mapped_column(CASE_PRIORITY_SQL, default=CasePriority.MEDIUM, nullable=False)

    # Financial targets
    target_amount: Mapped[float] = mapped_column(Money, nullable=False)
    collected_amount: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)
    waiver_approved: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)

    # Allocation metadata
    allocation_date: Mapped[date | None] = mapped_column(Date)
    allocation_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_ml_allocated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_visits_allowed: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)

    # Resolution
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    closure_reason: Mapped[str | None] = mapped_column(String(20))
    closed_by_bank_action_id: Mapped[str | None] = uuid_fk("lending.bank_actions.id", nullable=True, use_alter=True)

    # Bank-provided collection metadata (sent with the portfolio)
    collection_stage: Mapped[str] = mapped_column(
        String(30), ForeignKey("collections.collection_stages.code", ondelete="RESTRICT"),
        default="FIELD", nullable=False,
    )
    bank_ptp_date: Mapped[date | None] = mapped_column(Date)
    bank_ptp_amount: Mapped[float | None] = mapped_column(Money)
    bank_ptp_status: Mapped[str | None] = mapped_column(String(20))  # ACTIVE / HONORED / BROKEN / EXPIRED
    bank_agent_remarks: Mapped[str | None] = mapped_column(Text)

    # Agent handover (Phase 1D)
    handover_notes: Mapped[str | None] = mapped_column(Text)

    # Escalation (moves to collections.escalations in B23)
    is_escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalation_reason: Mapped[EscalationReason | None] = mapped_column(ESCALATION_REASON_SQL)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_notes: Mapped[str | None] = mapped_column(Text)

    customer: Mapped["Customer"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Customer", back_populates="cases", primaryjoin="Case.customer_id == Customer.id",
        foreign_keys="[Case.customer_id]")
    loan: Mapped["Loan"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Loan", back_populates="cases", primaryjoin="Case.loan_id == Loan.id", foreign_keys="[Case.loan_id]")
    agent: Mapped["Agent | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Agent", back_populates="cases", primaryjoin="Case.agent_id == Agent.id", foreign_keys="[Case.agent_id]")
    visits: Mapped[list["Visit"]] = relationship("Visit", back_populates="case", lazy="noload", primaryjoin="Case.id == Visit.case_id", foreign_keys="[Visit.case_id]")  # type: ignore[name-defined]  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="case", lazy="noload", primaryjoin="Case.id == Payment.case_id", foreign_keys="[Payment.case_id]")  # type: ignore[name-defined]  # noqa: F821
    ptps: Mapped[list["PTP"]] = relationship("PTP", back_populates="case", lazy="noload", primaryjoin="Case.id == PTP.case_id", foreign_keys="[PTP.case_id]")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint("bank_id", "case_number"),
        UniqueConstraint("id", "agency_id"),
        UniqueConstraint("id", "bank_id"),
        ForeignKeyConstraint(["placement_id", "agency_id"],
                             ["collections.placements.id", "collections.placements.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["customer_id", "bank_id"], ["lending.customers.id", "lending.customers.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        CheckConstraint(
            "closure_reason IS NULL OR closure_reason IN ("
            + ", ".join(repr(c.value) for c in ClosureReason) + ")",
            name="closure_reason",
        ),
        Index(None, "agency_id", "status", "priority"),
        Index(None, "agency_id", "agent_id", "status"),
        Index(None, "agency_id", "allocation_date"),
        Index(None, "loan_id"),
        Index(None, "customer_id"),
        Index(None, "placement_id"),
        # The per-agency unassigned pool (leaks 1 and 2 of plan §1).
        Index("ix_cases_unassigned_pool", "agency_id", "status",
              postgresql_where=text("agent_id IS NULL"), sqlite_where=text("agent_id IS NULL")),
        {"schema": "collections"},
    )
