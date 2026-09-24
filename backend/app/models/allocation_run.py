# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B08) — planning.allocation_runs (docs/DATA-MODEL-V2.md §4.5):
#   runs are per agency, then per manager inside it (A04). strategy / status
#   are code-owned state with CHECKs (FAILED was added without a migration in
#   v1; in v2 adding a value is one CHECK edit). expected_recovery_total is an
#   aggregate, NUMERIC(18,2).
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date
from sqlalchemy import CheckConstraint, Date, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, JsonDoc, TimestampMixin, UUIDPrimaryKey, UUIDType


class AllocationStrategy(str, enum.Enum):
    SMART = "SMART"
    LEGACY = "LEGACY"


class AllocationRunStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"
    # 2026-09-03. The nightly task planned for every manager inside one
    # try/except, so a single manager raising aborted the run for all of them
    # AND left no trace in the database — the only record was a stack trace in
    # the worker's container logs. On 2026-09-02 the 20:00 task died 131ms in
    # on a stale-import AttributeError and nobody knew until the logs were read
    # a day later.
    #
    # A FAILED row is how a failed run becomes a fact in the product rather
    # than an absence. status is String(20), not a Postgres enum, so adding
    # this needs no migration.
    FAILED = "FAILED"


class AllocationRun(Base, UUIDPrimaryKey, TimestampMixin):
    """Execution run for nightly next-day case allocation and beat planning."""
    __tablename__ = "allocation_runs"
    __tenant_parents__ = (("manager_user_id", "User"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    manager_user_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    strategy: Mapped[str] = mapped_column(String(20), default=AllocationStrategy.SMART.value, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=AllocationRunStatus.PLANNED.value, nullable=False)

    total_cases_evaluated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_allocated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_deferred: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_agents_planned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expected_recovery_total: Mapped[float] = mapped_column(Numeric(18, 2, asdecimal=False), default=0.0, nullable=False)
    summary_metadata: Mapped[dict] = mapped_column(JsonDoc, default=dict, nullable=False)

    # Relationships
    manager: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys=[manager_user_id], primaryjoin="AllocationRun.manager_user_id == User.id")
    # No delete-orphan cascade (it went 2026-09-24): allocation_decisions is a
    # partitioned audit table, and deleting a run must never silently take the
    # record of why every case landed where it did.
    decisions: Mapped[list["AllocationDecision"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AllocationDecision", back_populates="run", primaryjoin="AllocationRun.id == AllocationDecision.run_id",
        foreign_keys="[AllocationDecision.run_id]")
    beats: Mapped[list["Beat"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Beat", back_populates="allocation_run", primaryjoin="AllocationRun.id == Beat.allocation_run_id",
        foreign_keys="[Beat.allocation_run_id]")

    __table_args__ = (
        UniqueConstraint("id", "agency_id"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"]),
        ForeignKeyConstraint(["manager_user_id", "agency_id"], ["tenancy.users.id", "tenancy.users.agency_id"]),
        CheckConstraint("strategy IN ('SMART', 'LEGACY')", name="strategy"),
        CheckConstraint("status IN ('PLANNED', 'APPLIED', 'ROLLED_BACK', 'FAILED')", name="status"),
        Index("ix_alloc_run_mgr_date", "manager_user_id", "plan_date"),
        Index("ix_alloc_run_date_status", "agency_id", "plan_date", "status"),
        {"schema": "planning"},
    )
