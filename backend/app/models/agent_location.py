# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-18 — New file. Agent.last_known_latitude/longitude documented itself
#   as "updated via heartbeat every 30s when on duty", but no heartbeat endpoint
#   ever existed — those columns were only written at four discrete events
#   (check-in, visit, beat re-optimisation, SOS), so between visits an agent's
#   stored position could be hours stale. SOSButton.tsx papered over this by
#   falling back to hardcoded Gurugram coordinates when the browser did not
#   answer within 2.5s, meaning an emergency alert could carry a confidently
#   wrong location.
#
#   This table is the trail those columns implied. It is append-only: one row
#   per accepted fix, never updated. Agent.last_known_* is still maintained as
#   the denormalised "latest" so existing readers (manager list, field_ops) are
#   untouched — this table is additive, not a replacement.
#
#   Retention is enforced by workers/tasks/location_retention.py, not here.
# ───────────────────────────────────────────────────────────────────────────
import enum
from datetime import datetime

from sqlalchemy import (
    Float, Boolean, Integer, Enum as SAEnum, ForeignKey, Index, DateTime, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, SmallInteger
from app.models.base import PUBLIC, Base, UUIDPrimaryKey, UUIDType, uuid_fk


class LocationSource(str, enum.Enum):
    """Why this fix exists. Kept on the row so a trail can be read back with
    the events that punctuate it, rather than as an undifferentiated dot
    cloud — a manager reviewing an incident needs to see which point was the
    check-in and which was the SOS."""
    HEARTBEAT = "HEARTBEAT"   # periodic ping while on duty
    CHECK_IN  = "CHECK_IN"    # start of the agent's day
    VISIT     = "VISIT"       # captured when a visit was recorded
    SOS       = "SOS"         # captured during an active SOS


LOCATION_SOURCE_SQL = SAEnum(LocationSource, name="location_source_enum", schema=PUBLIC, metadata=Base.metadata)


class AgentLocation(Base, UUIDPrimaryKey):
    """One GPS fix for one agent. Append-only.

    2026-09-24 (B07): workforce schema; tenant columns; the agent FK is
    NO ACTION (this said RESTRICT; corrected 2026-09-24, see base.uuid_fk)
    (a cascade into a 30M-row partitioned table is a trap). On
    Postgres the table is partitioned LIST(is_sos) then RANGE(recorded_at)
    monthly, with PK (id, is_sos, recorded_at) — migration-only DDL (design
    §7); the ORM keeps `id` as its identity so lookups by id still work. The
    SOS partition is kept forever: retention can never drop an SOS point.
    """
    __tablename__ = "agent_locations"
    __tenant_parents__ = (("agent_id", "Agent"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agent_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agent_device_id: Mapped[str | None] = uuid_fk("workforce.agent_devices.id", nullable=True)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # Browser-reported horizontal accuracy. Retained because a 2km-accurate fix
    # and a 5m-accurate fix look identical on a map but mean very different
    # things during an SOS — the manager UI dims the low-confidence ones.
    accuracy_metres: Mapped[float | None] = mapped_column(Float, nullable=True)

    # recorded_at is the DEVICE clock at capture; received_at is the server
    # clock at write. They diverge whenever a batch was queued offline, and the
    # gap is exactly how long the agent was out of signal.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[LocationSource] = mapped_column(
        LOCATION_SOURCE_SQL, default=LocationSource.HEARTBEAT, nullable=False,
    )

    # True for every fix captured while an SOS was active. Denormalised
    # deliberately: an incident replay must not depend on joining against a
    # boolean that cancel_sos() has since cleared.
    is_sos: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phone battery at capture, 0-100. A lone worker whose phone is about to die
    # is a safety signal, not telemetry.
    battery_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    agent: Mapped["Agent"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Agent", lazy="noload", primaryjoin="AgentLocation.agent_id == Agent.id",
        foreign_keys="[AgentLocation.agent_id]")

    __table_args__ = (
        ForeignKeyConstraint(["agent_id", "agency_id"], ["workforce.agents.id", "workforce.agents.agency_id"]),
        CheckConstraint("battery_pct IS NULL OR (battery_pct >= 0 AND battery_pct <= 100)", name="battery"),
        # The trail query: one agent, one day, in order.
        Index("ix_agent_location_agent_time", "agent_id", "recorded_at"),
        Index(None, "agency_id", "recorded_at"),
        # 2026-09-24 (B11, lead-dev audit 3.11): ix_agent_location_recorded
        # (recorded_at) and ix_agent_location_sos (agent_id, is_sos,
        # recorded_at) are gone. On Postgres the table is LIST(is_sos) then
        # RANGE(recorded_at) by month, so retention drops whole partitions and
        # an SOS replay reads the SOS partition through
        # ix_agent_location_agent_time — the partitions ARE those indexes
        # (design §4.4), and each index cost a write on ~1.1M fixes a day.
        {"schema": "workforce"},
    )
