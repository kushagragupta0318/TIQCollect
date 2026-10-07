"""K01 — Admin > Bank Users (P3, 2026-10-07).

A BANK_ADMIN manages its own bank's staff accounts: list, invite, change an
analyst/techops role, deactivate / reactivate, reset login. A bank admin sees
and touches only its own bank; a non-admin bank role is refused at the
capability gate; a fellow bank admin and the admin's own account are out of
reach. The session factory carries NO default tenant, so a user built without
its bank is rejected rather than silently filled in.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.database import get_db
from app.core.errors import AppException, ErrorCode
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserSession
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.services.bank import bank_user_service
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BANK2 = test_id("bank:girivan")


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


def _user(db, key, role, *, bank=TEST_BANK_ID, agency=None, phone, active=True):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency,
             is_active=active)
    db.add(u)
    return u


@pytest.fixture()
def world():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})          # no default tenant
    db = Session()
    db.add(Bank(id=BANK2, code="GFL", legal_name="Girivan Finance Ltd.", display_name="Girivan Finance",
                timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()
    w = {
        "db": db, "Session": Session,
        "bank_admin": _user(db, "bankadmin", UserRole.BANK_ADMIN, phone="9800000002"),
        "bank_admin2": _user(db, "bankadmin2", UserRole.BANK_ADMIN, phone="9800000012"),
        "analyst": _user(db, "analyst", UserRole.BANK_ANALYST, phone="9800000003"),
        "techops": _user(db, "techops", UserRole.BANK_TECHOPS, phone="9800000004"),
        "bank2_admin": _user(db, "bank2admin", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000005"),
        "bank2_analyst": _user(db, "bank2analyst", UserRole.BANK_ANALYST, bank=BANK2, phone="9800000006"),
    }
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    from app.core.ratelimit import limiter
    was = limiter.enabled
    limiter.enabled = False
    try:
        yield w
    finally:
        limiter.enabled = was
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


def _hdr(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'dev-device-01')}"}


# ── list / scope ──────────────────────────────────────────────────────────────

def test_list_shows_only_the_admins_own_banks_staff(world):
    rows = bank_user_service.list_bank_users(world["db"], world["bank_admin"])
    emails = {r["email"] for r in rows}
    assert emails == {"bankadmin@example.in", "bankadmin2@example.in", "analyst@example.in", "techops@example.in"}
    assert "bank2analyst@example.in" not in emails and "bank2admin@example.in" not in emails
    # Every row carries the fields the page renders.
    analyst = next(r for r in rows if r["email"] == "analyst@example.in")
    assert analyst["role"] == "BANK_ANALYST" and analyst["status"] == "ACTIVE"
    assert analyst["mfa_enabled"] is False and "last_login_at" in analyst


def test_another_banks_user_is_invisible_the_uniform_404(world):
    for fn in (
        lambda: bank_user_service.change_role(world["db"], world["bank_admin"], world["bank2_analyst"].id,
                                              role=UserRole.BANK_TECHOPS, request=_request()),
        lambda: bank_user_service.deactivate_user(world["db"], world["bank_admin"], world["bank2_analyst"].id,
                                                 reason="x", request=_request()),
        lambda: bank_user_service.reactivate_user(world["db"], world["bank_admin"], world["bank2_analyst"].id,
                                                  request=_request()),
        # reset_login belongs in this loop most of all: it is the one that
        # TEXTS a credential link, so a missing scope here does not leak a row,
        # it sends another bank's user a way into an account.
        lambda: bank_user_service.reset_login(world["db"], world["bank_admin"], world["bank2_analyst"].id,
                                              request=_request()),
    ):
        with pytest.raises(AppException) as e:
            fn()
        assert e.value.status_code == 404


# ── invite ──────────────────────────────────────────────────────────────────

def test_invite_a_bank_user_writes_the_invite_in_the_admins_bank(world):
    out = bank_user_service.invite_bank_user(
        world["db"], world["bank_admin"], email="neha.kapoor@example.in", full_name="Neha Kapoor",
        phone="9811100001", role=UserRole.BANK_TECHOPS, request=_request())
    assert out["invite"]["role"] == "BANK_TECHOPS" and out["invite"]["bank_id"] == TEST_BANK_ID
    assert out["invite"]["agency_id"] is None
    assert len(_audits(world["db"], AuditAction.USER_INVITED)) == 1


def test_invite_refuses_a_non_bank_role(world):
    with pytest.raises(AppException) as e:
        bank_user_service.invite_bank_user(
            world["db"], world["bank_admin"], email="x@example.in", full_name="X", phone="9811100099",
            role=UserRole.AGENCY_MANAGER, request=_request())
    assert e.value.status_code == 422 and e.value.code == ErrorCode.VALIDATION_ERROR


# ── role change ───────────────────────────────────────────────────────────────

def test_role_change_moves_analyst_to_techops(world):
    out = bank_user_service.change_role(world["db"], world["bank_admin"], world["analyst"].id,
                                        role=UserRole.BANK_TECHOPS, request=_request())
    assert out["changed"] is True and out["role"] == "BANK_TECHOPS"
    world["db"].expire_all()
    assert world["db"].get(User, world["analyst"].id).role == UserRole.BANK_TECHOPS
    # v2_0032: its own action, not USER_DEACTIVATED with a details hint. The
    # bank Audit page counts by action, so a role flip filed as a deactivation
    # told a compliance screen something false.
    row = _audits(world["db"], AuditAction.USER_ROLE_CHANGED)[0]
    assert row.details["event"] == "USER_ROLE_CHANGED" and row.details["to_role"] == "BANK_TECHOPS"
    assert _audits(world["db"], AuditAction.USER_DEACTIVATED) == []      # nobody was deactivated


def test_role_change_to_the_same_role_is_a_no_op_not_an_error(world):
    out = bank_user_service.change_role(world["db"], world["bank_admin"], world["analyst"].id,
                                        role=UserRole.BANK_ANALYST, request=_request())
    assert out["changed"] is False
    assert _audits(world["db"], AuditAction.USER_ROLE_CHANGED) == []


def test_role_change_cannot_promote_to_bank_admin(world):
    with pytest.raises(AppException) as e:
        bank_user_service.change_role(world["db"], world["bank_admin"], world["analyst"].id,
                                      role=UserRole.BANK_ADMIN, request=_request())
    assert e.value.status_code == 422


# ── deactivate / reactivate ───────────────────────────────────────────────────

def test_deactivate_ends_sessions_and_reactivate_restores(world):
    db = world["db"]
    db.add(UserSession(id=test_id("sess:analyst"), user_id=world["analyst"].id, bank_id=TEST_BANK_ID,
                       device_id="dev-1", refresh_token_sha256="s" * 64, refresh_jti="j" * 32,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()

    out = bank_user_service.deactivate_user(db, world["bank_admin"], world["analyst"].id,
                                            reason="Left the team", request=_request())
    assert out["is_active"] is False and out["status"] == "DEACTIVATED"
    db.expire_all()
    assert world["analyst"].is_active is False and world["analyst"].deactivated_by == world["bank_admin"].id
    assert db.get(UserSession, test_id("sess:analyst")).revoked_at is not None
    derow = _audits(db, AuditAction.USER_DEACTIVATED)[0]
    assert derow.details["event"] == "USER_DEACTIVATED" and derow.details["reason"] == "Left the team"

    # Deactivating again is a conflict, not a second write.
    with pytest.raises(AppException) as e:
        bank_user_service.deactivate_user(db, world["bank_admin"], world["analyst"].id, reason="again",
                                          request=_request())
    assert e.value.status_code == 409

    back = bank_user_service.reactivate_user(db, world["bank_admin"], world["analyst"].id, request=_request())
    assert back["is_active"] is True
    rerow = _audits(db, AuditAction.USER_REACTIVATED)[0]                  # v2_0032, its own action
    assert rerow.details["event"] == "USER_REACTIVATED"
    assert len(_audits(db, AuditAction.USER_DEACTIVATED)) == 1            # still just the one
    db.expire_all()
    assert world["analyst"].is_active is True and world["analyst"].deactivated_at is None


def test_deactivate_needs_a_reason(world):
    with pytest.raises(AppException) as e:
        bank_user_service.deactivate_user(world["db"], world["bank_admin"], world["analyst"].id, reason="  ",
                                          request=_request())
    assert e.value.status_code == 422


# ── who may act on whom ────────────────────────────────────────────────────────

def test_an_admin_cannot_act_on_their_own_account(world):
    with pytest.raises(AppException) as e:
        bank_user_service.deactivate_user(world["db"], world["bank_admin"], world["bank_admin"].id,
                                          reason="x", request=_request())
    assert e.value.status_code == 409


def test_an_admin_cannot_act_on_a_fellow_bank_admin(world):
    with pytest.raises(AppException) as e:
        bank_user_service.deactivate_user(world["db"], world["bank_admin"], world["bank_admin2"].id,
                                          reason="x", request=_request())
    assert e.value.status_code == 403


# ── reset login ───────────────────────────────────────────────────────────────

def test_reset_login_texts_the_target_and_ends_their_sessions(world, monkeypatch):
    from app.core.config import settings
    from app.services import notification_service, password_service
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://fieldops.example.in")
    # The 10-minute admin cooldown lives in the shared OTP store (real Redis when
    # the dev stack is up), so it persists across runs for this deterministic id;
    # its behaviour is password_service's own to test — here it must not flake.
    monkeypatch.setattr(password_service, "claim_cooldown", lambda *a, **k: True)
    sent: list = []
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda to, body, **kw: sent.append((to, body)) or True))
    out = bank_user_service.reset_login(world["db"], world["bank_admin"], world["techops"].id, request=_request())
    assert out["sent"] is True
    to, body = sent[0]
    assert to == "+919800000004" and "/reset-password?token=" in body
    assert len(_audits(world["db"], AuditAction.PASSWORD_RESET_ISSUED)) == 1


def test_reset_login_refuses_a_fellow_admin(world):
    with pytest.raises(AppException) as e:
        bank_user_service.reset_login(world["db"], world["bank_admin"], world["bank_admin2"].id, request=_request())
    assert e.value.status_code == 404           # can_manage says no → the uniform not-found


# ── the capability gate (routes) ──────────────────────────────────────────────

def test_a_non_admin_bank_role_is_refused_at_the_gate(world):
    c = TestClient(app)
    for user in (world["analyst"], world["techops"]):
        r = c.get("/api/v1/bank/users", headers=_hdr(user))
        assert r.status_code == 403, r.text
    assert _audits(world["db"], AuditAction.ROLE_VIOLATION_ATTEMPT)


def test_the_bank_admin_reaches_the_routes(world):
    c = TestClient(app)
    r = c.get("/api/v1/bank/users", headers=_hdr(world["bank_admin"]))
    assert r.status_code == 200, r.text
    assert {row["email"] for row in r.json()} >= {"analyst@example.in", "techops@example.in"}

    d = c.post(f"/api/v1/bank/users/{world['analyst'].id}/deactivate", headers=_hdr(world["bank_admin"]),
               json={"reason": "offboarding"})
    assert d.status_code == 200 and d.json()["is_active"] is False


def test_the_invite_response_still_carries_the_link_fields(world, monkeypatch):
    """Rule 18's trap: response_model is an ALLOWLIST. A model that omits a
    field the service returns DROPS it, and every service-level test above
    still passes because they never go through the route. The LINK channel
    exists to hand back `token` and `path`; an invite response without them is
    an invite nobody can accept."""
    c = TestClient(app)
    r = c.post("/api/v1/bank/users/invite", headers=_hdr(world["bank_admin"]),
               json={"full_name": "New Analyst", "email": "new.analyst@example.in",
                     "phone": "9811111222", "role": "BANK_ANALYST", "channel": "LINK"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"] and body["path"], body
    assert body["invite"]["email"] == "new.analyst@example.in"


def test_the_reset_login_response_says_whether_it_sent(world, monkeypatch):
    """Same trap: admin_reset returns {sent, expires_at}, and a model that
    invented other field names would have returned nulls for both."""
    # Same stubs the service-level reset test uses: without PUBLIC_BASE_URL and
    # a stubbed sender the route 503s on delivery, which would make this test
    # about Twilio rather than about the response contract.
    from app.core.config import settings
    from app.services import notification_service, password_service
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://fieldops.example.in")
    monkeypatch.setattr(password_service, "claim_cooldown", lambda *a, **k: True)
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda *a, **k: True))
    c = TestClient(app)
    r = c.post(f"/api/v1/bank/users/{world['analyst'].id}/reset-login", headers=_hdr(world["bank_admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sent" in body and isinstance(body["sent"], bool)
    assert body["expires_at"]
