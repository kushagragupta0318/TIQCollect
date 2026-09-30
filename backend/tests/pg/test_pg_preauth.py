"""The pre-authentication paths, run AS tiq_app on Postgres (v2_0019).

Every transaction on `as_app` runs as tiq_app (SET LOCAL ROLE from the
engine's begin event), which is what the API becomes at RLS step 2. Before
v2_0019 none of this could work: tiq_app with no tenant bound reads no user,
so no one could sign in (the S1 audit, H5), and a platform admin could not
even read its own row (H4).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event, text
from starlette.requests import Request

from app.core import database, preauth
from app.core.audit import write_audit
from app.core.dependencies import get_current_user
from app.core.security import hash_password, token_sha256
from app.models.agent import Agent
from app.models.audit_log import AuditAction
from app.models.identity import PasswordResetToken, UserInvite
from app.models.tenancy import Agency, Bank
from app.models.user import User, UserRole
from app.services import auth_service, invite_service, password_service
from tests.pg.conftest import drop_database, new_database, run_alembic

B1, B2, A1 = (str(uuid.uuid4()) for _ in range(3))
PASSWORD = "Harbour-Lights-2026"
PEOPLE = {   # key -> (role, bank, agency, phone)
    "manager": (UserRole.AGENCY_MANAGER, B1, A1, "9822200001"),
    "agent": (UserRole.FIELD_AGENT, B1, A1, "9822200002"),
    "bank_admin": (UserRole.BANK_ADMIN, B1, None, "9822200003"),
    "other_bank_admin": (UserRole.BANK_ADMIN, B2, None, "9822200004"),
    "platform": (UserRole.PLATFORM_ADMIN, None, None, "9822200005"),
}
UID = {k: str(uuid.uuid4()) for k in PEOPLE}
AGENT_ID = str(uuid.uuid4())


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest-pg")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


@pytest.fixture(scope="module")
def url():
    from alembic import command
    u = new_database("preauth")
    try:
        run_alembic(u, command.upgrade, "head")
        owner = create_engine(u)
        with database.SessionLocal(bind=owner) as s:          # seeded as the owner: not under test
            for bank, code in ((B1, "GRV"), (B2, "KSF")):
                s.add(Bank(id=bank, code=code, legal_name=f"{code} Finance Ltd", display_name=f"{code} Finance",
                           timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
            s.flush()
            s.add(Agency(id=A1, bank_id=B1, code="AGY-1", legal_name="Nilgiri Recoveries Pvt. Ltd.",
                         trade_name="Nilgiri Recoveries", status="ACTIVE", contacts=[], is_demo=True))
            s.flush()
            for key, (role, bank, agency, phone) in PEOPLE.items():
                s.add(User(id=UID[key], bank_id=bank, agency_id=agency, role=role, phone=phone,
                           email=f"{key}@nilgiri.test", full_name=key.title(), hashed_password=hash_password(PASSWORD)))
            s.flush()
            s.add(Agent(id=AGENT_ID, user_id=UID["agent"], employee_code="NIL-1", id_card_number="NIL-1",
                        base_latitude=11.4, base_longitude=76.7, territory="Ooty", manager_user_id=UID["manager"],
                        bank_id=B1, agency_id=A1))
            s.add(UserInvite(bank_id=B1, agency_id=A1, purpose="USER_ONBOARD", email="newhire@nilgiri.test",
                             full_name="New Hire", role=UserRole.AGENCY_MANAGER,
                             token_sha256=token_sha256("pg-invite-token"), invited_by=UID["bank_admin"],
                             expires_at=datetime.now(timezone.utc) + timedelta(days=3)))
            s.add(PasswordResetToken(user_id=UID["manager"], kind="ADMIN_RESET",
                                     token_sha256=token_sha256("pg-reset-token"), issued_by=UID["bank_admin"],
                                     expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
            s.commit()
        owner.dispose()
        yield u
    finally:
        drop_database(u)


@pytest.fixture
def as_app(url):
    eng = create_engine(url)
    event.listen(eng, "begin", lambda conn: conn.exec_driver_sql("SET LOCAL ROLE tiq_app"))
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def db(as_app):
    s = database.SessionLocal(bind=as_app)
    try:
        yield s
    finally:
        s.close()


def _owner_rows(url, sql: str, **params) -> list[tuple]:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(sql), params).all()
    finally:
        eng.dispose()


def test_unbound_tiq_app_reads_no_user_but_every_lookup_resolves(db):
    assert db.execute(text("SELECT current_user")).scalar() == "tiq_app"
    assert db.execute(text("SELECT count(*) FROM tenancy.users")).scalar() == 0          # fail closed
    manager = preauth.Principal(UID["manager"], B1, A1, UserRole.AGENCY_MANAGER)
    assert preauth.by_user_id(db, UID["manager"]) == manager
    assert preauth.by_email(db, "manager@nilgiri.test") == manager
    assert preauth.by_phone(db, ["9822200001", "919822200001"]) == manager
    assert preauth.by_agent_id(db, AGENT_ID) == preauth.Principal(UID["agent"], B1, A1, UserRole.FIELD_AGENT)
    assert preauth.invitee_by_token_sha(db, token_sha256("pg-invite-token")) == preauth.Principal(
        None, B1, A1, UserRole.AGENCY_MANAGER)
    for miss in (preauth.by_user_id(db, str(uuid.uuid4())), preauth.by_email(db, "nobody@nilgiri.test"),
                 preauth.by_phone(db, ["9000000000"]), preauth.by_agent_id(db, str(uuid.uuid4())),
                 preauth.invitee_by_token_sha(db, token_sha256("nope"))):
        assert miss is None


@pytest.mark.parametrize("fn", ["tenancy.auth_principal_by_id(CAST(:v AS uuid))", "tenancy.auth_principal_by_email(:v)",
                                "audit.auth_write_refusal('LOGIN_FAILED', false, NULL, :v, NULL, NULL, NULL, NULL)"])
def test_the_definers_are_not_public(url, fn):
    """EXECUTE is revoked from PUBLIC: tiq_jobs (granted nothing here) is refused."""
    eng = create_engine(url)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            with eng.begin() as c:
                c.exec_driver_sql("SET LOCAL ROLE tiq_jobs")
                c.execute(text(f"SELECT * FROM {fn}"), {"v": str(uuid.uuid4())})
    finally:
        eng.dispose()


@pytest.mark.parametrize("key", ["manager", "bank_admin", "platform"])
def test_a_password_login_works_as_tiq_app(db, url, key):
    out = auth_service.login(db, f"{key}@nilgiri.test", PASSWORD, f"device-{key}", _request())
    assert out["user_id"] == UID[key] and out["access_token"]
    role, bank, agency, _ = PEOPLE[key]
    assert _owner_rows(url, "SELECT bank_id::text, agency_id::text FROM tenancy.user_sessions WHERE user_id = :u",
                       u=UID[key]) == [(bank, agency)]
    assert (bank, agency) in _owner_rows(url, "SELECT bank_id::text, agency_id::text FROM audit.audit_logs "
                                              "WHERE user_id = :u AND action = 'LOGIN'", u=UID[key])


@pytest.mark.parametrize("key", ["manager", "platform"])
def test_an_access_token_authenticates_as_tiq_app(db, key):
    user = get_current_user({"sub": UID[key], "type": "access"}, db)
    assert user.id == UID[key]


def test_a_platform_admin_reads_its_own_row_and_no_one_elses(db):
    preauth.bind(db, preauth.by_user_id(db, UID["platform"]))
    assert db.execute(text("SELECT id::text FROM tenancy.users")).scalars().all() == [UID["platform"]]
    assert db.execute(text("SELECT count(*) FROM tenancy.banks")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM tenancy.user_sessions WHERE user_id <> :u"),
                      {"u": UID["platform"]}).scalar() == 0


def test_a_password_reset_link_works_as_tiq_app(db, url):
    password_service.reset_with_token(db, "pg-reset-token", "Monsoon-Terrace-77", request=_request())
    assert _owner_rows(url, "SELECT bank_id::text, agency_id::text FROM audit.audit_logs "
                            "WHERE user_id = :u AND action = 'PASSWORD_RESET'", u=UID["manager"]) == [(B1, A1)]


def test_an_invitation_previews_as_tiq_app(db):
    out = invite_service.preview_invite(db, "pg-invite-token")
    assert out["email"] == "newhire@nilgiri.test" and out["organisation"] == "Nilgiri Recoveries Pvt. Ltd."


def test_the_refusal_writer_takes_only_tenantless_refusals_it_lists(db, url):
    marks = {k: str(uuid.uuid4()) for k in ("ok", "success", "unlisted")}
    assert write_audit(db, action=AuditAction.LOGIN_FAILED, user_id=None, entity_type="User",
                       entity_id=marks["ok"], success=False, failure_reason="unknown link") is True
    assert write_audit(db, action=AuditAction.LOGIN_FAILED, user_id=None, entity_type="User",
                       entity_id=marks["success"], success=True) is False
    assert write_audit(db, action=AuditAction.LOGIN, user_id=None, entity_type="User",
                       entity_id=marks["unlisted"], success=False) is False
    got = _owner_rows(url, "SELECT entity_id, bank_id, success FROM audit.audit_logs WHERE entity_id IN "
                           "(:a, :b, :c)", a=marks["ok"], b=marks["success"], c=marks["unlisted"])
    assert got == [(marks["ok"], None, False)]


def test_platform_onboards_one_bank_and_that_operation_cannot_reach_another(db, url):
    """The scoped BANK context: the invite lands in the named bank, and a query
    in the same operation that forgot its bank filter still sees that bank only."""
    platform = get_current_user({"sub": UID["platform"], "type": "access"}, db)
    out = invite_service.create_invite(db, platform, email="first.admin@grv.test", role=UserRole.BANK_ADMIN,
                                       full_name="First Admin", phone="9822200099", bank_id=B1, channel="LINK")
    assert out["token"]
    assert _owner_rows(url, "SELECT bank_id::text, agency_id FROM tenancy.user_invites WHERE email = :e",
                       e="first.admin@grv.test") == [(B1, None)]
    assert _owner_rows(url, "SELECT bank_id::text FROM audit.audit_logs WHERE action = 'USER_INVITED' "
                            "AND user_id = :u", u=UID["platform"]) == [(B1,)]
    # the forgotten filter, inside the same operation's session:
    assert db.execute(text("SELECT id::text FROM tenancy.banks")).scalars().all() == [B1]
    assert db.execute(text("SELECT count(*) FROM tenancy.users WHERE bank_id = :b"), {"b": B2}).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM tenancy.users")).scalar() == 3      # B1's three, nobody else's
