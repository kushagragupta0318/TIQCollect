# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-19 — New file. Findings themselves are NOT stored: they are recomputed
#   from Visit rows on every scan, so a threshold change takes effect everywhere
#   at once and a stored finding can never disagree with the rule that produced
#   it. What is stored is the human verdict on one, which cannot be recomputed.
#
#   That verdict is also the point. Every confirm or dismiss here is a labelled
#   example — the training set a supervised detector would need, produced as a
#   by-product of a manager doing their job rather than by an annotation
#   exercise nobody will fund.
# ───────────────────────────────────────────────────────────────────────────
import enum
from datetime import datetime

from sqlalchemy import (
    DateTime, Enum as SAEnum, ForeignKey, Index, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey


class ReviewVerdict(str, enum.Enum):
    CONFIRMED = "CONFIRMED"   # a real problem — pursue it
    DISMISSED = "DISMISSED"   # explained, or not worth acting on


class FraudReview(Base, UUIDPrimaryKey):
    """One manager's verdict on one finding."""
    __tablename__ = "fraud_reviews"

    # A finding is identified by the visit it concerns and the check that
    # produced it. That pair is stable across rescans, which is what lets a
    # dismissal stay dismissed without storing the finding itself.
    visit_id: Mapped[str] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # Denormalised so the per-agent rollup does not have to join back through
    # visits for what is a read-heavy summary.
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    verdict: Mapped[ReviewVerdict] = mapped_column(
        SAEnum(ReviewVerdict, name="review_verdict_enum"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    visit: Mapped["Visit"] = relationship("Visit", lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        # One standing verdict per finding. A manager changing their mind
        # updates the row rather than adding a second, contradictory one.
        UniqueConstraint("visit_id", "finding_type", name="uq_fraud_review_visit_type"),
        Index("ix_fraud_review_agent_verdict", "agent_id", "verdict"),
    )
