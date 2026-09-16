# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — Added CallLog.available_from / available_until (line 66-67):
#   a structured, machine-readable version of the existing free-text
#   best_time_to_visit, so CaseService.reoptimize_beat() has a real value to
#   turn into an OR-Tools time-window constraint or urgent forced_next
#   override. Migration applied by hand against local Postgres this session —
#   no committed Alembic migration file yet (tracked gap). Full detail + why:
#   /changelog.md
# 2026-09-16 — Added BorrowerDisposition + CallLog.borrower_disposition (and the
#   same column on Visit). The production-readiness audit of recovery_risk's
#   15-feature GAM found two of its inputs, `latest_disposition` and
#   `disposition_recency_class`, had NO source column in the product: the
#   ledger world that developed them records a stance on every contact, the
#   schema recorded none. Migration c9a3d5e7f102. Nullable, no default.
# ───────────────────────────────────────────────────────────────────────────
import enum
from datetime import date, datetime
from sqlalchemy import (
    String, Integer, Boolean, Float, Date, DateTime,
    ForeignKey, Enum as SAEnum, Index, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class CallOutcome(str, enum.Enum):
    ANSWERED     = "ANSWERED"
    NO_ANSWER    = "NO_ANSWER"
    BUSY         = "BUSY"
    DECLINED     = "DECLINED"       # picked up then cut / call rejected
    SWITCHED_OFF = "SWITCHED_OFF"
    WRONG_NUMBER = "WRONG_NUMBER"


class BorrowerDisposition(str, enum.Enum):
    """What the borrower SAID about paying, recorded on every contact that
    reached them. 2026-09-16.

    ONE VOCABULARY, TWO CHANNELS. The same six values sit on `CallLog` (an
    answered call) and `Visit` (a met visit), because the model that reads
    them — `recovery_risk` 2.2.0, `latest_disposition` — pools the two and
    takes the newest. It is an ordinal stance, best to worst:

        WILL_PAY       a date or an amount was named
        MAY_PAY        wants to pay, will not commit
        NO_COMMITMENT  reached, nothing said either way
        HARDSHIP       cannot pay — a capacity statement, not a stance
        DISPUTE        contests the debt
        REFUSES        will not pay

    The vocabulary is the ledger simulator's (`simulator.DISP_*`), which is
    where the feature was developed; `tests/test_recovery_risk_gam_features.py`
    holds the two together. Nullable, and NULL means "not recorded" — never
    defaulted, because a default would be a reading nobody took. Until the
    field process records it, every borrower reads as never-read, and
    `app/ml/pipeline/missingness.py` watches that share.
    """
    WILL_PAY      = "WILL_PAY"
    MAY_PAY       = "MAY_PAY"
    NO_COMMITMENT = "NO_COMMITMENT"
    HARDSHIP      = "HARDSHIP"
    DISPUTE       = "DISPUTE"
    REFUSES       = "REFUSES"


class CallLog(Base, UUIDPrimaryKey, TimestampMixin):
    """Records every phone call attempt an agent makes to a customer for a case.

    Columns are designed so an AI agent can extract visit-scheduling intelligence
    directly from the call record without needing to parse visit history.
    """
    __tablename__ = "call_logs"

    # ── Core foreign keys ────────────────────────────────────────────────────
    case_id:     Mapped[str] = mapped_column(ForeignKey("cases.id",     ondelete="CASCADE"), nullable=False, index=True)
    agent_id:    Mapped[str] = mapped_column(ForeignKey("agents.id",    ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)

    # ── Call metadata ────────────────────────────────────────────────────────
    called_at:        Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)   # None if not answered
    outcome: Mapped[CallOutcome] = mapped_column(
        SAEnum(CallOutcome, name="call_outcome_enum"), nullable=False
    )
    phone_used: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "PRIMARY" | "ALTERNATE"

    # ── What the customer said (agent's free-text note) ──────────────────────
    customer_response_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Structured intelligence extracted from the call ──────────────────────

    # Can the agent visit today based on this call?
    visit_feasible_today: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Best time window customer mentioned for a visit (free text preserved for AI)
    # e.g. "before 10 AM", "after 6 PM", "Saturday morning", "weekdays 11AM-1PM"
    best_time_to_visit: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Structured version of best_time_to_visit, when the agent can pin an exact
    # window (vs. a vague phrase) — this is what route re-optimization actually
    # consumes as a hard/soft time-window constraint. Both null unless the
    # customer gave a concrete window for a visit today.
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # If customer said they are unavailable until a specific date — block visits until then
    blocked_until_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # If customer mentioned they are at a different location (work, relative's house, etc.)
    alternate_location_hint: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Did customer signal willingness / urgency to pay?
    payment_intent_signalled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # 2026-09-16 — the structured disposition, recorded only on an ANSWERED
    # call and only when the agent captured it. See BorrowerDisposition.
    borrower_disposition: Mapped[BorrowerDisposition | None] = mapped_column(
        SAEnum(BorrowerDisposition, name="borrower_disposition_enum"), nullable=True
    )

    # Date customer mentioned for payment (PTP over call — not a formal PTP record)
    verbal_payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Any other actionable intel the AI should factor in for case strategy
    # e.g. "customer's son now handles finances", "property listed for sale", "got new job"
    ai_intel_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ────────────────────────────────────────────────────────
    case:     Mapped["Case"]     = relationship("Case",     foreign_keys=[case_id],     lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    agent:    Mapped["Agent"]    = relationship("Agent",    foreign_keys=[agent_id],    lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    customer: Mapped["Customer"] = relationship("Customer", foreign_keys=[customer_id], lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_call_log_case_time",     "case_id",     "called_at"),
        Index("ix_call_log_agent_time",    "agent_id",    "called_at"),
        Index("ix_call_log_customer_time", "customer_id", "called_at"),
        Index("ix_call_log_outcome",       "outcome"),
    )
