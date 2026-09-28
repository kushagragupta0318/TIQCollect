"""A07 — password change, admin reset, forced change on first login, SMS
self-reset (P1, d4, 2026-09-28). Services driven directly; HTTP only where
the route itself is the subject. No default tenant in the session factory."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.errors import AppException, ErrorCode
from app.core.security import decode_token, hash_password, token_sha256, verify_password
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import PasswordResetToken, UserSession
from app.models.user import User, UserRole
from app.services import auth_service, otp_service, password_service
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

PASSWORD = "Harbour-Lights-2026"
NEW = "Monsoon-Terrace-77"


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


def _user(db, key, role, *, bank=TEST_BANK_ID, agency=None, phone, **kw):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
             hashed_password=hash_password(PASSWORD), role=role, bank_id=bank, agency_id=agency, **kw)
    db.add(u)
    return u


@pytest.fixture()
def world(monkeypatch):
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    monkeypatch.setattr(otp_service, "_otp_store", otp_service._InProcessOtpStore())
    sent = []
    from app.services import notification_service
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda to, body, **kw: sent.append((to, body, kw)) or True))
    w = {
        "db": db, "sent": sent,
        "bank_admin": _user(db, "bankadmin", UserRole.BANK_ADMIN, phone="9800000002"),
        "analyst": _user(db, "analyst", UserRole.BANK_ANALYST, phone="9800000003"),
        "agency_admin": _user(db, "agencyadmin", UserRole.AGENCY_ADMIN, agency=TEST_AGENCY_ID, phone="9800000005"),
        "manager": _user(db, "manager", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000006"),
        "fresh": _user(db, "fresh", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000007",
                       must_change_password=True),
    }
    db.commit()
    yield w
    db.close()


def _login(w, user, password=PASSWORD, device="laptop-device-1"):
    return auth_service.login(w["db"], user.email, password, device, _request())


def _audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


def _live(db, user):
    return db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).count()


# ── change ──────────────────────────────────────────────────────────────────

def test_changing_the_password_signs_out_every_other_device_only(world):
    here = _login(world, world["manager"], device="laptop-device-1")
    _login(world, world["manager"], device="tablet-device-2")
    sid = decode_token(here["access_token"])["sid"]
    password_service.change_password(world["db"], world["manager"], PASSWORD, NEW, sid=sid, request=_request())
    world["db"].expire_all()
    live = world["db"].query(UserSession).filter(UserSession.user_id == world["manager"].id,
                                                 UserSession.revoked_at.is_(None)).all()
    assert [s.id for s in live] == [sid]
    assert verify_password(NEW, world["manager"].hashed_password)
    revoked = _audits(world["db"], AuditAction.SESSION_REVOKED)
    assert len(revoked) == 1 and revoked[0].details == {"reason": "PASSWORD_CHANGED", "count": 1, "kept_sid": sid}
    assert _audits(world["db"], AuditAction.PASSWORD_CHANGED)[-1].success is True


def test_a_wrong_current_password_changes_nothing_and_is_recorded(world):
    before = world["manager"].hashed_password
    with pytest.raises(AppException) as e:
        password_service.change_password(world["db"], world["manager"], "not-my-password-1", NEW)
    assert e.value.code == ErrorCode.PASSWORD_INCORRECT
    assert world["manager"].hashed_password == before
    assert _audits(world["db"], AuditAction.PASSWORD_CHANGED)[-1].success is False


@pytest.mark.parametrize("new", ["short1", "nodigitshere-at-all", "Password2026!", PASSWORD])
def test_the_new_password_must_pass_the_rule(world, new):
    with pytest.raises(AppException) as e:
        password_service.change_password(world["db"], world["manager"], PASSWORD, new)
    assert e.value.code == ErrorCode.PASSWORD_POLICY


# ── forced change on first login ────────────────────────────────────────────

def test_a_flagged_account_gets_a_ticket_not_a_session(world):
    res = _login(world, world["fresh"])
    assert res["next"] == "CHANGE_PASSWORD" and "access_token" not in res
    assert _live(world["db"], world["fresh"]) == 0
    password_service.reset_with_token(world["db"], res["reset_token"], NEW)
    world["db"].expire_all()
    assert world["fresh"].must_change_password is False
    assert "access_token" in _login(world, world["fresh"], NEW)


def test_a_wrong_password_on_a_flagged_account_gets_nothing(world):
    with pytest.raises(HTTPException) as e:
        _login(world, world["fresh"], "Wrong-Password-00")
    assert e.value.status_code == 401
    assert world["db"].query(PasswordResetToken).count() == 0


def test_the_first_login_ticket_is_single_use_and_short_lived(world):
    res = _login(world, world["fresh"])
    row = world["db"].query(PasswordResetToken).filter(
        PasswordResetToken.token_sha256 == token_sha256(res["reset_token"])).one()
    assert row.kind == "FIRST_LOGIN"
    assert password_service.utc(row.expires_at) - password_service.utc(row.created_at) <= timedelta(minutes=15, seconds=5)
    password_service.reset_with_token(world["db"], res["reset_token"], NEW)
    with pytest.raises(AppException) as e:
        password_service.reset_with_token(world["db"], res["reset_token"], "Another-Terrace-88")
    assert e.value.code == ErrorCode.RESET_INVALID


def test_only_the_newest_ticket_works(world):
    first = _login(world, world["fresh"])["reset_token"]
    second = _login(world, world["fresh"])["reset_token"]
    with pytest.raises(AppException):
        password_service.reset_with_token(world["db"], first, NEW)
    password_service.reset_with_token(world["db"], second, NEW)


# ── admin reset ─────────────────────────────────────────────────────────────

def test_an_admin_reset_is_a_link_not_a_password_and_ends_every_session(world):
    _login(world, world["manager"])
    out = password_service.admin_reset(world["db"], world["agency_admin"], world["manager"].id, request=_request())
    assert out["path"] == f"/reset-password?token={out['token']}"
    assert verify_password(PASSWORD, world["manager"].hashed_password)       # untouched until used
    assert _live(world["db"], world["manager"]) == 0
    issued = _audits(world["db"], AuditAction.PASSWORD_RESET_ISSUED)
    assert issued[-1].user_id == world["agency_admin"].id and issued[-1].entity_id == world["manager"].id
    password_service.reset_with_token(world["db"], out["token"], NEW)
    assert _audits(world["db"], AuditAction.PASSWORD_RESET)[-1].details["kind"] == "ADMIN_RESET"


@pytest.mark.parametrize("admin,target,ok", [
    ("agency_admin", "manager", True),
    ("bank_admin", "analyst", True),
    ("bank_admin", "agency_admin", True),
    ("bank_admin", "manager", False),          # the agency admin's people
    ("agency_admin", "analyst", False),
    ("manager", "fresh", False),
    ("bank_admin", "bank_admin", False),       # never yourself
])
def test_who_may_reset_whom(world, admin, target, ok):
    if ok:
        assert password_service.admin_reset(world["db"], world[admin], world[target].id)["token"]
    else:
        with pytest.raises(AppException) as e:
            password_service.admin_reset(world["db"], world[admin], world[target].id)
        assert e.value.status_code == 404


# ── self-service by SMS code ────────────────────────────────────────────────

def _code(world) -> str:
    return re.search(r"\b(\d{6})\b", world["sent"][-1][1]).group(1)


def test_the_whole_self_service_reset(world):
    out = password_service.forgot(world["db"], "Manager@Example.in", request=_request())
    assert out["message"] == password_service.FORGOT_MESSAGE
    to, body, kw = world["sent"][-1]
    assert to == "+919800000006" and kw == {"db": world["db"], "user_id": world["manager"].id}
    token = password_service.verify_code(world["db"], out["request_id"], _code(world))["reset_token"]
    password_service.reset_with_token(world["db"], token, NEW)
    world["db"].expire_all()
    assert verify_password(NEW, world["manager"].hashed_password)


def test_an_unknown_account_gets_the_same_answer_and_nothing_is_sent(world):
    known = password_service.forgot(world["db"], "manager@example.in")
    unknown = password_service.forgot(world["db"], "nobody.here@example.in")
    assert set(known) == set(unknown) and known["message"] == unknown["message"]
    assert len(known["request_id"]) == len(unknown["request_id"])
    assert len(world["sent"]) == 1
    with pytest.raises(AppException) as e:
        password_service.verify_code(world["db"], unknown["request_id"], "123456")
    assert e.value.code == ErrorCode.RESET_INVALID


def test_a_phone_number_finds_the_account_too(world):
    password_service.forgot(world["db"], "+91 98000 00006")
    assert world["sent"][-1][2]["user_id"] == world["manager"].id


def test_three_requests_an_hour_then_silence(world):
    for _ in range(5):
        password_service.forgot(world["db"], "manager@example.in")
    assert len(world["sent"]) == password_service.RESET_REQUESTS_PER_HOUR


def test_five_wrong_codes_burn_the_request(world):
    out = password_service.forgot(world["db"], "manager@example.in")
    right = _code(world)
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(password_service.RESET_OTP_MAX_ATTEMPTS):
        with pytest.raises(AppException):
            password_service.verify_code(world["db"], out["request_id"], wrong)
    with pytest.raises(AppException):
        password_service.verify_code(world["db"], out["request_id"], right)


def test_a_code_belongs_to_its_request(world):
    a = password_service.forgot(world["db"], "manager@example.in")
    code_a = _code(world)
    b = password_service.forgot(world["db"], "analyst@example.in")
    with pytest.raises(AppException):
        password_service.verify_code(world["db"], b["request_id"], code_a)
    assert password_service.verify_code(world["db"], a["request_id"], code_a)["reset_token"]


def test_a_self_service_token_without_a_verified_code_is_refused(world):
    row_token = password_service._issue(world["db"], world["manager"], "SELF_SERVICE", timedelta(minutes=5))
    world["db"].commit()
    with pytest.raises(AppException) as e:
        password_service.reset_with_token(world["db"], row_token, NEW)
    assert e.value.code == ErrorCode.RESET_INVALID


def test_the_code_is_never_in_the_audit_trail(world):
    password_service.forgot(world["db"], "manager@example.in")
    code = _code(world)
    rows = world["db"].query(AuditLog).all()
    assert rows and not any(code in repr(r.details) for r in rows)


def test_a_reset_clears_the_lockout_but_not_mfa(world):
    world["manager"].failed_login_attempts = 5
    world["manager"].locked_until = password_service.now() + timedelta(minutes=10)
    world["manager"].totp_enabled = True
    world["db"].commit()
    out = password_service.forgot(world["db"], "manager@example.in")
    token = password_service.verify_code(world["db"], out["request_id"], _code(world))["reset_token"]
    password_service.reset_with_token(world["db"], token, NEW)
    world["db"].expire_all()
    assert world["manager"].failed_login_attempts == 0 and world["manager"].locked_until is None
    assert world["manager"].totp_enabled is True
