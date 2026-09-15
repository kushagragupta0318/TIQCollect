"""LocationService — the stationary heartbeat and the live map's "last seen".

2026-09-14. The service had no tests. What is pinned here is the one
behaviour that was wrong on the live map: a heartbeat within MIN_MOVE_METRES
of the last stored fix is (correctly) not written as a row, but it IS the
agent reporting in, so the agent's heard-from time must advance and the
manager's map must read it. Before this, a parked agent uploading every
minute showed "28 min ago", because the age came only from stored rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.schemas.agent import LocationPing
from app.core.database import Base
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.agent_location import AgentLocation
from app.models.user import User, UserRole
from app.services.location_service import MIN_MOVE_METRES, LocationService

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

GURGAON = (28.4595, 77.0266)


def _uid():
    return str(uuid.uuid4())


@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    s = Session()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def agent(db):
    mgr = User(id=_uid(), email="m@t.io", phone="9000000001", full_name="Manager",
               hashed_password="x", role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True)
    u = User(id=_uid(), email="a@t.io", phone="9000000002", full_name="Agent One",
             hashed_password="x", role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
    db.add_all([mgr, u]); db.flush()
    a = Agent(id=_uid(), user_id=u.id, employee_code="EMP0001", id_card_number="EMP0001-ID",
              agency_id="TIQ-DELHI-01", manager_user_id=mgr.id, gender="M",
              base_latitude=GURGAON[0], base_longitude=GURGAON[1], territory="Gurgaon",
              languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, ranking_score=80.0)
    db.add(a); db.commit()
    return a


def _ping(at: datetime, lat=GURGAON[0], lon=GURGAON[1], source="HEARTBEAT"):
    return LocationPing(latitude=lat, longitude=lon, accuracy_metres=20.0,
                        recorded_at=at, source=source)


def test_a_stationary_heartbeat_is_not_stored_but_is_heard(db, agent):
    svc = LocationService(db)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    first = svc.record_batch(agent, [_ping(t0)])
    assert first["accepted"] == 1

    # Same spot, 15 s later — within MIN_MOVE_METRES, so no row...
    t1 = t0 + timedelta(seconds=15)
    second = svc.record_batch(agent, [_ping(t1)])
    assert second == {"accepted": 0, "rejected": 1, "last_recorded_at": None}
    assert db.query(AgentLocation).filter_by(agent_id=agent.id).count() == 1

    # ...but the agent was heard from at t1, and the map says so.
    db.refresh(agent)
    assert datetime.fromisoformat(agent.last_location_update) == t1
    live = {p["agent_id"]: p for p in svc.live_positions([agent.id])}[agent.id]
    assert datetime.fromisoformat(live["recorded_at"]) == t1
    assert 280 <= live["age_seconds"] <= 300        # t1 is 4m45s ago by construction
    assert (live["latitude"], live["longitude"]) == GURGAON


def test_the_heard_from_time_only_moves_forward(db, agent):
    """A late-arriving stationary ping from BEFORE the newest stored row must
    not drag last-seen backwards — same rule as the stored-row path."""
    svc = LocationService(db)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    svc.record_batch(agent, [_ping(t0)])
    svc.record_batch(agent, [_ping(t0 - timedelta(minutes=1))])   # older, stationary
    db.refresh(agent)
    assert datetime.fromisoformat(agent.last_location_update) == t0


def test_a_real_move_still_writes_a_row_and_wins_over_heard(db, agent):
    svc = LocationService(db)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    svc.record_batch(agent, [_ping(t0)])
    svc.record_batch(agent, [_ping(t0 + timedelta(seconds=15))])       # stationary
    moved = svc.record_batch(agent, [_ping(t0 + timedelta(seconds=30),
                                           lat=GURGAON[0] + 0.001)])  # ~110 m north
    assert moved["accepted"] == 1
    assert db.query(AgentLocation).filter_by(agent_id=agent.id).count() == 2
    live = svc.live_positions([agent.id])[0]
    assert datetime.fromisoformat(live["recorded_at"]) == t0 + timedelta(seconds=30)
    assert live["latitude"] == pytest.approx(GURGAON[0] + 0.001)


def test_the_stationary_threshold_is_the_one_the_client_mirrors():
    # The client thins at 50 m; the server must be no stricter than that or a
    # walking agent's every-other fix would be dropped as "stationary".
    assert MIN_MOVE_METRES <= 50.0


def test_a_checkin_far_from_the_last_fix_is_not_shown_at_the_old_fix(db, agent):
    """agent_service.checkin and visit_service write last_known_lat/lon and
    last_location_update with NO AgentLocation row and no proximity constraint.
    The live map must then report the CHECK-IN position, not the stored fix's
    position with the check-in's fresh timestamp."""
    svc = LocationService(db)
    t0 = datetime.now(timezone.utc) - timedelta(hours=3)
    svc.record_batch(agent, [_ping(t0)])                      # stored row, Gurgaon

    # 10 km away, an hour later, exactly as checkin() writes it.
    t1 = t0 + timedelta(hours=1)
    agent.last_known_latitude = GURGAON[0] + 0.09
    agent.last_known_longitude = GURGAON[1]
    agent.last_location_update = t1.isoformat()
    db.commit()

    live = svc.live_positions([agent.id])[0]
    assert datetime.fromisoformat(live["recorded_at"]) == t1
    assert live["latitude"] == pytest.approx(GURGAON[0] + 0.09)   # NOT the stored fix
    assert live["longitude"] == pytest.approx(GURGAON[1])
    # Accuracy/battery describe the stored fix, which is not where we are.
    assert live["accuracy_metres"] is None and live["battery_pct"] is None


def test_a_stationary_heartbeat_keeps_the_accuracy_of_the_fix_it_repeats(db, agent):
    """The far-check-in case above withholds accuracy because it describes
    somewhere else. A heartbeat repeats the SAME place, so withholding there
    would strip the live map's +/- metres from every parked agent."""
    svc = LocationService(db)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    svc.record_batch(agent, [_ping(t0)])
    svc.record_batch(agent, [_ping(t0 + timedelta(seconds=15))])   # stationary
    live = svc.live_positions([agent.id])[0]
    assert live["accuracy_metres"] == 20.0
    assert datetime.fromisoformat(live["recorded_at"]) == t0 + timedelta(seconds=15)


def test_an_older_agent_row_never_overrides_the_newest_stored_fix(db, agent):
    svc = LocationService(db)
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    agent.last_known_latitude = GURGAON[0] + 0.09
    agent.last_known_longitude = GURGAON[1]
    agent.last_location_update = t0.isoformat()
    db.commit()

    t1 = t0 + timedelta(hours=1)                              # newer real movement
    svc.record_batch(agent, [_ping(t1, lat=GURGAON[0], lon=GURGAON[1])])
    live = svc.live_positions([agent.id])[0]
    assert datetime.fromisoformat(live["recorded_at"]) == t1
    assert live["latitude"] == pytest.approx(GURGAON[0])
    assert live["accuracy_metres"] == 20.0
