"""Live field events — core/events.py and /api/v1/events/*.

2026-09-24 (standalone plan, P0-05/P0-06). What is pinned:

- publishing reaches the agent's MANAGER's channel and nobody else's;
- it never raises: an unknown type, an unassigned agent, a dead Redis all
  return False and the caller carries on;
- a Redis outage costs immediacy, not the event — it is still readable from
  /events/recent through the in-process fallback;
- /events/recent is manager-only and scoped to the caller;
- the commit points actually publish (check-in and a location batch are run
  through the real services, not asserted from source text);
- /events/stream answers 503 SERVICE_UNAVAILABLE when Redis is unreachable,
  which is the frontend's cue to poll instead.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import events as ev
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.user import User, UserRole
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)


class FakeStore:
    is_memory = False

    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.lists: dict[str, list[str]] = defaultdict(list)

    def publish_recent(self, channel, key, payload):
        self.published.append((channel, json.loads(payload)))
        self.lists[key].insert(0, payload)

    def recent(self, key, limit):
        return self.lists[key][:limit]


class BrokenStore:
    is_memory = False

    def publish_recent(self, channel, key, payload):
        raise ConnectionError("redis went away")

    def recent(self, key, limit):
        raise ConnectionError("redis went away")


@pytest.fixture
def store():
    s = FakeStore()
    ev.set_store_for_tests(s)
    yield s
    ev.set_store_for_tests(None)


def _agent(mgr="mgr-1"):
    return SimpleNamespace(id=test_id("agent-1"), manager_user_id=mgr,
                           user=SimpleNamespace(full_name="Asha Verma"))


# ── publish_event ────────────────────────────────────────────────────────────

def test_an_event_goes_to_the_agents_manager_channel(store):
    assert ev.publish_event("visit.recorded", agent=_agent(), data={"case_id": "c1"}) is True
    [(channel, event)] = store.published
    assert channel == ev.manager_channel("mgr-1")
    assert event["type"] == "visit.recorded"
    assert event["agent_id"] == test_id("agent-1")
    assert event["agent_name"] == "Asha Verma"
    assert event["data"] == {"case_id": "c1"}
    assert event["id"] and event["at"]


def test_an_unknown_type_is_refused_not_raised(store):
    assert ev.publish_event("visit.recroded", agent=_agent()) is False
    assert store.published == []


def test_an_agent_with_no_manager_publishes_nothing(store):
    assert ev.publish_event("agent.location", agent=_agent(mgr=None)) is False
    assert store.published == []


def test_a_redis_outage_costs_immediacy_not_the_event():
    ev.set_store_for_tests(BrokenStore())
    try:
        # Never raises, reports that the push did not happen...
        assert ev.publish_event("ptp.set", agent=_agent(), data={"n": 1}) is False
        # ...and a late joiner still finds it, from the in-process fallback.
        recent = ev.recent_events("mgr-1")
        assert [e["type"] for e in recent] == ["ptp.set"]
    finally:
        ev.set_store_for_tests(None)


def test_recent_is_newest_first_and_capped(store):
    for i in range(5):
        ev.publish_event("agent.location", agent=_agent(), data={"i": i})
    got = ev.recent_events("mgr-1", limit=3)
    assert [e["data"]["i"] for e in got] == [4, 3, 2]


# ── the commit points publish ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def world():
    create_schema(engine)
    db = TestingSession()

    def user(email, role, name):
        u = User(id=str(uuid.uuid4()), email=email, phone=str(uuid.uuid4().int)[:10],
                 full_name=name, hashed_password="x", role=role, is_active=True, is_verified=True)
        db.add(u)
        return u

    m1 = user("m1@t.io", UserRole.AGENCY_MANAGER, "Manager One")
    m2 = user("m2@t.io", UserRole.AGENCY_MANAGER, "Manager Two")
    au = user("a1@t.io", UserRole.FIELD_AGENT, "Asha Verma")
    db.flush()
    agent = Agent(id=str(uuid.uuid4()), user_id=au.id, employee_code="EMP9001",
                  id_card_number="EMP9001-ID", agency_id="AG-1", manager_user_id=m1.id,
                  gender="F", base_latitude=28.45, base_longitude=77.07, territory="Gurugram",
                  languages_spoken=["HINDI"], status=AgentStatus.OFF_DUTY, tier=AgentTier.TIER_1,
                  specialization=AgentSpecialization.BOTH, ranking_score=80.0)
    db.add(agent)
    db.commit()
    yield {"db": db, "m1": m1, "m2": m2, "au": au, "agent": agent}
    db.close()
    drop_schema(engine)


def test_check_in_publishes_with_its_position(store, world, monkeypatch):
    from app.services.agent_service import AgentService
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "DEMO_REHEARSAL_MODE", False)
    AgentService(world["db"]).checkin(world["agent"], SimpleNamespace(latitude=28.46, longitude=77.08))
    [(channel, event)] = store.published
    assert channel == ev.manager_channel(world["m1"].id)
    assert event["type"] == "agent.checked_in"
    assert event["data"] == {"lat": 28.46, "lon": 77.08}


def test_a_location_batch_publishes_once_with_the_newest_fix(store, world):
    from app.services.location_service import LocationService
    now = datetime.now(timezone.utc)
    pings = [SimpleNamespace(latitude=28.45 + i * 0.01, longitude=77.07, recorded_at=now.replace(microsecond=i),
                             source="GPS", accuracy_metres=5.0, battery_pct=90)
             for i in range(3)]
    out = LocationService(world["db"]).record_batch(world["agent"], pings)
    assert out["accepted"] == 3
    located = [e for _, e in store.published if e["type"] == "agent.location"]
    assert len(located) == 1, "one event per batch, never one per row"
    assert located[0]["data"]["lat"] == pytest.approx(28.47)
    assert located[0]["data"]["accepted"] == 3


# ── the endpoints ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(world):
    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _hdr(user, role):
    return {"Authorization": f"Bearer {create_access_token(user.id, role, 'test-device-01')}"}


def test_recent_is_scoped_to_the_calling_manager(store, client, world):
    ev.publish_event("visit.recorded", manager_user_id=world["m1"].id, data={"who": "m1"})
    ev.publish_event("visit.recorded", manager_user_id=world["m2"].id, data={"who": "m2"})
    r1 = client.get("/api/v1/events/recent", headers=_hdr(world["m1"], "AGENCY_MANAGER"))
    assert r1.status_code == 200, r1.text
    assert [e["data"]["who"] for e in r1.json()["events"]] == ["m1"]
    r2 = client.get("/api/v1/events/recent", headers=_hdr(world["m2"], "AGENCY_MANAGER"))
    assert [e["data"]["who"] for e in r2.json()["events"]] == ["m2"]


def test_recent_refuses_an_agent(client, world):
    r = client.get("/api/v1/events/recent", headers=_hdr(world["au"], "FIELD_AGENT"))
    assert r.status_code == 403


def test_the_stream_is_never_gzipped_and_everything_else_still_is():
    # Measured 2026-09-24: through Starlette 0.41's GZipMiddleware a connected
    # stream delivered NOTHING in 8 s (zlib buffers small writes); exempted, the
    # same event arrived in 40 ms. The exemption must pass the stream's `send`
    # through untouched and must not switch compression off anywhere else.
    import asyncio

    from app.main import _GZipExceptEventStream

    seen: dict[str, object] = {}

    async def inner(scope, receive, send):
        seen[scope["path"]] = send

    mw = _GZipExceptEventStream(inner, minimum_size=1000)

    async def send(message):  # the server's own send
        pass

    async def receive():
        return {"type": "http.request"}

    def scope(path):
        return {"type": "http", "path": path, "headers": [(b"accept-encoding", b"gzip")]}

    asyncio.run(mw(scope("/api/v1/events/stream"), receive, send))
    asyncio.run(mw(scope("/api/v1/manager/dashboard"), receive, send))
    assert seen["/api/v1/events/stream"] is send, "the stream must reach the server unwrapped"
    assert seen["/api/v1/manager/dashboard"] is not send, "everything else is still gzip-wrapped"


def test_stream_says_unavailable_when_redis_is_down(client, world, monkeypatch):
    # Port 1 is never a Redis. The stream must refuse cleanly — 503 with the
    # typed code the frontend switches to polling on — rather than hang or 500.
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    r = client.get("/api/v1/events/stream", headers=_hdr(world["m1"], "AGENCY_MANAGER"))
    assert r.status_code == 503
    assert r.json()["code"] == "SERVICE_UNAVAILABLE"
