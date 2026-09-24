# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B08) — planning.allocation_decisions (docs/DATA-MODEL-V2.md §4.5).
#   Partitioned monthly by plan_date on Postgres (78,809 rows in the demo
#   already); plan_date is denormalised from the run and the ORM keeps `id`
#   as its identity. The model link becomes (model_prediction_id,
#   model_prediction_as_of) because model_predictions is partitioned by
#   as_of_date and an FK into it must carry the partition key. outcome gains
#   an FK to its lookup.
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date
from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, JsonDoc, TimestampMixin, UUIDPrimaryKey, UUIDType


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
    # plan_date and the tenant come from the run; the prediction's as-of date
    # from the prediction (models/tenancy_listener.py).
    __tenant_parents__ = (
        ("run_id", "AllocationRun"),
        ("model_prediction_id", "ModelPrediction", {"model_prediction_as_of": "as_of_date"}),
    )

    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    run_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    case_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    previous_agent_id: Mapped[str | None] = mapped_column(UUIDType)
    allocated_agent_id: Mapped[str | None] = mapped_column(UUIDType)

    # 32, not 20. DEFERRED_ROUTE_INFEASIBLE is 25 characters and would have
    # raised a StringDataRightTruncation the first time an unroutable outlier
    # appeared — it never has (only ALLOCATED, BLOCKED and DEFERRED are present
    # across 26,841 rows), so the fault sat unexercised rather than fixed.
    outcome: Mapped[str] = mapped_column(
        String(32), ForeignKey("planning.allocation_outcomes.code", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    visit_priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fit_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JsonDoc, default=dict, nullable=False)

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
    model_prediction_id: Mapped[str | None] = mapped_column(UUIDType)
    model_prediction_as_of: Mapped[date | None] = mapped_column(Date)

    # Relationships
    run: Mapped["AllocationRun"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AllocationRun", back_populates="decisions", primaryjoin="AllocationDecision.run_id == AllocationRun.id",
        foreign_keys=[run_id])
    case: Mapped["Case"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Case", foreign_keys=[case_id], primaryjoin="AllocationDecision.case_id == Case.id")
    previous_agent: Mapped["Agent | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Agent", foreign_keys=[previous_agent_id], primaryjoin="AllocationDecision.previous_agent_id == Agent.id")
    allocated_agent: Mapped["Agent | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Agent", foreign_keys=[allocated_agent_id], primaryjoin="AllocationDecision.allocated_agent_id == Agent.id")

    __table_args__ = (
        UniqueConstraint("run_id", "case_id", "plan_date"),
        ForeignKeyConstraint(["run_id", "agency_id"], ["planning.allocation_runs.id", "planning.allocation_runs.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["case_id", "agency_id"], ["collections.cases.id", "collections.cases.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["previous_agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["allocated_agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["model_prediction_id", "model_prediction_as_of"],
                             ["ml.model_predictions.id", "ml.model_predictions.as_of_date"],
                             ondelete="SET NULL"),
        CheckConstraint("(model_prediction_id IS NULL) = (model_prediction_as_of IS NULL)", name="prediction_pair"),
        Index("ix_alloc_decision_run_outcome", "run_id", "outcome"),
        Index("ix_alloc_decision_case_date", "case_id", "created_at"),
        Index(None, "agency_id", "plan_date", "outcome"),
        Index(None, "allocated_agent_id", "plan_date"),
        Index(None, "model_prediction_id"),
        {"schema": "planning"},
    )
