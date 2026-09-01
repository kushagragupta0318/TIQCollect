import enum
from sqlalchemy import String, Integer, Float, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AllocationObjective(str, enum.Enum):
    BALANCED = "BALANCED"
    MAX_RECOVERY = "MAX_RECOVERY"
    MIN_DISTANCE = "MIN_DISTANCE"


class AllocationSetting(Base, UUIDPrimaryKey, TimestampMixin):
    """Active configuration for an agency/manager allocation policy."""
    __tablename__ = "allocation_settings"

    manager_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False, index=True)
    objective: Mapped[str] = mapped_column(String(30), default=AllocationObjective.BALANCED.value, nullable=False)
    max_territory_radius_km: Mapped[float] = mapped_column(Float, default=16.0, nullable=False)
    max_daily_stops_per_agent: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    custom_weights: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
