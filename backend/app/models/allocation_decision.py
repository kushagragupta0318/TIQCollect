import enum
from sqlalchemy import String, Float, Text, ForeignKey, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AllocationOutcome(str, enum.Enum):
    ALLOCATED = "ALLOCATED"
    DEFERRED = "DEFERRED"
    DEFERRED_ROUTE_INFEASIBLE = "DEFERRED_ROUTE_INFEASIBLE"
    BLOCKED = "BLOCKED"
    # Held until a promised date. The borrower committed to pay on a future
    # day, so sending an agent before then is a wasted visit and a nuisance
    # call on someone who is already cooperating.
    DEFERRED_PTP = "DEFERRED_PTP"
    # This month's visit budget for the case is spent (Case.max_visits_allowed
    # counted within the calendar month, not over the case's lifetime).
    DEFERRED_VISIT_CAP = "DEFERRED_VISIT_CAP"


class AllocationDecision(Base, UUIDPrimaryKey, TimestampMixin):
    """Per-case audit record capturing why a case was allocated, deferred, or blocked."""
    __tablename__ = "allocation_decisions"

    run_id: Mapped[str] = mapped_column(ForeignKey("allocation_runs.id"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    previous_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    allocated_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)

    # 32, not 20. DEFERRED_ROUTE_INFEASIBLE is 25 characters and would have
    # raised a StringDataRightTruncation the first time an unroutable outlier
    # appeared — it never has (only ALLOCATED, BLOCKED and DEFERRED are present
    # across 26,841 rows), so the fault sat unexercised rather than fixed.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    visit_priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fit_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # ── the exact model score behind this decision ──────────────────────────
    # 2026-09-09. `model_predictions` carries no run linkage and a re-plan
    # writes a fresh row per case, so a decision could not be traced to the
    # score that produced it. Measured on the live book: nine runs for one plan
    # date, 7-9 predictions per case, and 201 of 214 allocated decisions
    # matching MORE THAN ONE prediction on (case_id, agent_id).
    #
    # The link lives HERE rather than as an allocation_run_id on the prediction
    # because a prediction is borrower-side and agent-independent: one score can
    # legitimately inform several runs, so "which run owns this row" has no
    # single answer. A decision has exactly one score behind it, which makes
    # this direction the single-valued one.
    #
    # AN EXPLORATION SWAP DOES NOT CHANGE IT. Exploration moves a case to a
    # different agent and re-prices the decision, but the model's probability is
    # a property of the BORROWER and does not depend on who visits. The same
    # prediction informed the decision either way; what the swap changes is
    # `allocated_agent_id`, `score_breakdown["exploration"]` and the prediction's
    # own `agent_id` stamp, all of which record the agent that actually got it.
    #
    # NULL means "not recorded" -- never "no model was involved". Rows written
    # before this column existed genuinely do not know, and are not backfilled.
    model_prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_predictions.id", ondelete="SET NULL"),
        nullable=True, index=True)

    # Relationships
    run: Mapped["AllocationRun"] = relationship("AllocationRun", back_populates="decisions")  # type: ignore[name-defined]  # noqa: F821
    case: Mapped["Case"] = relationship("Case", foreign_keys=[case_id])  # type: ignore[name-defined]  # noqa: F821
    previous_agent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[previous_agent_id])  # type: ignore[name-defined]  # noqa: F821
    allocated_agent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[allocated_agent_id])  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_alloc_decision_run_outcome", "run_id", "outcome"),
        Index("ix_alloc_decision_case_date", "case_id", "created_at"),
    )
