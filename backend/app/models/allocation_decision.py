import enum
from sqlalchemy import String, Float, Text, ForeignKey, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AllocationOutcome(str, enum.Enum):
    ALLOCATED = "ALLOCATED"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"


class AllocationDecision(Base, UUIDPrimaryKey, TimestampMixin):
    """Per-case audit record capturing why a case was allocated, deferred, or blocked."""
    __tablename__ = "allocation_decisions"

    run_id: Mapped[str] = mapped_column(ForeignKey("allocation_runs.id"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    previous_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    allocated_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)

    outcome: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # ALLOCATED / DEFERRED / BLOCKED
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    visit_priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fit_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    run: Mapped["AllocationRun"] = relationship("AllocationRun", back_populates="decisions")  # type: ignore[name-defined]  # noqa: F821
    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id])  # type: ignore[name-defined]  # noqa: F821
    previous_agent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[previous_agent_id])  # type: ignore[name-defined]  # noqa: F821
    allocated_agent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[allocated_agent_id])  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_alloc_decision_run_outcome", "run_id", "outcome"),
        Index("ix_alloc_decision_case_date", "case_id", "created_at"),
    )
