# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B08) — planning.allocation_settings: tenant columns; a row with
#   manager_user_id NULL is the AGENCY default (one per agency); objective is
#   an FK to its lookup.
# ────────────────────────────────────────────────────────────────────────────
import enum
from sqlalchemy import text as text  # noqa: F401
from sqlalchemy import Float, ForeignKey, ForeignKeyConstraint, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, JsonDoc, TimestampMixin, UUIDPrimaryKey, UUIDType


class AllocationObjective(str, enum.Enum):
    BALANCED = "BALANCED"
    MAX_RECOVERY = "MAX_RECOVERY"
    MIN_DISTANCE = "MIN_DISTANCE"


class AllocationSetting(Base, UUIDPrimaryKey, TimestampMixin):
    """Active configuration for an agency/manager allocation policy."""
    __tablename__ = "allocation_settings"
    __tenant_parents__ = (("manager_user_id", "User"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    manager_user_id: Mapped[str | None] = mapped_column(UUIDType)
    objective: Mapped[str] = mapped_column(
        String(30), ForeignKey("planning.allocation_objectives.code", ondelete="RESTRICT"),
        default=AllocationObjective.BALANCED.value, nullable=False)
    max_territory_radius_km: Mapped[float] = mapped_column(Float, default=16.0, nullable=False)
    max_daily_stops_per_agent: Mapped[int] = mapped_column(SmallInteger, default=12, nullable=False)
    custom_weights: Mapped[dict] = mapped_column(JsonDoc, default=dict, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["manager_user_id", "agency_id"], ["tenancy.users.id", "tenancy.users.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        Index("uq_allocation_settings_manager", "manager_user_id", unique=True,
              postgresql_where=text("manager_user_id IS NOT NULL"), sqlite_where=text("manager_user_id IS NOT NULL")),
        Index("uq_allocation_settings_agency_default", "agency_id", unique=True,
              postgresql_where=text("manager_user_id IS NULL"), sqlite_where=text("manager_user_id IS NULL")),
        {"schema": "planning"},
    )
