# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B07) — workforce.agents / agent_performance / agent_devices
#   (docs/DATA-MODEL-V2.md §4.4).
#   - agency_id is a real UUID FK (it was a free String(50) equal to
#     'AGENCY-TIQ-001' on 18 of 18 rows, read only by /verify-agent). An agent
#     never changes agency; a move is a new agent row.
#   - (user_id, agency_id) and (manager_user_id, agency_id) → users(id,
#     agency_id): an agent's login and manager must belong to the same agency.
#     user_id is RESTRICT (was CASCADE) and manager_user_id RESTRICT (was
#     SET NULL).
#   - employee_code unique per agency; id_card_number unique per bank.
#   - last_location_update / sos_triggered_at are instants (were String(50)).
#   - joined_on / exited_on (attrition), suspended_at / suspended_reason —
#     SUSPENDED was declared and never written until now.
#   - agent_performance.month is a DATE (first of month), was 'YYYY-MM'.
#   - agent_devices: device binding, finally written (A09). It replaces
#     users.registered_device_fingerprint. DEFERRED: fcm_token stays on agents
#     until its readers move (B23), so there is one push token, not two.
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date, datetime

from sqlalchemy import text as text  # noqa: F401
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, Float, ForeignKeyConstraint, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import PUBLIC, Base, JsonDoc, Money, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


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


AGENT_TIER_SQL = SAEnum(AgentTier, name="agent_tier_enum", schema=PUBLIC, metadata=Base.metadata)
AGENT_STATUS_SQL = SAEnum(AgentStatus, name="agent_status_enum", schema=PUBLIC, metadata=Base.metadata)
AGENT_SPEC_SQL = SAEnum(AgentSpecialization, name="agent_spec_enum", schema=PUBLIC, metadata=Base.metadata)


class Agent(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "agents"
    __tenant_parents__ = (("agency_id", "Agency"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    user_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    employee_code: Mapped[str] = mapped_column(String(20), nullable=False)
    id_card_number: Mapped[str] = mapped_column(String(50), nullable=False)

    # Gender of the agent. Added 2026-08-19 for allocation eligibility:
    # Customer.requires_female_agent has existed since the schema was written and
    # could never be honoured, because nothing anywhere recorded an agent's
    # gender. Nullable on purpose — it is unknown for existing rows, and the
    # eligibility check treats unknown as "cannot satisfy a female-only
    # requirement" rather than assuming. Held here rather than on User because
    # it is a field-workforce matching attribute; it has no meaning for a
    # manager or admin account.
    gender: Mapped[str | None] = mapped_column(String(10))

    # Geographic base
    base_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    base_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    territory: Mapped[str] = mapped_column(String(100), nullable=False)
    territory_region_id: Mapped[str | None] = mapped_column(UUIDType)
    languages_spoken: Mapped[list] = mapped_column(JsonDoc, default=list, nullable=False)

    # Capabilities
    specialization: Mapped[AgentSpecialization] = mapped_column(
        AGENT_SPEC_SQL, default=AgentSpecialization.BOTH, nullable=False)
    max_cases_per_day: Mapped[int] = mapped_column(SmallInteger, default=15, nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(50), default="TWO_WHEELER", nullable=False)

    # Status & ranking
    status: Mapped[AgentStatus] = mapped_column(AGENT_STATUS_SQL, default=AgentStatus.OFF_DUTY, nullable=False)
    tier: Mapped[AgentTier] = mapped_column(AGENT_TIER_SQL, default=AgentTier.TIER_3, nullable=False)
    ranking_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Live location (updated via heartbeat every 30s when on duty)
    last_known_latitude: Mapped[float | None] = mapped_column(Float)
    last_known_longitude: Mapped[float | None] = mapped_column(Float)
    last_location_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Performance counters (current month)
    current_month_visits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_month_collections: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)
    current_month_ptps_set: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_month_ptps_honored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifetime_collection_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # SOS
    sos_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sos_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fcm_token: Mapped[str | None] = mapped_column(Text)

    # Lifecycle
    joined_on: Mapped[date | None] = mapped_column(Date)
    exited_on: Mapped[date | None] = mapped_column(Date)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    # Invented demo data: the generator fills these for the realistic roster.
    dra_certificate_no: Mapped[str | None] = mapped_column(String(40))
    dra_certificate_expires_on: Mapped[date | None] = mapped_column(Date)

    # Manager assignment — each agent belongs to exactly one manager in the
    # same agency (AGENCY_MANAGER, or the agency's AGENCY_ADMIN).
    manager_user_id: Mapped[str | None] = mapped_column(UUIDType)

    user: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", foreign_keys="[Agent.user_id]", primaryjoin="Agent.user_id == User.id",
        back_populates="agent_profile")
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    visits: Mapped[list["Visit"]] = relationship("Visit", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    beats: Mapped[list["Beat"]] = relationship("Beat", back_populates="agent", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    performance_records: Mapped[list["AgentPerformance"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AgentPerformance", back_populates="agent", lazy="noload")

    __table_args__ = (
        UniqueConstraint("user_id"),
        UniqueConstraint("agency_id", "employee_code"),
        UniqueConstraint("bank_id", "id_card_number"),
        UniqueConstraint("id", "agency_id"),
        UniqueConstraint("id", "bank_id"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["user_id", "agency_id"], ["tenancy.users.id", "tenancy.users.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["manager_user_id", "agency_id"], ["tenancy.users.id", "tenancy.users.agency_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["territory_region_id", "bank_id"], ["tenancy.regions.id", "tenancy.regions.bank_id"],
                             ondelete="RESTRICT"),
        Index(None, "agency_id", "status"),
        Index(None, "agency_id", "manager_user_id"),
        Index(None, "agency_id", "tier", "ranking_score"),
        Index("ix_agents_gender", "agency_id", "gender",
              postgresql_where=text("gender IS NOT NULL"), sqlite_where=text("gender IS NOT NULL")),
        {"schema": "workforce"},
    )


class AgentPerformance(Base, UUIDPrimaryKey, TimestampMixin):
    """Monthly performance snapshots for trend analytics."""
    __tablename__ = "agent_performance"
    __tenant_parents__ = (("agent_id", "Agent"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agent_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first of the month
    total_visits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    customer_met: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_collected: Mapped[float] = mapped_column(Money, default=0.0, nullable=False)
    ptps_set: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ptps_honored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    collection_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ranking_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tier: Mapped[AgentTier] = mapped_column(AGENT_TIER_SQL, default=AgentTier.TIER_3, nullable=False)

    agent: Mapped["Agent"] = relationship(
        "Agent", back_populates="performance_records", primaryjoin="AgentPerformance.agent_id == Agent.id",
        foreign_keys="[AgentPerformance.agent_id]")

    __table_args__ = (
        ForeignKeyConstraint(["agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"],
                             ondelete="RESTRICT"),
        Index("ix_perf_agent_month", "agent_id", "month", unique=True),
        Index(None, "agency_id", "month"),
        {"schema": "workforce"},
    )


DEVICE_PLATFORMS = ("ANDROID", "IOS", "WEB", "SIMULATOR")


class AgentDevice(Base, UUIDPrimaryKey, TimestampMixin):
    """Device binding (plan §3.2, A09): an agent is bound to one device at a time."""
    __tablename__ = "agent_devices"
    __tenant_parents__ = (("agent_id", "Agent"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agent_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    device_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(100))
    platform: Mapped[str | None] = mapped_column(String(12))
    app_version: Mapped[str | None] = mapped_column(String(20))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    is_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unbound_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    unbind_reason: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("agent_id", "device_fingerprint"),
        ForeignKeyConstraint(["agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"],
                             ondelete="RESTRICT"),
        CheckConstraint("platform IS NULL OR platform IN ('ANDROID', 'IOS', 'WEB', 'SIMULATOR')", name="platform"),
        Index("uq_agent_devices_bound", "agent_id", unique=True,
              postgresql_where=text("is_bound"), sqlite_where=text("is_bound = 1")),
        Index(None, "agency_id", "last_seen_at"),
        {"schema": "workforce"},
    )
