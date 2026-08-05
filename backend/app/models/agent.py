import enum
from sqlalchemy import String, Float, Integer, Boolean, ForeignKey, Enum as SAEnum, Index, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AgentTier(str, enum.Enum):
    TIER_1 = "TIER_1"  # Top performer
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"  # New / low performer


class AgentStatus(str, enum.Enum):
    ON_DUTY = "ON_DUTY"
    OFF_DUTY = "OFF_DUTY"
    ON_LEAVE = "ON_LEAVE"
    SUSPENDED = "SUSPENDED"


class AgentSpecialization(str, enum.Enum):
    SECURED = "SECURED"        # Home/auto loans
    UNSECURED = "UNSECURED"    # Personal/credit card
    BOTH = "BOTH"


class Agent(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "agents"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    employee_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    id_card_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    agency_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Geographic base
    base_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    base_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    territory: Mapped[str] = mapped_column(String(100), nullable=False)
    languages_spoken: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # Capabilities
    specialization: Mapped[AgentSpecialization] = mapped_column(
        SAEnum(AgentSpecialization, name="agent_spec_enum"), default=AgentSpecialization.BOTH, nullable=False
    )
    max_cases_per_day: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(50), default="TWO_WHEELER", nullable=False)

    # Status & ranking
    status: Mapped[AgentStatus] = mapped_column(
        SAEnum(AgentStatus, name="agent_status_enum"), default=AgentStatus.OFF_DUTY, nullable=False
    )
    tier: Mapped[AgentTier] = mapped_column(
        SAEnum(AgentTier, name="agent_tier_enum"), default=AgentTier.TIER_3, nullable=False
    )
    ranking_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Live location (updated via heartbeat every 30s when on duty)
    last_known_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_known_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_location_update: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Performance counters (current month)
    current_month_visits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_month_collections: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_month_ptps_set: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_month_ptps_honored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifetime_collection_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # SOS
    sos_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sos_triggered_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    fcm_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Manager assignment — each agent belongs to exactly one manager (User with AGENCY_MANAGER role)
    manager_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    user: Mapped["User"] = relationship("User", foreign_keys="[Agent.user_id]", back_populates="agent_profile")  # type: ignore[name-defined]  # noqa: F821
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    visits: Mapped[list["Visit"]] = relationship("Visit", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    beats: Mapped[list["Beat"]] = relationship("Beat", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    performance_records: Mapped[list["AgentPerformance"]] = relationship("AgentPerformance", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_agents_status_agency", "status", "agency_id"),
        Index("ix_agents_tier_score", "tier", "ranking_score"),
    )


class AgentPerformance(Base, UUIDPrimaryKey, TimestampMixin):
    """Monthly performance snapshots for trend analytics."""
    __tablename__ = "agent_performance"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # "2025-01"
    total_visits: Mapped[int] = mapped_column(Integer, default=0)
    customer_met: Mapped[int] = mapped_column(Integer, default=0)
    total_collected: Mapped[float] = mapped_column(Float, default=0.0)
    ptps_set: Mapped[int] = mapped_column(Integer, default=0)
    ptps_honored: Mapped[int] = mapped_column(Integer, default=0)
    collection_rate: Mapped[float] = mapped_column(Float, default=0.0)
    ranking_score: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[AgentTier] = mapped_column(SAEnum(AgentTier, name="agent_tier_enum"), default=AgentTier.TIER_3)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="performance_records")

    __table_args__ = (
        Index("ix_perf_agent_month", "agent_id", "month", unique=True),
    )
