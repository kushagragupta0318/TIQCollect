# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B08) — New file: the bank's placement engine runs and their
#   per-loan decisions (docs/DATA-MODEL-V2.md §4.5). Placement is today's
#   allocator one level up — bank → agency instead of agency → agent — and it
#   keeps the allocator's audit discipline: every decision is persisted with
#   its reason, its hard-gate results and, for the ε-greedy slice, the same
#   exploration keys (so the slice can be found and reweighted later).
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index,
    Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonDoc, TimestampMixin, UUIDPrimaryKey, UUIDType, new_id, uuid_fk

SCHEMA = "planning"


class PlacementRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "placement_runs"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    strategy: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PLANNED")
    simulate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exploration_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    total_loans_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_placed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_kept: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_deferred: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_recalled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_recovery_total: Mapped[float | None] = mapped_column(Numeric(18, 2, asdecimal=False))
    parameters: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    summary: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    applied_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("strategy IN ('MIN_COST_FLOW', 'HUNGARIAN', 'GREEDY', 'MANUAL_BATCH')", name="strategy"),
        CheckConstraint("status IN ('PLANNED', 'APPLIED', 'ROLLED_BACK', 'FAILED', 'SIMULATED')", name="status"),
        Index(None, "bank_id", "plan_date", "status"),
        {"schema": SCHEMA},
    )


class PlacementDecision(Base):
    """Partition-ready: (id, plan_date) is the PK, plan_date denormalised from
    the run, so it can be partitioned by month without a key change (Q9)."""
    __tablename__ = "placement_decisions"
    __tenant_parents__ = (("run_id", "PlacementRun"),)

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=new_id)
    plan_date: Mapped[date] = mapped_column(Date, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), default=func.now())
    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    run_id: Mapped[str] = uuid_fk("planning.placement_runs.id")
    loan_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    chosen_agency_id: Mapped[str | None] = mapped_column(UUIDType)
    previous_agency_id: Mapped[str | None] = mapped_column(UUIDType)
    outcome: Mapped[str] = mapped_column(
        String(20), ForeignKey("planning.placement_outcomes.code", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    score_breakdown: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    gate_results: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    model_prediction_id: Mapped[str | None] = mapped_column(UUIDType)
    model_prediction_as_of: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("run_id", "loan_id", "plan_date"),
        ForeignKeyConstraint(["loan_id", "bank_id"], ["lending.loans.id", "lending.loans.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["chosen_agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["previous_agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["model_prediction_id", "model_prediction_as_of"],
                             ["ml.model_predictions.id", "ml.model_predictions.as_of_date"],
                             ondelete="SET NULL"),
        CheckConstraint("(model_prediction_id IS NULL) = (model_prediction_as_of IS NULL)", name="prediction_pair"),
        Index(None, "bank_id", "plan_date", "outcome"),
        Index(None, "loan_id", "plan_date"),
        Index(None, "chosen_agency_id", "plan_date"),
        {"schema": SCHEMA},
    )
