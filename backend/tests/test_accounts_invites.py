"""A06 — invitations (P1, d4, 2026-09-28).

Single-use, hashed at rest, 72 hours; the invitee sets their own password;
nobody invites into another tenant. The session factory here carries NO
default tenant, so a user created without its bank or agency fails instead of
being quietly filled in by the test helper.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.database import get_db
from app.core.errors import AppException, ErrorCode
from app.core.security import create_access_token, decode_token, hash_password, token_sha256
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserInvite
from app.models.tenancy import Agency, Bank
from app.models.user import User, UserRole
from app.services import invite_service
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

PASSWORD = "Harbour-Lights-2026"
DEVICE = "chrome-win11-4a7e"
BANK2 = test_id("bank:girivan")
AGENCY2 = test_id("agency:almora-recovery")          # bank 2
AGENCY_PENDING = test_id("agency:sahyadri-pending")   # bank 1, onboarding
AGENCY_SUSPENDED = test_id("agency:konkan-suspended")  # bank 1


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


def _user(db, key, role, *, bank=TEST_BANK_ID, agency=None, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
             hashed_password=hash_password(PASSWORD), role=role, bank_id=bank, agency_id=agency)
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
    db.add_all([
        Agency(id=AGENCY2, bank_id=BANK2, code="AGY-ALM", legal_name="Almora Recovery Desk LLP",
               status="ACTIVE", contacts=[], is_demo=True),
        Agency(id=AGENCY_PENDING, bank_id=TEST_BANK_ID, code="AGY-SAH", legal_name="Sahyadri Field Recovery",
               status="PENDING", contacts=[], is_demo=True),
        Agency(id=AGENCY_SUSPENDED, bank_id=TEST_BANK_ID, code="AGY-KON", legal_name="Konkan Collections",
               status="SUSPENDED", contacts=[], is_demo=True),
    ])
    db.flush()
    w = {
        "db": db, "Session": Session,
        "platform": _user(db, "platform", UserRole.PLATFORM_ADMIN, bank=None, phone="9800000001"),
        "bank_admin": _user(db, "bankadmin", UserRole.BANK_ADMIN, phone="9800000002"),
        "analyst": _user(db, "analyst", UserRole.BANK_ANALYST, phone="9800000003"),
        "bank2_admin": _user(db, "bank2admin", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000004"),
        "agency_admin": _user(db, "agencyadmin", UserRole.AGENCY_ADMIN, agency=TEST_AGENCY_ID, phone="9800000005"),
        "manager": _user(db, "manager", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000006"),
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


def _invite(w, inviter="bank_admin", *, email="neha.kapoor@example.in", role=UserRole.BANK_ANALYST,
            agency_id=None, bank_id=None, phone="9811100001", channel="LINK"):
    return invite_service.create_invite(w["db"], w[inviter], email=email, role=role, full_name="Neha Kapoor",
                                        phone=phone, agency_id=agency_id, bank_id=bank_id, channel=channel,
                                        request=_request())


def _audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


# ── the token ────────────────────────────────────────────────────────────────

def test_the_token_is_shown_once_and_stored_only_as_its_hash(world):
    out = _invite(world)
    token = out["token"]
    row = world["db"].get(UserInvite, out["invite"]["id"])
    assert row.token_sha256 == token_sha256(token) and token not in row.token_sha256
    assert "token" not in out["invite"] and "token_sha256" not in out["invite"]
    assert out["path"] == f"/set-password?token={token}"
    listed = invite_service.list_invites(world["db"], world["bank_admin"])
    assert token not in repr(listed) and row.token_sha256 not in repr(listed)


def test_it_expires_after_72_hours(world):
    out = _invite(world)
    row = world["db"].get(UserInvite, out["invite"]["id"])
    created, expires = invite_service.utc(row.created_at), invite_service.utc(row.expires_at)
    assert timedelta(hours=71, minutes=59) < expires - created <= timedelta(hours=72, seconds=5)
    row.expires_at = invite_service.now() - timedelta(seconds=1)
    world["db"].commit()
    with pytest.raises(AppException) as e:
        invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    assert e.value.code == ErrorCode.INVITE_INVALID


# ── accept ──────────────────────────────────────────────────────────────────

def test_accepting_creates_the_account_in_the_inviters_bank_and_signs_it_in(world):
    out = _invite(world)
    res = invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    claims = decode_token(res["access_token"])
    user = world["db"].query(User).filter(User.email == "neha.kapoor@example.in").one()
    assert user.role == UserRole.BANK_ANALYST and user.bank_id == TEST_BANK_ID and user.agency_id is None
    assert claims["sub"] == user.id and claims["sid"] and claims["bank_id"] == TEST_BANK_ID
    assert user.password_changed_at is not None and not user.must_change_password
    inv = world["db"].get(UserInvite, out["invite"]["id"])
    assert inv.accepted_user_id == user.id and invite_service.status_of(inv) == "ACCEPTED"
    assert len(_audits(world["db"], AuditAction.USER_INVITED)) == 1
    assert len(_audits(world["db"], AuditAction.INVITE_ACCEPTED)) == 1
    created = _audits(world["db"], AuditAction.USER_CREATED)
    assert len(created) == 1 and created[0].details["invited_by"] == world["bank_admin"].id
    # Signed in through the same path as /auth/login (audit MED): a LOGIN row naming the route.
    login_row = world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.LOGIN).one()
    assert login_row.user_id == user.id and login_row.details == {"method": "invite"}


def test_a_used_link_cannot_create_a_second_account(world):
    out = _invite(world)
    invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    with pytest.raises(AppException) as e:
        invite_service.accept_invite(world["db"], out["token"], "Another-Password-99", "other-device-1", _request())
    assert e.value.code == ErrorCode.INVITE_INVALID
    assert world["db"].query(User).filter(User.email == "neha.kapoor@example.in").count() == 1


def test_two_racing_accepts_create_one_account(world, monkeypatch):
    """The compare-and-swap on accepted_at: the second request passes the
    read (the invite still looks open to it) and must lose the write."""
    out = _invite(world)
    real = invite_service._open_by_token
    Session2 = world["Session"]

    def racing(db, token):
        inv = real(db, token)
        s2 = Session2()
        try:
            monkeypatch.setattr(invite_service, "_open_by_token", real)
            invite_service.accept_invite(s2, token, PASSWORD, "tab-two-device", _request())
        finally:
            s2.close()
        return inv
    monkeypatch.setattr(invite_service, "_open_by_token", racing)
    with pytest.raises(AppException) as e:
        invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    assert e.value.code == ErrorCode.INVITE_INVALID
    world["db"].expire_all()
    assert world["db"].query(User).filter(User.email == "neha.kapoor@example.in").count() == 1


@pytest.mark.parametrize("bad", ["short1A", "lettersonlypassword", "1234567890123", "Password@123",
                                  "neha.kapoor2026x"])
def test_a_weak_password_is_refused_and_the_invite_stays_open(world, bad):
    out = _invite(world)
    with pytest.raises(AppException) as e:
        invite_service.accept_invite(world["db"], out["token"], bad, DEVICE, _request())
    assert e.value.code == ErrorCode.PASSWORD_POLICY
    world["db"].expire_all()
    assert invite_service.status_of(world["db"].get(UserInvite, out["invite"]["id"])) == "OPEN"


@pytest.mark.parametrize("token", ["", "x" * 43, "not-a-real-token-at-all-000000000000000000"])
def test_an_unknown_token_is_the_same_error_as_an_expired_one(world, token):
    with pytest.raises(AppException) as e:
        invite_service.preview_invite(world["db"], token)
    assert e.value.code == ErrorCode.INVITE_INVALID and e.value.status_code == 400


def test_a_withdrawn_invite_cannot_be_accepted(world):
    out = _invite(world)
    invite_service.revoke_invite(world["db"], world["bank_admin"], out["invite"]["id"], request=_request())
    with pytest.raises(AppException):
        invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    assert len(_audits(world["db"], AuditAction.INVITE_REVOKED)) == 1


# ── who may invite whom ─────────────────────────────────────────────────────

@pytest.mark.parametrize("inviter,role,agency,ok", [
    ("bank_admin", UserRole.BANK_ADMIN, None, True),
    ("bank_admin", UserRole.BANK_TECHOPS, None, True),
    ("bank_admin", UserRole.AGENCY_ADMIN, TEST_AGENCY_ID, True),
    ("bank_admin", UserRole.AGENCY_ADMIN, AGENCY_PENDING, True),        # master login during onboarding
    ("bank_admin", UserRole.AGENCY_MANAGER, TEST_AGENCY_ID, False),     # the agency admin's call
    ("bank_admin", UserRole.FIELD_AGENT, TEST_AGENCY_ID, False),        # Manage Agents (G)
    ("bank_admin", UserRole.PLATFORM_ADMIN, None, False),
    ("bank_admin", UserRole.SERVICE, None, False),
    ("analyst", UserRole.BANK_ANALYST, None, False),
    ("agency_admin", UserRole.AGENCY_MANAGER, TEST_AGENCY_ID, True),
    ("agency_admin", UserRole.AGENCY_ADMIN, TEST_AGENCY_ID, False),
    ("agency_admin", UserRole.BANK_ANALYST, None, False),
    ("manager", UserRole.AGENCY_MANAGER, TEST_AGENCY_ID, False),
])
def test_who_may_invite_whom(world, inviter, role, agency, ok):
    if ok:
        out = _invite(world, inviter, role=role, agency_id=agency)
        assert out["invite"]["role"] == role.value
    else:
        with pytest.raises(AppException) as e:
            _invite(world, inviter, role=role, agency_id=agency)
        assert e.value.status_code == 403 and e.value.code == ErrorCode.INVITE_NOT_ALLOWED
        assert _audits(world["db"], AuditAction.ROLE_VIOLATION_ATTEMPT)


def test_a_bank_admin_cannot_invite_into_another_banks_agency(world):
    with pytest.raises(AppException) as e:
        _invite(world, "bank_admin", role=UserRole.AGENCY_ADMIN, agency_id=AGENCY2)
    assert e.value.status_code == 404                    # not theirs reads as not found


def test_the_tenant_comes_from_the_inviter_not_the_request(world):
    out = _invite(world, "bank_admin", role=UserRole.BANK_ANALYST, bank_id=BANK2)
    assert out["invite"]["bank_id"] == TEST_BANK_ID


def test_an_agency_admin_cannot_name_another_agency(world):
    out = _invite(world, "agency_admin", role=UserRole.AGENCY_MANAGER, agency_id=AGENCY2)
    assert out["invite"]["agency_id"] == TEST_AGENCY_ID


def test_a_suspended_agency_gets_no_invites(world):
    with pytest.raises(AppException) as e:
        _invite(world, "bank_admin", role=UserRole.AGENCY_ADMIN, agency_id=AGENCY_SUSPENDED)
    assert e.value.status_code == 409


def test_the_platform_admin_brings_in_a_banks_first_admin(world):
    out = _invite(world, "platform", role=UserRole.BANK_ADMIN, bank_id=BANK2)
    assert out["invite"]["bank_id"] == BANK2


def test_an_existing_account_or_an_open_invite_blocks_a_new_one(world):
    with pytest.raises(AppException) as e:
        _invite(world, email="analyst@example.in")
    assert e.value.status_code == 409
    _invite(world)
    with pytest.raises(AppException):
        _invite(world, phone="9811100002")               # same email, still open


def test_admins_see_only_their_own_tenants_invites(world):
    _invite(world, "bank_admin")
    _invite(world, "bank2_admin", email="farhan.q@example.in", phone="9811100009")
    mine = invite_service.list_invites(world["db"], world["bank_admin"])
    theirs = invite_service.list_invites(world["db"], world["bank2_admin"])
    assert [i["email"] for i in mine] == ["neha.kapoor@example.in"]
    assert [i["email"] for i in theirs] == ["farhan.q@example.in"]
    assert invite_service.list_invites(world["db"], world["manager"]) == []
    other = theirs[0]["id"]
    with pytest.raises(AppException) as e:
        invite_service.revoke_invite(world["db"], world["bank_admin"], other)
    assert e.value.status_code == 404


# ── delivery ────────────────────────────────────────────────────────────────

def test_email_delivery_is_refused_not_faked(world):
    with pytest.raises(AppException) as e:
        _invite(world, channel="EMAIL")
    assert e.value.code == ErrorCode.CHANNEL_UNAVAILABLE
    assert world["db"].query(UserInvite).count() == 0


@pytest.mark.parametrize("base", ["", "${PUBLIC_BASE_URL}"])
def test_sms_needs_a_public_base_url(world, monkeypatch, base):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", base)
    with pytest.raises(AppException) as e:
        _invite(world, channel="SMS")
    assert e.value.code == ErrorCode.CHANNEL_UNAVAILABLE


def test_an_sms_invite_texts_the_link_and_never_returns_the_token(world, monkeypatch):
    from app.core.config import settings
    from app.services import notification_service
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://fieldops.example.in")
    sent = []
    monkeypatch.setattr(notification_service.NotificationService, "send_sms",
                        staticmethod(lambda to, body, **kw: sent.append((to, body, kw)) or True))
    out = _invite(world, channel="SMS")
    assert "token" not in out and out["delivered"] is True
    to, body, kw = sent[0]
    assert to == "+919811100001" and "https://fieldops.example.in/set-password?token=" in body
    assert kw["bank_id"] == TEST_BANK_ID                  # a subject, so demo tenants are suppressed


# ── the routes ──────────────────────────────────────────────────────────────

def _hdr(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'dev-device-01')}"}


def test_the_routes_end_to_end(world):
    c = TestClient(app)
    r = c.post("/api/v1/admin/invites", headers=_hdr(world["bank_admin"]),
               json={"email": "Ritu.Sen@Example.in", "full_name": "Ritu Sen", "phone": "98111 00007",
                     "role": "BANK_TECHOPS"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    p = c.post("/api/v1/auth/invites/preview", json={"token": token})
    assert p.status_code == 200 and p.json()["email"] == "ritu.sen@example.in"
    assert p.json()["organisation"] == "Meridian Trust Bank Ltd."
    a = c.post("/api/v1/auth/invites/accept", json={"token": token, "password": PASSWORD, "device_id": DEVICE})
    assert a.status_code == 200 and a.json()["role"] == "BANK_TECHOPS"
    again = c.post("/api/v1/auth/invites/accept", json={"token": token, "password": PASSWORD, "device_id": DEVICE})
    assert again.status_code == 400 and again.json()["code"] == "INVITE_INVALID"


def test_a_manager_cannot_reach_the_admin_routes(world):
    c = TestClient(app)
    r = c.get("/api/v1/admin/invites", headers=_hdr(world["manager"]))
    assert r.status_code == 403



def test_the_accept_swap_itself_refuses_an_expired_invite(world, monkeypatch):
    """Expiry is inside the compare-and-swap, not only in the read before it
    (audit LOW): an invite that expires between the two is still refused."""
    out = _invite(world)
    row = world["db"].get(UserInvite, out["invite"]["id"])
    row.expires_at = invite_service.now() - timedelta(seconds=1)
    world["db"].commit()
    monkeypatch.setattr(invite_service, "_open_by_token", lambda db, token: db.get(UserInvite, row.id))
    with pytest.raises(AppException) as e:
        invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    assert e.value.code == ErrorCode.INVITE_INVALID
    assert world["db"].query(User).filter(User.email == "neha.kapoor@example.in").count() == 0


def test_a_failed_accept_leaves_no_audit_rows(world):
    """The rows are staged with the account, so a clash that rolls the
    account back rolls them back too (audit MED)."""
    out = _invite(world)
    world["db"].add(User(id=test_id("u:squatter"), email="squatter@example.in", phone="9811100001",
                         full_name="Squatter", hashed_password=hash_password(PASSWORD),
                         role=UserRole.BANK_ANALYST, bank_id=TEST_BANK_ID))
    world["db"].commit()
    with pytest.raises(AppException) as e:
        invite_service.accept_invite(world["db"], out["token"], PASSWORD, DEVICE, _request())
    assert e.value.status_code == 409
    assert _audits(world["db"], AuditAction.INVITE_ACCEPTED) == []
    assert _audits(world["db"], AuditAction.USER_CREATED) == []


@pytest.mark.parametrize("email,status", [
    ("kavya.nair@sarthakrecovery.test", 201),     # demo domains are .test: EmailStr would have 422'd this
    ("not-an-address", 422),
    ("two@@example.in", 422),
])
def test_the_invite_route_takes_account_emails(world, email, status):
    c = TestClient(app)
    r = c.post("/api/v1/admin/invites", headers=_hdr(world["bank_admin"]),
               json={"email": email, "full_name": "Kavya Nair", "phone": "9811100031", "role": "BANK_ANALYST"})
    assert r.status_code == status, r.text
