import enum
from datetime import date
from sqlalchemy import String, Integer, Float, Date, ForeignKey, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AllocationStrategy(str, enum.Enum):
    SMART = "SMART"
    LEGACY = "LEGACY"


class AllocationRunStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"


class AllocationRun(Base, UUIDPrimaryKey, TimestampMixin):
    """Execution run for nightly next-day case allocation and beat planning."""
    __tablename__ = "allocation_runs"

    manager_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(20), default=AllocationStrategy.SMART.value, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=AllocationRunStatus.PLANNED.value, nullable=False)

    total_cases_evaluated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_allocated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_deferred: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cases_blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_agents_planned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expected_recovery_total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    summary_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    manager: Mapped["User"] = relationship("User", foreign_keys=[manager_user_id])  # type: ignore[name-defined]  # noqa: F821
    decisions: Mapped[list["AllocationDecision"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AllocationDecision", back_populates="run", cascade="all, delete-orphan"
    )
    beats: Mapped[list["Beat"]] = relationship("Beat", back_populates="allocation_run")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_alloc_run_mgr_date", "manager_user_id", "plan_date"),
        Index("ix_alloc_run_date_status", "plan_date", "status"),
    )
