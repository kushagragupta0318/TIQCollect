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
    monkeypatch.setattr(password_service, "_dispatch", lambda fn: fn())        # the SMS thread, run inline
    from app.core.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://fieldops.example.in")
    sent = []
    from app.services import notification_service
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda to, body, **kw: sent.append((to, body, kw)) or True))
    w = {
        "db": db, "sent": sent,
        "platform": _user(db, "platform", UserRole.PLATFORM_ADMIN, bank=None, phone="9800000001"),
        "bank_admin": _user(db, "bankadmin", UserRole.BANK_ADMIN, phone="9800000002"),
        "bank_admin2": _user(db, "bankadmin2", UserRole.BANK_ADMIN, phone="9800000008"),
        "analyst": _user(db, "analyst", UserRole.BANK_ANALYST, phone="9800000003"),
        "agency_admin": _user(db, "agencyadmin", UserRole.AGENCY_ADMIN, agency=TEST_AGENCY_ID, phone="9800000005"),
        "manager": _user(db, "manager", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000006"),
        "fresh": _user(db, "fresh", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000007",
                       must_change_password=True),
        "manager2": _user(db, "manager2", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000009"),
        "own_agent": _user(db, "ownagent", UserRole.FIELD_AGENT, agency=TEST_AGENCY_ID, phone="9800000010"),
        "other_agent": _user(db, "otheragent", UserRole.FIELD_AGENT, agency=TEST_AGENCY_ID, phone="9800000011"),
    }
    db.flush()
    from app.models.agent import Agent
    for key, boss in (("own_agent", "manager"), ("other_agent", "manager2")):
        db.add(Agent(id=test_id(f"agent:{key}"), user_id=w[key].id, employee_code=f"AFS-{key[:5].upper()}",
                     id_card_number=f"ID-{key}", bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID,
                     base_latitude=28.45, base_longitude=77.07, territory="Sector 44, Gurugram",
                     manager_user_id=w[boss].id))
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

def _texted_token(world) -> str:
    return re.search(r"/reset-password\?token=([\w-]+)", world["sent"][-1][1]).group(1)


def test_an_admin_reset_texts_the_link_to_the_target_and_ends_every_session(world):
    _login(world, world["manager"])
    out = password_service.admin_reset(world["db"], world["agency_admin"], world["manager"].id, request=_request())
    assert set(out) == {"sent", "expires_at"} and out["sent"] is True       # the admin never holds the link
    to, body, kw = world["sent"][-1]
    assert to == "+919800000006" and kw["user_id"] == world["manager"].id
    assert "https://fieldops.example.in/reset-password?token=" in body
    assert verify_password(PASSWORD, world["manager"].hashed_password)       # untouched until used
    assert _live(world["db"], world["manager"]) == 0
    issued = _audits(world["db"], AuditAction.PASSWORD_RESET_ISSUED)
    assert issued[-1].user_id == world["agency_admin"].id and issued[-1].entity_id == world["manager"].id
    password_service.reset_with_token(world["db"], _texted_token(world), NEW)
    assert _audits(world["db"], AuditAction.PASSWORD_RESET)[-1].details["kind"] == "ADMIN_RESET"


@pytest.mark.parametrize("base", ["", "${PUBLIC_BASE_URL}"])
def test_an_admin_reset_without_a_public_url_changes_nothing(world, monkeypatch, base):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", base)
    _login(world, world["manager"])
    with pytest.raises(AppException) as e:
        password_service.admin_reset(world["db"], world["agency_admin"], world["manager"].id)
    assert e.value.code == ErrorCode.CHANNEL_UNAVAILABLE
    assert _live(world["db"], world["manager"]) == 1
    assert world["db"].query(PasswordResetToken).count() == 0


@pytest.mark.parametrize("admin,target,ok", [
    ("agency_admin", "manager", True),
    ("bank_admin", "analyst", True),
    ("bank_admin", "agency_admin", True),
    ("bank_admin", "manager", False),          # the agency admin's people
    ("agency_admin", "analyst", False),
    ("manager", "fresh", False),
    ("bank_admin", "bank_admin", False),       # never yourself
    ("bank_admin", "bank_admin2", False),      # never a fellow bank admin (audit HIGH): a takeover of a peer
    ("platform", "bank_admin", True),          # a bank admin's reset needs the platform admin
    ("manager", "own_agent", True),            # G02: an agent's OWN manager
    ("manager2", "own_agent", False),          # another manager in the same agency
    ("manager", "fresh", False),               # a fellow manager
    ("manager", "agency_admin", False),        # their admin
    ("manager", "other_agent", False),         # an agent of the same agency, managed by someone else
])
def test_who_may_reset_whom(world, admin, target, ok):
    if ok:
        assert password_service.admin_reset(world["db"], world[admin], world[target].id)["sent"] is True
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
    assert to == "+919800000006" and kw["user_id"] == world["manager"].id
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



def test_wrong_current_passwords_count_toward_the_lockout(world):
    for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
        with pytest.raises(AppException):
            password_service.change_password(world["db"], world["manager"], "not-my-password-1", NEW)
    assert world["manager"].locked_until is not None
    with pytest.raises(AppException) as e:                     # even the right one, while locked
        password_service.change_password(world["db"], world["manager"], PASSWORD, NEW)
    assert e.value.status_code == 429
    with pytest.raises(HTTPException) as e:
        _login(world, world["manager"])
    assert e.value.status_code == 429


def test_the_reset_sms_leaves_the_request_path(world, monkeypatch):
    """forgot() hands the send to _dispatch and returns: an unknown account
    and a known one cost the request the same (no SMS on either path)."""
    queued = []
    monkeypatch.setattr(password_service, "_dispatch", queued.append)
    out = password_service.forgot(world["db"], "manager@example.in")
    assert out["message"] == password_service.FORGOT_MESSAGE
    assert world["sent"] == [] and len(queued) == 1
    queued[0]()                                               # the thread's work, run now
    assert len(world["sent"]) == 1


def test_a_changed_password_and_its_audit_row_land_together(world, monkeypatch):
    """Staged, not written after: when the business commit fails, no
    PASSWORD_CHANGED row survives either."""
    from sqlalchemy.orm import Session as _S
    real_commit = _S.commit
    calls = {"n": 0}

    def failing_commit(self):
        calls["n"] += 1
        raise RuntimeError("database went away")
    monkeypatch.setattr(_S, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        password_service.change_password(world["db"], world["manager"], PASSWORD, NEW)
    monkeypatch.setattr(_S, "commit", real_commit)
    world["db"].rollback()
    assert [r for r in _audits(world["db"], AuditAction.PASSWORD_CHANGED) if r.success] == []
    world["db"].expire_all()
    assert verify_password(PASSWORD, world["manager"].hashed_password)


def test_a_crash_after_the_change_cannot_lose_its_audit_row(world, monkeypatch):
    """The other half, and the one that tells staging from writing after the
    commit: if anything writing audit rows AFTER the business commit dies (the
    process is killed), a staged row is already in. Written-after, it is lost."""
    def dead(*a, **k):
        raise RuntimeError("process killed after the business commit")
    monkeypatch.setattr(password_service, "write_audit", dead)
    password_service.change_password(world["db"], world["manager"], PASSWORD, NEW)
    rows = [r for r in _audits(world["db"], AuditAction.PASSWORD_CHANGED) if r.success]
    assert len(rows) == 1 and rows[0].entity_id == world["manager"].id



def test_a_manager_resets_their_own_agents_password_through_the_route(world):
    """G02's "reset login": the route admits AGENCY_MANAGER; the service
    decides it is their agent; the link goes to the agent's phone."""
    from fastapi.testclient import TestClient
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app
    from app.core.ratelimit import limiter
    engine = world["db"].get_bind()
    from sqlalchemy.orm import sessionmaker
    S = sessionmaker(bind=engine, info={})

    def override():
        s = S()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    was, limiter.enabled = limiter.enabled, False
    try:
        c = TestClient(app)
        hdr = {"Authorization": f"Bearer {create_access_token(world['manager'].id, 'AGENCY_MANAGER', 'dev-device-01')}"}
        ok = c.post(f"/api/v1/admin/users/{world['own_agent'].id}/password-reset", headers=hdr)
        assert ok.status_code == 200 and ok.json()["sent"] is True and "token" not in ok.json()
        assert world["sent"][-1][0] == "+919800000010"
        other = c.post(f"/api/v1/admin/users/{world['other_agent'].id}/password-reset", headers=hdr)
        assert other.status_code == 404                               # not theirs reads as not found
        mfa = c.post(f"/api/v1/admin/users/{world['own_agent'].id}/mfa-reset", headers=hdr)
        assert mfa.status_code == 403                                 # MFA resets stay with admins
        inv = c.get("/api/v1/admin/invites", headers=hdr)
        assert inv.status_code == 403                                 # so do invitations
    finally:
        limiter.enabled = was
        app.dependency_overrides.pop(get_db, None)



def test_a_new_agents_first_password_link_goes_to_the_agent(world):
    """G02's create-agent: the account exists with no usable password; its
    own manager issues the first-password link; it reaches the agent's phone
    and sets a password; a FIRST_LOGIN token 72 h, audited FIRST_PASSWORD."""
    agent = world["own_agent"]
    out = password_service.issue_first_password(world["db"], world["manager"], agent, request=_request())
    assert set(out) == {"sent", "expires_at"} and out["sent"] is True
    to, body, kw = world["sent"][-1]
    assert to == "+919800000010" and "Welcome to TIQCollect" in body and kw["user_id"] == agent.id
    row = world["db"].query(PasswordResetToken).filter(PasswordResetToken.user_id == agent.id).one()
    assert row.kind == "FIRST_LOGIN" and row.issued_by == world["manager"].id
    ttl = password_service.utc(row.expires_at) - password_service.utc(row.created_at)
    assert timedelta(hours=71, minutes=59) < ttl <= timedelta(hours=72, seconds=5)
    issued = _audits(world["db"], AuditAction.PASSWORD_RESET_ISSUED)[-1]
    assert issued.details == {"kind": "FIRST_PASSWORD", "channel": "SMS"}
    password_service.reset_with_token(world["db"], _texted_token(world), NEW)
    world["db"].expire_all()
    assert verify_password(NEW, agent.hashed_password)


@pytest.mark.parametrize("admin", ["manager2", "bank_admin", "analyst"])
def test_only_the_agents_own_manager_or_agency_admin_issues_it(world, admin):
    with pytest.raises(AppException) as e:
        password_service.issue_first_password(world["db"], world[admin], world["own_agent"])
    assert e.value.status_code == 404
    assert world["db"].query(PasswordResetToken).count() == 0


def test_the_agency_admin_may_issue_it_too(world):
    assert password_service.issue_first_password(world["db"], world["agency_admin"], world["own_agent"])["sent"]
