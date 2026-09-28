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
def _make_agent_with_a_real_password(w, tag: str, password: str = "Rakshit@2026") -> tuple[User, Agent]:
    """Unlike create_agent's own agents (disabled_password_hash by design —
    nobody, including the manager, ever knows their password), these tests
    need to actually sign one in, so this constructs the User/Agent pair
    directly with a real, known password."""
    from app.core.security import hash_password
    db = w["db"]
    u = User(id=test_id(f"u:agent:{tag}"), email=f"{tag}@meridiantrust.example", phone=f"98100060{len(tag):02d}",
             full_name=f"Agent {tag}", hashed_password=hash_password(password), role=UserRole.FIELD_AGENT,
             bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, is_active=True, is_verified=True)
    db.add(u)
    db.flush()
    a = Agent(id=test_id(f"agent:{tag}"), user_id=u.id, employee_code=f"{tag.upper()}01",
             id_card_number=f"{tag.upper()}-ID-01", manager_user_id=w["mgr"].id,
             base_latitude=28.45, base_longitude=77.07, territory="Sector 44, Gurugram",
             bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, status=AgentStatus.OFF_DUTY)
    db.add(a)
    db.commit()
    return u, a


def _request():
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "client": ("10.0.0.9", 1), "query_string": b""})


def test_a_suspended_agent_is_deactivated_and_cannot_log_in(w):
    """coordinator audit HIGH: suspend_agent used to touch only Agent.status
    — User.is_active stayed True, and login/refresh check only is_active,
    so a suspended agent could sign straight back in on a fresh device."""
    from app.services import auth_service

    db = w["db"]
    u, a = _make_agent_with_a_real_password(w, "highsev")
    suspend_agent(db, w["mgr"], a.id, reason="Compliance hold")
    db.expire_all()
    assert db.get(User, u.id).is_active is False

    with pytest.raises(Exception) as exc:
        auth_service.login(db, u.email, "Rakshit@2026", "dev-1", _request())
    assert getattr(exc.value, "status_code", None) == 403


def test_a_suspended_agents_refresh_token_is_refused_even_if_the_session_somehow_survived(w):
    """Belt and braces: suspend_agent already revokes every live session, so
    this constructs a session AFTER suspension (simulating is_active and
    Agent.status having gone out of sync some other way) to prove the
    Agent.status check in refresh_tokens is real, not just redundant with
    the session revocation."""
    from app.core.security import create_refresh_token, token_sha256
    from app.services import auth_service

    db = w["db"]
    u, a = _make_agent_with_a_real_password(w, "highsev2")
    suspend_agent(db, w["mgr"], a.id, reason="Compliance hold")

    sid = test_id("session:highsev2")
    token = create_refresh_token(u.id, "dev-1", sid=sid)
    db.add(UserSession(id=sid, user_id=u.id, device_id="dev-1", refresh_token_sha256=token_sha256(token),
                       refresh_jti="jti-highsev2", expires_at=datetime(2030, 1, 1)))
    db.commit()

    with pytest.raises(Exception) as exc:
        auth_service.refresh_tokens(db, token, _request())
    assert getattr(exc.value, "status_code", None) == 401


def test_reactivate_restores_is_active(w):
    db = w["db"]
    u, a = _make_agent_with_a_real_password(w, "reactive")
    suspend_agent(db, w["mgr"], a.id, reason="Under review")
    reactivate_agent(db, w["mgr"], a.id)
    db.expire_all()
    assert db.get(User, u.id).is_active is True


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


def test_suspend_with_a_reason_over_500_characters_is_refused(w):
    with pytest.raises(AppException) as exc:
        suspend_agent(w["db"], w["mgr"], w["agent_id"], reason="A" * 501)
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


# ── phone-change hold (coordinator audit on 084d9ba) ────────────────────────
# Without this, a manager edits an agent's phone to one they control, resets
# login, and signs in AS the agent: every visit and payment they record after
# that reads as the agent's own.
def test_reset_login_refuses_within_24h_of_a_phone_change_on_an_established_account(w, monkeypatch):
    from datetime import timezone
    from app.services.agent_management_service import edit_agent

    db = w["db"]
    # An "established" account: never_had_a_password looks at last_login_at /
    # password_changed_at / any UserSession row. A fresh create_agent() agent
    # has none of these — this simulates one that does.
    agent_user = db.get(User, w["agent_user_id"])
    agent_user.password_changed_at = datetime.now(timezone.utc)
    db.commit()

    edit_agent(db, w["mgr"], w["agent_id"], phone="9810005088")
    with pytest.raises(AppException) as exc:
        reset_agent_login(db, w["mgr"], w["agent_id"])
    assert exc.value.status_code == 409


def test_reset_login_is_not_held_after_a_phone_change_for_a_brand_new_agent(w):
    """never_had_a_password is True right after create_agent — the hold does
    not apply, or a manager could never fix a typo'd phone number before an
    agent's very first login."""
    from app.services.agent_management_service import edit_agent

    db = w["db"]
    edit_agent(db, w["mgr"], w["agent_id"], phone="9810005089")
    # Falls through to admin_reset, which then hits the real (unrelated)
    # gate in this test environment — proving the phone-change hold itself
    # did not fire, not that reset-login unconditionally succeeds.
    with pytest.raises(AppException) as exc:
        reset_agent_login(db, w["mgr"], w["agent_id"])
    assert exc.value.status_code == 503


def test_reset_login_is_allowed_again_once_the_hold_window_has_passed(w):
    from datetime import timedelta, timezone
    from app.models.audit_log import AuditLog
    from app.services.agent_management_service import PHONE_CHANGE_HOLD, edit_agent

    db = w["db"]
    agent_user = db.get(User, w["agent_user_id"])
    agent_user.password_changed_at = datetime.now(timezone.utc)
    db.commit()

    edit_agent(db, w["mgr"], w["agent_id"], phone="9810005090")
    # entity_type/entity_id alone also matches create_agent's own
    # USER_CREATED row (same Agent entity) — the action filter is what
    # narrows this to the edit.
    old_row = (db.query(AuditLog)
              .filter(AuditLog.entity_type == "Agent", AuditLog.entity_id == w["agent_id"],
                      AuditLog.action == AuditAction.AGENT_UPDATED).one())
    old_row.created_at = datetime.now(timezone.utc) - PHONE_CHANGE_HOLD - timedelta(minutes=1)
    db.commit()

    with pytest.raises(AppException) as exc:
        reset_agent_login(db, w["mgr"], w["agent_id"])
    assert exc.value.status_code == 503   # past the gate; 503 is the unrelated PUBLIC_BASE_URL check


def test_edit_agent_phone_change_texts_the_old_number(w, monkeypatch):
    from app.services import agent_management_service, notification_service

    sent = []
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda phone, body, **kw: sent.append((phone, body)) or True))

    db = w["db"]
    old_phone = db.get(User, w["agent_user_id"]).phone
    agent_management_service.edit_agent(db, w["mgr"], w["agent_id"], phone="9810005091")

    assert len(sent) == 1
    phone, body = sent[0]
    assert old_phone in phone   # normalize_phone/"+" wrapping is NotificationService's own concern
    assert w["mgr"].full_name in body


def test_edit_agent_without_a_phone_change_sends_no_sms(w, monkeypatch):
    from app.services import agent_management_service, notification_service

    sent = []
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda *a, **kw: sent.append(1) or True))
    agent_management_service.edit_agent(w["db"], w["mgr"], w["agent_id"], territory="Sector 60, Gurugram")
    assert sent == []


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
