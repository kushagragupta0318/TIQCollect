# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (P2 G02). suspend_agent / reactivate_agent /
# reset_agent_login (agent_management_service.py) and their routes.
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.errors import AppException
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentStatus
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserSession
from app.models.tenancy import Agency
from app.models.user import User, UserRole
from app.services.agent_management_service import create_agent, reactivate_agent, reset_agent_login, suspend_agent
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

OTHER_AGENCY = test_id("agency:manage-agents-lifecycle-other")


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    db.add(Agency(id=OTHER_AGENCY, bank_id=TEST_BANK_ID, code="AGENCY-MAL-OTHER",
                  legal_name="Sundarbans Field Solutions LLP", trade_name="Sundarbans Field Solutions",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.flush()

    mgr = User(id=test_id("u:mgr:mal"), email="deepak.rao@meridiantrust.example", phone="9810005001",
              full_name="Deepak Rao", hashed_password="x", role=UserRole.AGENCY_MANAGER,
              bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    mgr2 = User(id=test_id("u:mgr2:mal"), email="second.manager@meridiantrust.example", phone="9810005002",
               full_name="Second Manager", hashed_password="x", role=UserRole.AGENCY_MANAGER,
               bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    mgr_other = User(id=test_id("u:mgr:mal:other"), email="other.agency.mgr@meridiantrust.example",
                     phone="9810005003", full_name="Other Agency Manager", hashed_password="x",
                     role=UserRole.AGENCY_MANAGER, bank_id=TEST_BANK_ID, agency_id=OTHER_AGENCY)
    db.add_all([mgr, mgr2, mgr_other])
    db.commit()

    out = create_agent(
        db, mgr, full_name="Field Agent One", email="fieldagent1@meridiantrust.example", phone="9810005004",
        employee_code="MAL0001", id_card_number="MAL-ID-0001", base_latitude=28.45, base_longitude=77.07,
        territory="Sector 44, Gurugram",
    )
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "mgr": mgr, "mgr2": mgr2, "mgr_other": mgr_other, "agent_id": out["agent_id"],
              "agent_user_id": out["user_id"]}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _live_session(db, user_id: str, device: str) -> UserSession:
    s = UserSession(user_id=user_id, device_id=device, refresh_token_sha256=f"sha-{device}",
                    refresh_jti=f"jti-{device}", expires_at=datetime(2030, 1, 1))
    db.add(s)
    db.commit()
    return s


# ── suspend ───────────────────────────────────────────────────────────────
def test_suspend_sets_status_and_reason(w):
    db = w["db"]
    out = suspend_agent(db, w["mgr"], w["agent_id"], reason="Repeated compliance violations")
    assert out["status"] == "SUSPENDED"
    agent = db.get(Agent, w["agent_id"])
    assert agent.status == AgentStatus.SUSPENDED
    assert agent.suspended_reason == "Repeated compliance violations"
    assert agent.suspended_at is not None


def test_suspend_ends_every_live_session(w):
    db = w["db"]
    s1 = _live_session(db, w["agent_user_id"], "phone-1")
    s2 = _live_session(db, w["agent_user_id"], "phone-2")
    suspend_agent(db, w["mgr"], w["agent_id"], reason="Fraud review")
    db.expire_all()
    assert db.get(UserSession, s1.id).revoked_at is not None
    assert db.get(UserSession, s2.id).revoked_at is not None


def test_suspend_without_a_reason_is_refused(w):
    with pytest.raises(AppException) as exc:
        suspend_agent(w["db"], w["mgr"], w["agent_id"], reason="  ")
    assert exc.value.status_code == 422


def test_suspending_an_already_suspended_agent_is_refused(w):
    db = w["db"]
    suspend_agent(db, w["mgr"], w["agent_id"], reason="First reason")
    with pytest.raises(AppException) as exc:
        suspend_agent(db, w["mgr"], w["agent_id"], reason="Second reason")
    assert exc.value.status_code == 409


def test_suspend_is_audited(w):
    db = w["db"]
    suspend_agent(db, w["mgr"], w["agent_id"], reason="Repeated compliance violations")
    rows = db.query(AuditLog).filter(AuditLog.entity_type == "Agent",
                                     AuditLog.entity_id == w["agent_id"]).all()
    row = next(r for r in rows if r.details.get("event") == "AGENT_SUSPENDED")
    assert row.action == AuditAction.AGENT_STATUS_CHANGED
    assert row.user_id == w["mgr"].id


def test_a_manager_cannot_suspend_another_managers_agent_in_the_same_agency(w):
    with pytest.raises(AppException) as exc:
        suspend_agent(w["db"], w["mgr2"], w["agent_id"], reason="Not my agent")
    assert exc.value.status_code == 404


def test_a_manager_cannot_suspend_an_agent_in_another_agency(w):
    with pytest.raises(AppException) as exc:
        suspend_agent(w["db"], w["mgr_other"], w["agent_id"], reason="Cross-tenant attempt")
    assert exc.value.status_code == 404


# ── reactivate ───────────────────────────────────────────────────────────
def test_reactivate_returns_the_agent_to_off_duty_and_clears_the_reason(w):
    db = w["db"]
    suspend_agent(db, w["mgr"], w["agent_id"], reason="Under review")
    out = reactivate_agent(db, w["mgr"], w["agent_id"])
    assert out["status"] == "OFF_DUTY"
    agent = db.get(Agent, w["agent_id"])
    assert agent.status == AgentStatus.OFF_DUTY
    assert agent.suspended_at is None and agent.suspended_reason is None


def test_reactivating_a_non_suspended_agent_is_refused(w):
    with pytest.raises(AppException) as exc:
        reactivate_agent(w["db"], w["mgr"], w["agent_id"])
    assert exc.value.status_code == 409


def test_a_manager_cannot_reactivate_an_agent_in_another_agency(w):
    db = w["db"]
    suspend_agent(db, w["mgr"], w["agent_id"], reason="Under review")
    with pytest.raises(AppException) as exc:
        reactivate_agent(db, w["mgr_other"], w["agent_id"])
    assert exc.value.status_code == 404


# ── reset login ──────────────────────────────────────────────────────────
def test_reset_login_refuses_across_agencies(w):
    with pytest.raises(AppException) as exc:
        reset_agent_login(w["db"], w["mgr_other"], w["agent_id"])
    assert exc.value.status_code == 404


def test_reset_login_refuses_for_a_peer_managers_agent(w):
    with pytest.raises(AppException) as exc:
        reset_agent_login(w["db"], w["mgr2"], w["agent_id"])
    assert exc.value.status_code == 404


def test_reset_login_without_public_base_url_is_a_clean_service_unavailable(w, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "", raising=False)
    with pytest.raises(AppException) as exc:
        reset_agent_login(w["db"], w["mgr"], w["agent_id"])
    assert exc.value.status_code == 503


# ── HTTP ─────────────────────────────────────────────────────────────────
def test_http_suspend_reactivate_round_trip(w):
    client = TestClient(app)
    r = client.post(f"/api/v1/manager/agents/{w['agent_id']}/suspend", json={"reason": "Compliance hold"},
                    headers=_h(w["mgr"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUSPENDED"

    r = client.post(f"/api/v1/manager/agents/{w['agent_id']}/reactivate", headers=_h(w["mgr"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "OFF_DUTY"


def test_http_suspend_refused_for_another_agencys_manager(w):
    client = TestClient(app)
    r = client.post(f"/api/v1/manager/agents/{w['agent_id']}/suspend", json={"reason": "Not yours"},
                    headers=_h(w["mgr_other"]))
    assert r.status_code == 404


def test_http_suspend_refused_for_a_field_agent_principal(w):
    client = TestClient(app)
    agent_user = User(id=test_id("u:fa:mal"), email="fa-principal@meridiantrust.example", phone="9810005099",
                      full_name="A Field Agent", hashed_password="x", role=UserRole.FIELD_AGENT,
                      bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    w["db"].add(agent_user)
    w["db"].commit()
    r = client.post(f"/api/v1/manager/agents/{w['agent_id']}/suspend", json={"reason": "x"},
                    headers=_h(agent_user))
    assert r.status_code == 403


def test_http_reset_login_returns_a_clean_channel_unavailable_without_public_base_url(w):
    client = TestClient(app)
    r = client.post(f"/api/v1/manager/agents/{w['agent_id']}/reset-login", headers=_h(w["mgr"]))
    # PUBLIC_BASE_URL is unset in the test environment, so this is the
    # expected shape, not a fixture gap — see reset_agent_login's own
    # not-configured test above for the service-level version.
    assert r.status_code == 503


def test_reset_login_route_is_rate_limited_per_caller_not_per_agent_id(w):
    """d4's own pattern (test_accounts_password.py::test_the_reset_route_is_
    rate_limited): a plain @limiter.limit counts per URL PATH, so with
    {agent_id} in the path each id would get its own ten-a-minute bucket and
    a caller spraying resets across a whole roster would never be bounded.
    reset-login shares scope="admin-credential-links" with
    /admin/users/{id}/password-reset and mfa-reset for exactly this reason
    — one bucket per caller, regardless of which id each call names. Random
    ids (never-existing agents) keep every call a clean 404 up to the
    limit, so the per-target cooldown inside admin_reset never gets a say —
    only the route-level limiter is under test."""
    import uuid
    from app.core.ratelimit import AUTH_LIMIT, limiter

    was, limiter.enabled = limiter.enabled, True
    limiter.reset()
    try:
        client = TestClient(app)
        headers = _h(w["mgr"])
        n = int(str(AUTH_LIMIT).split("/")[0])
        codes = [client.post(f"/api/v1/manager/agents/{uuid.uuid4()}/reset-login",
                             headers=headers).status_code for _ in range(n + 1)]
        assert codes[:n] == [404] * n and codes[n] == 429
    finally:
        limiter.reset()
        limiter.enabled = was
