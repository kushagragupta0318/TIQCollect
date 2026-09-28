"""Every place a session is minted or extended, and its MFA / forced-change
decision (43's review HIGH + the coordinator's list, 2026-09-28).

| site                 | decision                                                    |
|----------------------|-------------------------------------------------------------|
| /auth/login          | code, then forced change, then required enrollment          |
| quick-login          | refused (403, link NOT burned) when any second step is owed |
| refresh              | refused (401, session kept) when a change / enrollment owed |
| invite accept        | new user: enrollment ticket under BANK_MFA_REQUIRED          |
| MFA ticket confirm   | a session only after a valid code (test_accounts_mfa)        |
| reset completion     | mints NO session                                             |
| manager bridge       | deleted (A10)                                                 |
| SERVICE login (A15)  | not built — ce applies the same gate                          |
"""
from __future__ import annotations

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import settings
from app.core.errors import ErrorCode
from app.core.security import create_quick_login_token, decode_token, hash_password
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserSession
from app.models.quick_login_token import UsedQuickLoginToken
from app.models.user import User, UserRole
from app.services import auth_service, invite_service, mfa_service, otp_service, password_service
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

PASSWORD = "Harbour-Lights-2026"


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


@pytest.fixture()
def world(monkeypatch):
    engine = make_engine()
    create_schema(engine)
    db = make_session_factory(engine, info={})()
    monkeypatch.setattr(settings, "TOTP_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "")
    monkeypatch.setattr(otp_service, "_otp_store", otp_service._InProcessOtpStore())
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://fieldops.example.in")
    sent = []
    from app.services import notification_service
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda to, body, **kw: sent.append(body) or True))

    def mk(key, role, agency=None, phone="9800000000"):
        u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
                 hashed_password=hash_password(PASSWORD), role=role, bank_id=TEST_BANK_ID, agency_id=agency)
        db.add(u)
        return u
    w = {"db": db, "sent": sent,
         "manager": mk("manager", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000006"),
         "analyst": mk("analyst", UserRole.BANK_ANALYST, phone="9800000003"),
         "bank_admin": mk("bankadmin", UserRole.BANK_ADMIN, phone="9800000002")}
    db.commit()
    yield w
    db.close()


def _enroll(db, user):
    out = mfa_service.start_enrollment(db, user)
    mfa_service.confirm_enrollment(db, user, pyotp.TOTP(out["secret"]).now())


# ── quick-login ─────────────────────────────────────────────────────────────

def _quick(w, user):
    token = create_quick_login_token(user.id, "AGY-TIQ-001")
    return token, decode_token(token)["jti"]


def test_quick_login_still_works_for_an_account_that_owes_nothing(world):
    token, _ = _quick(world, world["manager"])
    assert "access_token" in auth_service.quick_login(world["db"], token, _request())


@pytest.mark.parametrize("owes", ["totp_enabled", "must_change_password", "bank_mfa_required"])
def test_quick_login_is_refused_when_a_second_step_is_owed_and_the_link_is_not_burned(world, monkeypatch, owes):
    user = world["analyst"]
    if owes == "totp_enabled":
        _enroll(world["db"], user)
    elif owes == "must_change_password":
        user.must_change_password = True
        world["db"].commit()
    else:
        monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    token, jti = _quick(world, user)
    before = world["db"].query(UserSession).filter(UserSession.user_id == user.id).count()
    with pytest.raises(HTTPException) as e:
        auth_service.quick_login(world["db"], token, _request())
    assert e.value.status_code == 403 and e.value.detail == auth_service.SIGN_IN_REQUIRED_MESSAGE
    assert e.value.code == ErrorCode.SIGN_IN_REQUIRED
    assert world["db"].get(UsedQuickLoginToken, jti) is None
    assert world["db"].query(UserSession).filter(UserSession.user_id == user.id).count() == before
    assert world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.LOGIN_FAILED).count() >= 1


# ── refresh ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("owes", ["must_change_password", "bank_mfa_required"])
def test_a_refresh_cannot_outlive_a_step_the_account_now_owes(world, monkeypatch, owes):
    user = world["analyst"]
    tokens = auth_service.login(world["db"], user.email, PASSWORD, "laptop-device-1", _request())
    if owes == "must_change_password":
        user.must_change_password = True
        world["db"].commit()
    else:
        monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    with pytest.raises(HTTPException) as e:
        auth_service.refresh_tokens(world["db"], tokens["refresh_token"], _request())
    assert e.value.status_code == 401 and e.value.detail == auth_service.SIGN_IN_REQUIRED_MESSAGE
    assert e.value.code == ErrorCode.SIGN_IN_REQUIRED          # the same refusal as quick-login
    sid = decode_token(tokens["access_token"])["sid"]
    assert world["db"].get(UserSession, sid).revoked_at is None          # kept, just not extended


def test_a_refresh_still_works_for_an_account_that_owes_nothing(world):
    tokens = auth_service.login(world["db"], world["manager"].email, PASSWORD, "laptop-device-1", _request())
    assert auth_service.refresh_tokens(world["db"], tokens["refresh_token"], _request())["access_token"]


def test_enrolling_ends_the_sessions_opened_without_the_factor(world):
    """A refresh token stolen before the victim enrolled must stop working
    when they do; the enrolling session stays."""
    user = world["analyst"]
    stolen = auth_service.login(world["db"], user.email, PASSWORD, "attacker-device-9", _request())
    mine = auth_service.login(world["db"], user.email, PASSWORD, "laptop-device-1", _request())
    out = mfa_service.start_enrollment(world["db"], user)
    mfa_service.confirm_enrollment(world["db"], user, pyotp.TOTP(out["secret"]).now(),
                                   keep_sid=decode_token(mine["access_token"])["sid"])
    with pytest.raises(HTTPException):
        auth_service.refresh_tokens(world["db"], stolen["refresh_token"], _request())
    assert auth_service.refresh_tokens(world["db"], mine["refresh_token"], _request())["access_token"]


# ── invite accept ───────────────────────────────────────────────────────────

def test_an_invited_bank_user_under_required_mfa_gets_a_ticket_not_a_session(world, monkeypatch):
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    inv = invite_service.create_invite(world["db"], world["bank_admin"], email="ira.bose@example.in",
                                       role=UserRole.BANK_ANALYST, full_name="Ira Bose", phone="9811100011")
    res = invite_service.accept_invite(world["db"], inv["token"], PASSWORD, "laptop-device-1", _request())
    assert res["next"] == "ENROLL_MFA" and "access_token" not in res
    new = world["db"].query(User).filter(User.email == "ira.bose@example.in").one()
    assert world["db"].query(UserSession).filter(UserSession.user_id == new.id).count() == 0


# ── reset completion ────────────────────────────────────────────────────────

def test_completing_a_reset_mints_no_session(world):
    password_service.admin_reset(world["db"], world["bank_admin"], world["analyst"].id)
    import re
    token = re.search(r"token=([\w-]+)", world["sent"][-1]).group(1)
    password_service.reset_with_token(world["db"], token, "Monsoon-Terrace-77")
    assert world["db"].query(UserSession).filter(UserSession.user_id == world["analyst"].id,
                                                 UserSession.revoked_at.is_(None)).count() == 0


# ── manager bridge ──────────────────────────────────────────────────────────

def test_no_backend_route_mints_a_session_for_a_visitor_without_credentials(world):
    """The /manager-bridge SSO lived in the frontend (A10 removed it); no API
    route may take no secret and return tokens."""
    minting = {"/api/v1/auth/login", "/api/v1/auth/quick-login", "/api/v1/auth/refresh",
               "/api/v1/auth/invites/accept", "/api/v1/auth/mfa/enroll/confirm"}
    paths = {r.path for r in app.routes if getattr(r, "methods", None) and "POST" in r.methods}
    assert minting <= paths
    assert not any("bridge" in p for p in paths)
