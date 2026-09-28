"""A08 — TOTP for bank roles (P1, d4, 2026-09-28).

The coordinator's two rulings are the tests that matter most: the secret is
never stored in plaintext (Fernet under TOTP_ENC_KEY, enrollment refused
without a key) and a code cannot be replayed (the step is claimed by
compare-and-swap). The clock is pinned by patching mfa_service.time."""
from __future__ import annotations

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.security import hash_password
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserSession
from app.models.user import User, UserRole
from app.services import auth_service, mfa_service, otp_service
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

PASSWORD = "Harbour-Lights-2026"
T0 = 1_790_000_010          # a fixed instant, 10 s into a 30 s step


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


class _Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


@pytest.fixture()
def world(monkeypatch):
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    monkeypatch.setattr(settings, "TOTP_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "")
    monkeypatch.setattr(otp_service, "_otp_store", otp_service._InProcessOtpStore())
    clock = _Clock(T0)
    monkeypatch.setattr(mfa_service, "time", clock)

    def mk(key, role, agency=None, phone="9800000000"):
        u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
                 hashed_password=hash_password(PASSWORD), role=role, bank_id=TEST_BANK_ID, agency_id=agency)
        db.add(u)
        return u
    w = {"db": db, "clock": clock,
         "analyst": mk("analyst", UserRole.BANK_ANALYST, phone="9800000003"),
         "techops": mk("techops", UserRole.BANK_TECHOPS, phone="9800000004"),
         "bank_admin": mk("bankadmin", UserRole.BANK_ADMIN, phone="9800000002"),
         "bank_admin2": mk("bankadmin2", UserRole.BANK_ADMIN, phone="9800000008"),
         "manager": mk("manager", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000006")}
    db.commit()
    yield w
    db.close()


def _code(secret, t, offset=0):
    return pyotp.TOTP(secret).at(t + offset * 30)


def _enroll(w, user):
    out = mfa_service.start_enrollment(w["db"], user)
    mfa_service.confirm_enrollment(w["db"], user, _code(out["secret"], w["clock"].t))
    w["clock"].t += 30                   # the next code is a later step
    return out["secret"]


def _login(w, user, code=None):
    return auth_service.login(w["db"], user.email, PASSWORD, "laptop-device-1", _request(), totp_code=code)


# ── the secret ──────────────────────────────────────────────────────────────

def test_the_secret_is_stored_encrypted_and_shown_once(world):
    out = mfa_service.start_enrollment(world["db"], world["analyst"])
    stored = world["analyst"].totp_secret
    assert out["secret"] not in stored and len(stored) > 64
    assert Fernet(settings.TOTP_ENC_KEY.encode()).decrypt(stored.encode()).decode() == out["secret"]
    assert out["otpauth_uri"].startswith("otpauth://totp/") and "issuer=TIQCollect" in out["otpauth_uri"]
    assert world["analyst"].totp_enabled is False               # pending until confirmed


@pytest.mark.parametrize("key", ["", "${TOTP_ENC_KEY}", "not-a-fernet-key"])
def test_no_key_means_no_enrollment(world, monkeypatch, key):
    monkeypatch.setattr(settings, "TOTP_ENC_KEY", key)
    with pytest.raises(AppException) as e:
        mfa_service.start_enrollment(world["db"], world["analyst"])
    assert e.value.code == ErrorCode.MFA_NOT_CONFIGURED
    assert world["analyst"].totp_secret is None


def test_only_bank_roles_enroll(world):
    with pytest.raises(AppException) as e:
        mfa_service.start_enrollment(world["db"], world["manager"])
    assert e.value.code == ErrorCode.MFA_NOT_ALLOWED


def test_a_rotated_key_refuses_codes_rather_than_waving_them_through(world, monkeypatch):
    secret = _enroll(world, world["analyst"])
    monkeypatch.setattr(settings, "TOTP_ENC_KEY", Fernet.generate_key().decode())
    with pytest.raises(AppException) as e:
        _login(world, world["analyst"], _code(secret, world["clock"].t))
    assert e.value.code == ErrorCode.MFA_INVALID


# ── confirm ─────────────────────────────────────────────────────────────────

def test_a_wrong_confirmation_code_leaves_it_off(world):
    mfa_service.start_enrollment(world["db"], world["analyst"])
    with pytest.raises(AppException) as e:
        mfa_service.confirm_enrollment(world["db"], world["analyst"], "000000")
    assert e.value.code == ErrorCode.MFA_INVALID and world["analyst"].totp_enabled is False


def test_enrolling_is_audited(world):
    _enroll(world, world["analyst"])
    assert world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.MFA_ENABLED).count() == 1


# ── login ───────────────────────────────────────────────────────────────────

def test_an_enrolled_user_needs_the_code(world):
    secret = _enroll(world, world["analyst"])
    with pytest.raises(AppException) as e:
        _login(world, world["analyst"])
    assert e.value.status_code == 401 and e.value.code == ErrorCode.MFA_REQUIRED
    assert "access_token" in _login(world, world["analyst"], _code(secret, world["clock"].t))


def test_a_code_cannot_be_used_twice(world):
    secret = _enroll(world, world["analyst"])
    code = _code(secret, world["clock"].t)
    assert "access_token" in _login(world, world["analyst"], code)
    with pytest.raises(AppException) as e:
        _login(world, world["analyst"], code)                    # replay, same step
    assert e.value.code == ErrorCode.MFA_INVALID


def test_an_older_step_cannot_follow_a_newer_one(world):
    """Within the ±1 window, the code of the step BEFORE one already used
    is refused: the claim is strictly increasing."""
    secret = _enroll(world, world["analyst"])
    t = world["clock"].t
    assert "access_token" in _login(world, world["analyst"], _code(secret, t, +1))
    with pytest.raises(AppException):
        _login(world, world["analyst"], _code(secret, t))


def test_a_code_used_from_another_device_cannot_be_used_here(world):
    """Another session (another device, another worker) claims the step
    first; this session's user object still holds the OLD totp_last_step, so
    only the database-side compare-and-swap can refuse the second use."""
    secret = _enroll(world, world["analyst"])
    code = _code(secret, world["clock"].t)
    db2 = world["db"].__class__(bind=world["db"].get_bind(), info={})
    other = db2.get(User, world["analyst"].id)
    assert "access_token" in auth_service.login(db2, other.email, PASSWORD, "tablet-device-2", _request(),
                                                totp_code=code)
    db2.close()
    with pytest.raises(AppException):
        _login(world, world["analyst"], code)
    assert world["db"].query(UserSession).filter(UserSession.user_id == world["analyst"].id).count() == 1


def test_codes_outside_the_window_are_refused(world):
    secret = _enroll(world, world["analyst"])
    for off in (-3, -2, +2, +3):
        with pytest.raises(AppException):
            _login(world, world["analyst"], _code(secret, world["clock"].t, off))


def test_wrong_codes_count_toward_the_lockout(world):
    _enroll(world, world["analyst"])
    for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
        with pytest.raises(AppException):
            _login(world, world["analyst"], "000000")
    world["db"].expire_all()
    assert world["analyst"].locked_until is not None
    assert world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.MFA_FAILED).count() == 5
    with pytest.raises(HTTPException) as e:
        _login(world, world["analyst"], "000000")
    assert e.value.status_code == 429


def test_a_wrong_password_never_reaches_the_code_check(world):
    _enroll(world, world["analyst"])
    with pytest.raises(HTTPException) as e:
        auth_service.login(world["db"], world["analyst"].email, "Wrong-Password-00", "laptop-device-1",
                           _request(), totp_code="123456")
    assert e.value.status_code == 401 and not isinstance(e.value, AppException)


def test_mfa_comes_before_the_forced_password_change(world):
    """A stolen password alone must not reach the change-password ticket."""
    secret = _enroll(world, world["analyst"])
    world["analyst"].must_change_password = True
    world["db"].commit()
    with pytest.raises(AppException) as e:
        _login(world, world["analyst"])
    assert e.value.code == ErrorCode.MFA_REQUIRED
    assert _login(world, world["analyst"], _code(secret, world["clock"].t))["next"] == "CHANGE_PASSWORD"


# ── BANK_MFA_REQUIRED ───────────────────────────────────────────────────────

@pytest.mark.parametrize("flag,required", [("true", True), ("TRUE", True), ("", False), ("false", False),
                                           ("1", False), ("${BANK_MFA_REQUIRED}", False)])
def test_the_requirement_is_on_only_for_an_explicit_true(world, monkeypatch, flag, required):
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", flag)
    res = _login(world, world["analyst"])
    assert (res.get("next") == "ENROLL_MFA") is required
    assert ("access_token" in res) is (not required)


def test_required_mfa_gives_an_enrollment_ticket_not_a_session(world, monkeypatch):
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    res = _login(world, world["analyst"])
    assert world["db"].query(UserSession).count() == 0
    start = mfa_service.ticket_start(world["db"], res["enrollment_token"])
    done = mfa_service.ticket_confirm(world["db"], res["enrollment_token"], _code(start["secret"], world["clock"].t),
                                      "laptop-device-1", _request())
    assert done["access_token"] and world["analyst"].totp_enabled
    with pytest.raises(AppException):                                  # the ticket is spent
        mfa_service.ticket_start(world["db"], res["enrollment_token"])


def test_required_mfa_does_not_touch_agency_users(world, monkeypatch):
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    assert "access_token" in _login(world, world["manager"])


def test_required_but_unconfigured_refuses_bank_logins(world, monkeypatch):
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    monkeypatch.setattr(settings, "TOTP_ENC_KEY", "")
    with pytest.raises(AppException) as e:
        _login(world, world["analyst"])
    assert e.value.code == ErrorCode.MFA_NOT_CONFIGURED


# ── disable / admin reset ───────────────────────────────────────────────────

def test_disabling_needs_a_fresh_code(world):
    secret = _enroll(world, world["analyst"])
    with pytest.raises(AppException):
        mfa_service.disable(world["db"], world["analyst"], "000000")
    mfa_service.disable(world["db"], world["analyst"], _code(secret, world["clock"].t))
    assert world["analyst"].totp_enabled is False and world["analyst"].totp_secret is None


def test_a_required_factor_cannot_be_switched_off(world, monkeypatch):
    secret = _enroll(world, world["analyst"])
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    with pytest.raises(AppException) as e:
        mfa_service.disable(world["db"], world["analyst"], _code(secret, world["clock"].t))
    assert e.value.code == ErrorCode.MFA_NOT_ALLOWED


def test_a_bank_admin_resets_a_lost_phone_and_signs_them_out(world):
    secret = _enroll(world, world["analyst"])
    _login(world, world["analyst"], _code(secret, world["clock"].t))
    mfa_service.admin_reset(world["db"], world["bank_admin"], world["analyst"], request=_request())
    world["db"].expire_all()
    assert world["analyst"].totp_enabled is False
    assert world["db"].query(UserSession).filter(UserSession.revoked_at.is_(None)).count() == 0
    row = world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.MFA_DISABLED).one()
    assert row.user_id == world["bank_admin"].id and row.details == {"by": "admin"}


@pytest.mark.parametrize("admin,target", [("analyst", "techops"), ("bank_admin", "manager"),
                                          ("bank_admin", "bank_admin"),
                                          ("bank_admin", "bank_admin2")])   # audit HIGH: not a peer admin
def test_only_a_bank_admin_resets_another_bank_users_mfa(world, admin, target):
    with pytest.raises(AppException) as e:
        mfa_service.admin_reset(world["db"], world[admin], world[target])
    assert e.value.status_code == 404



def test_the_platform_admin_resets_a_bank_admins_factor(world):
    platform = User(id=test_id("u:platform"), email="platform@example.in", phone="9800000001",
                    full_name="Platform", hashed_password=hash_password(PASSWORD), role=UserRole.PLATFORM_ADMIN)
    world["db"].add(platform)
    world["db"].commit()
    _enroll(world, world["bank_admin"])
    mfa_service.admin_reset(world["db"], platform, world["bank_admin"])
    assert world["bank_admin"].totp_enabled is False


def test_finishing_enrollment_from_a_ticket_is_a_login(world, monkeypatch):
    """The ticket path goes through auth_service.complete_login: a LOGIN row
    naming the route, like /auth/login writes."""
    monkeypatch.setattr(settings, "BANK_MFA_REQUIRED", "true")
    res = _login(world, world["analyst"])
    start = mfa_service.ticket_start(world["db"], res["enrollment_token"])
    mfa_service.ticket_confirm(world["db"], res["enrollment_token"], _code(start["secret"], world["clock"].t),
                               "laptop-device-1", _request())
    row = world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.LOGIN).one()
    assert row.user_id == world["analyst"].id and row.details == {"method": "mfa_enrollment"}
