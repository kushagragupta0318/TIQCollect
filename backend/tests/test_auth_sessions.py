"""A05 sessions, A09 device binding, quick-login, and the manager's device reset.

2026-09-24 (coordinator audit gates 3, 6 and 8). Until this file none of these
had a test. Each test drives the real service or the real endpoint; nothing
here inspects source text. The login rate limit (10/min per client) is why the
session tests call auth_service directly and use HTTP only where the endpoint
itself is the subject.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_quick_login_token, decode_token, hash_password
from app.main import app
from app.models.agent import Agent, AgentDevice
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserSession
from app.models.user import User, UserRole
from app.services import auth_service
from tests._db import create_schema, make_engine, make_session_factory, test_id

PASSWORD = "Rakshit@2026"
DEVICE_A = "pixel-7a-8f3c"
DEVICE_B = "galaxy-a54-1d9e"


def _request(ua: str = "pytest") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", ua.encode())],
                    "client": ("10.0.0.7", 51000), "query_string": b""})


@pytest.fixture()
def world():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    mgr = User(id=test_id("u:mgr"), email="kavita.menon@aravallifs.in", phone="9810001001",
               full_name="Kavita Menon", hashed_password=hash_password(PASSWORD), role=UserRole.AGENCY_MANAGER)
    other = User(id=test_id("u:mgr2"), email="sanjay.dutt@aravallifs.in", phone="9810001002",
                 full_name="Sanjay Dutt", hashed_password=hash_password(PASSWORD), role=UserRole.AGENCY_MANAGER)
    au = User(id=test_id("u:agent"), email="rohit.yadav@aravallifs.in", phone="9810001003",
              full_name="Rohit Yadav", hashed_password=hash_password(PASSWORD), role=UserRole.FIELD_AGENT)
    db.add_all([mgr, other, au])
    db.flush()
    agent = Agent(id=test_id("agent"), user_id=au.id, employee_code="AFS0107", id_card_number="AFS-ID-0107",
                  base_latitude=28.45, base_longitude=77.07, territory="Sector 44, Gurugram",
                  manager_user_id=mgr.id)
    db.add(agent)
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "Session": Session, "mgr": mgr, "other": other, "agent_user": au, "agent": agent}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _login(w, user, device=DEVICE_A):
    return auth_service.login(w["db"], user.email, PASSWORD, device, _request())


def _audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


# ── A05: sessions ───────────────────────────────────────────────────────────

def test_each_login_is_its_own_session_and_the_token_names_it(world):
    a = _login(world, world["mgr"], "laptop-1")
    b = _login(world, world["mgr"], "tablet-2")
    sa, sb = decode_token(a["access_token"])["sid"], decode_token(b["access_token"])["sid"]
    assert sa != sb
    live = world["db"].query(UserSession).filter(UserSession.user_id == world["mgr"].id,
                                                 UserSession.revoked_at.is_(None)).count()
    assert live == 2


def test_refresh_rotates_and_a_replayed_token_revokes_only_that_session(world):
    first = _login(world, world["mgr"], "laptop-1")
    other_device = _login(world, world["mgr"], "tablet-2")
    rotated = auth_service.refresh_tokens(world["db"], first["refresh_token"], _request())
    assert rotated["refresh_token"] != first["refresh_token"]
    with pytest.raises(HTTPException) as exc:
        auth_service.refresh_tokens(world["db"], first["refresh_token"], _request())    # replay
    assert exc.value.status_code == 401
    sid = decode_token(first["access_token"])["sid"]
    world["db"].expire_all()
    assert world["db"].get(UserSession, sid).revoked_reason == "REUSE_DETECTED"
    # the replay killed the rotated token too (same session) ...
    with pytest.raises(HTTPException):
        auth_service.refresh_tokens(world["db"], rotated["refresh_token"], _request())
    # ... and nothing else: the tablet still refreshes.
    assert auth_service.refresh_tokens(world["db"], other_device["refresh_token"], _request())["access_token"]


def test_two_concurrent_refreshes_with_one_token_cannot_both_win(world, monkeypatch):
    """Gate 6. The race is forced deterministically: while the first refresh
    is between its read and its write, a second refresh with the SAME token
    completes on another session. The compare-and-swap must make the first
    one lose — and treat the loss as reuse."""
    tokens = _login(world, world["mgr"], "laptop-1")
    real = auth_service.create_refresh_token
    raced = {"done": False}

    def racing_create(*a, **kw):
        if not raced["done"]:
            raced["done"] = True
            other = world["Session"]()
            try:
                auth_service.refresh_tokens(other, tokens["refresh_token"], _request())   # the thief wins the row
            finally:
                other.close()
        return real(*a, **kw)

    monkeypatch.setattr(auth_service, "create_refresh_token", racing_create)
    with pytest.raises(HTTPException) as exc:
        auth_service.refresh_tokens(world["db"], tokens["refresh_token"], _request())
    assert exc.value.status_code == 401 and "reuse" in exc.value.detail.lower()
    world["db"].expire_all()
    sid = decode_token(tokens["access_token"])["sid"]
    assert world["db"].get(UserSession, sid).revoked_reason == "REUSE_DETECTED"


def test_logout_ends_the_session_and_its_access_token_at_once(world):
    tokens = _login(world, world["mgr"], "laptop-1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=h).status_code in (200, 204)
    r = client.get("/api/v1/auth/me", headers=h)
    assert r.status_code == 401, "an access token outlived its logged-out session"


# ── A09: device binding ─────────────────────────────────────────────────────

def test_the_first_agent_login_binds_the_device_and_the_same_device_is_welcome(world):
    _login(world, world["agent_user"], DEVICE_A)
    _login(world, world["agent_user"], DEVICE_A)
    bound = world["db"].query(AgentDevice).filter(AgentDevice.is_bound.is_(True)).all()
    assert len(bound) == 1


def test_a_different_device_is_refused_and_recorded(world, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_DEVICE_REBIND", False)
    _login(world, world["agent_user"], DEVICE_A)
    with pytest.raises(HTTPException) as exc:
        _login(world, world["agent_user"], DEVICE_B)
    assert exc.value.status_code == 403
    rows = _audits(world["db"], AuditAction.DEVICE_MISMATCH)
    assert len(rows) == 1 and rows[0].success is False


def test_demo_device_rebind_rebinds_and_says_so(world, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_DEVICE_REBIND", True)
    _login(world, world["agent_user"], DEVICE_A)
    _login(world, world["agent_user"], DEVICE_B)
    world["db"].expire_all()
    bound = world["db"].query(AgentDevice).filter(AgentDevice.is_bound.is_(True)).all()
    assert len(bound) == 1
    rows = _audits(world["db"], AuditAction.DEVICE_MISMATCH)
    assert len(rows) == 1 and rows[0].success is True and rows[0].details["rebound"] is True


def test_demo_mode_alone_no_longer_rebinds(world, monkeypatch):
    """Audit MED 7: DEMO_MODE used to switch this control off as a side effect."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "DEMO_DEVICE_REBIND", False)
    _login(world, world["agent_user"], DEVICE_A)
    with pytest.raises(HTTPException) as exc:
        _login(world, world["agent_user"], DEVICE_B)
    assert exc.value.status_code == 403


def test_two_first_logins_racing_get_a_403_and_an_audit_row_not_a_500(world, monkeypatch):
    """Gate 8. Both logins see no bound device; the second to commit hits the
    one-bound-device index. Forced by binding another device in between."""
    monkeypatch.setattr(settings, "DEMO_DEVICE_REBIND", False)
    real_open = auth_service._open_session

    def racing_open(db, user, device_id, request):
        other = world["Session"]()
        try:
            other.add(AgentDevice(agent_id=world["agent"].id, device_fingerprint="f" * 64, is_bound=True,
                                  bound_at=datetime.now(timezone.utc)))
            other.commit()
        finally:
            other.close()
        return real_open(db, user, device_id, request)

    monkeypatch.setattr(auth_service, "_open_session", racing_open)
    with pytest.raises(HTTPException) as exc:
        _login(world, world["agent_user"], DEVICE_A)
    assert exc.value.status_code == 403
    rows = _audits(world["db"], AuditAction.DEVICE_MISMATCH)
    assert rows and rows[-1].failure_reason.startswith("Concurrent first login")


# ── quick login ─────────────────────────────────────────────────────────────

def test_a_quick_login_link_works_once(world):
    token = create_quick_login_token(world["mgr"].id, agency_code="AGENCY-TIQ-001")
    assert auth_service.quick_login(world["db"], token, _request())["access_token"]
    with pytest.raises(HTTPException) as exc:
        auth_service.quick_login(world["db"], token, _request())
    assert exc.value.status_code == 401


def test_a_quick_login_link_never_signs_in_a_field_agent(world):
    """It skips device binding, so an agent signed in by a link would bypass A09."""
    token = create_quick_login_token(world["agent_user"].id, agency_code="AGENCY-TIQ-001")
    with pytest.raises(HTTPException) as exc:
        auth_service.quick_login(world["db"], token, _request())
    assert exc.value.status_code == 403
    assert world["db"].query(UserSession).filter(UserSession.user_id == world["agent_user"].id).count() == 0


# ── the manager's device reset (gate 3) ─────────────────────────────────────

def test_reset_device_unbinds_revokes_sessions_and_audits(world, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_DEVICE_REBIND", False)
    agent_tokens = _login(world, world["agent_user"], DEVICE_A)
    mgr_tokens = _login(world, world["mgr"], "laptop-1")
    client = TestClient(app)
    r = client.post(f"/api/v1/manager/agents/{world['agent'].id}/reset-device",
                    headers={"Authorization": f"Bearer {mgr_tokens['access_token']}"})
    assert r.status_code == 200, r.text
    assert r.json()["devices_unbound"] == 1 and r.json()["sessions_revoked"] == 1
    db = world["db"]
    db.expire_all()
    assert db.query(AgentDevice).filter(AgentDevice.is_bound.is_(True)).count() == 0
    sid = decode_token(agent_tokens["access_token"])["sid"]
    assert db.get(UserSession, sid).revoked_reason == "DEVICE_RESET"
    assert len(_audits(db, AuditAction.DEVICE_RESET)) == 1
    # the agent can now bind a NEW phone
    _login(world, world["agent_user"], DEVICE_B)


def test_reset_device_on_another_managers_agent_is_a_404(world):
    tokens = _login(world, world["other"], "laptop-9")
    r = TestClient(app).post(f"/api/v1/manager/agents/{world['agent'].id}/reset-device",
                             headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 404
    assert world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.DEVICE_RESET).count() == 0
