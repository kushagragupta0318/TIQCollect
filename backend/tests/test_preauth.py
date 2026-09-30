"""A13b S1b on SQLite: core/preauth resolves a principal's tenant before RLS
lets the API read anything, and PLATFORM enters one bank only after its rule
passes. tests/pg/test_pg_preauth.py runs the same flows as tiq_app, through
the v2_0019 SECURITY DEFINER functions."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from app.core import database, preauth
from app.core.audit import write_audit
from app.core.errors import AppException
from app.core.security import token_sha256
from app.models.agent import Agent
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserInvite
from app.models.user import User, UserRole
from app.services import password_service
from app.services.scope import platform_acts_in_bank
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _rev(stem: str):
    spec = importlib.util.spec_from_file_location(f"rev_{stem}", VERSIONS / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine, info={})()
    people = {
        "manager": (UserRole.AGENCY_MANAGER, TEST_BANK_ID, TEST_AGENCY_ID, "9811100001"),
        "agent": (UserRole.FIELD_AGENT, TEST_BANK_ID, TEST_AGENCY_ID, "9811100002"),
        "bank_admin": (UserRole.BANK_ADMIN, TEST_BANK_ID, None, "9811100003"),
        "analyst": (UserRole.BANK_ANALYST, TEST_BANK_ID, None, "9811100004"),
        "platform": (UserRole.PLATFORM_ADMIN, None, None, "9811100005"),
    }
    for key, (role, bank, agency, phone) in people.items():
        s.add(User(id=test_id(f"u:{key}"), email=f"{key}@preauth.test", phone=phone, full_name=key.title(),
                   hashed_password="x", role=role, bank_id=bank, agency_id=agency))
    s.flush()
    s.add(Agent(id=test_id("ag:agent"), user_id=test_id("u:agent"), employee_code="PRE-1", id_card_number="PRE-1",
                base_latitude=28.4, base_longitude=77.0, territory="Gurugram", manager_user_id=test_id("u:manager"),
                bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID))
    s.add(UserInvite(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, purpose="USER_ONBOARD", email="new@preauth.test",
                     role=UserRole.AGENCY_MANAGER, token_sha256=token_sha256("invite-token"),
                     invited_by=test_id("u:bank_admin"), expires_at=_in_days(3)))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def _in_days(days: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(days=days)


def test_each_lookup_returns_the_tenant_and_role_only(db):
    manager = preauth.Principal(test_id("u:manager"), TEST_BANK_ID, TEST_AGENCY_ID, UserRole.AGENCY_MANAGER)
    assert preauth.by_user_id(db, test_id("u:manager")) == manager
    assert preauth.by_email(db, "manager@preauth.test") == manager
    assert preauth.by_phone(db, ["9811100001", "919811100001"]) == manager
    assert preauth.by_agent_id(db, test_id("ag:agent")).user_id == test_id("u:agent")
    invitee = preauth.invitee_by_token_sha(db, token_sha256("invite-token"))
    assert invitee == preauth.Principal(None, TEST_BANK_ID, TEST_AGENCY_ID, UserRole.AGENCY_MANAGER)
    assert invitee.scope == "AGENCY"
    assert preauth.by_user_id(db, test_id("u:platform")).scope == "PLATFORM"


@pytest.mark.parametrize("call", [
    lambda d: preauth.by_user_id(d, test_id("u:nobody")),
    lambda d: preauth.by_user_id(d, "not-a-uuid"),
    lambda d: preauth.by_user_id(d, None),
    lambda d: preauth.by_email(d, "Manager@preauth.test"),        # exact, as login's own query
    lambda d: preauth.by_email(d, ""),
    lambda d: preauth.by_phone(d, ["", None]),
    lambda d: preauth.by_agent_id(d, "x"),
    lambda d: preauth.invitee_by_token_sha(d, token_sha256("wrong")),
], ids=["unknown-id", "malformed-id", "no-id", "case-differs", "no-email", "no-phone", "bad-agent", "bad-invite"])
def test_an_unknown_or_malformed_key_resolves_to_nobody(db, call):
    assert call(db) is None


def test_bind_carries_the_principal_into_the_session(db):
    preauth.bind(db, preauth.by_user_id(db, test_id("u:bank_admin")))
    assert db.info[database.TENANT_CONTEXT] == {"bank_id": TEST_BANK_ID, "agency_id": None, "scope": "BANK",
                                                "user_id": test_id("u:bank_admin")}


def test_platform_acts_in_one_bank_and_only_platform_may(db):
    platform, bank_admin = db.get(User, test_id("u:platform")), db.get(User, test_id("u:bank_admin"))
    with pytest.raises(PermissionError):
        platform_acts_in_bank(db, bank_admin, TEST_BANK_ID)
    assert database.TENANT_CONTEXT not in db.info
    with pytest.raises(AppException) as exc:
        platform_acts_in_bank(db, platform, "not-a-bank")
    assert exc.value.status_code == 404 and database.TENANT_CONTEXT not in db.info
    assert platform_acts_in_bank(db, platform, TEST_BANK_ID.upper()) == TEST_BANK_ID
    assert db.info[database.TENANT_CONTEXT] == {"bank_id": TEST_BANK_ID, "agency_id": None, "scope": "BANK",
                                                "user_id": test_id("u:platform")}


def test_platform_enters_the_bank_only_after_its_rule_passes(db):
    """can_manage lets PLATFORM act on a BANK_ADMIN only. For anyone else the
    target is refused BEFORE the request enters that bank."""
    platform = db.get(User, test_id("u:platform"))
    assert password_service.credential_target(db, platform, test_id("u:analyst")) is None
    assert database.TENANT_CONTEXT not in db.info
    assert password_service.credential_target(db, platform, test_id("u:nobody")) is None
    assert database.TENANT_CONTEXT not in db.info
    target = password_service.credential_target(db, platform, test_id("u:bank_admin"))
    assert target.id == test_id("u:bank_admin")
    assert db.info[database.TENANT_CONTEXT]["bank_id"] == TEST_BANK_ID


def test_a_tenantless_refusal_still_lands_on_sqlite(db):
    assert write_audit(db, action=AuditAction.LOGIN_FAILED, user_id=None, entity_type="User",
                       entity_id="whoever", success=False, failure_reason="unknown link") is True
    row = db.query(AuditLog).one()
    assert (row.user_id, row.bank_id, row.agency_id, row.success) == (None, None, None, False)


def test_v2_0019_restates_v2_0012_s_policy_before_extending_it():
    """The revision restates the step-1 expression as a frozen literal; this
    keeps the restatement honest, and the new one a strict extension of it."""
    step1, step2a = _rev("v2_0012_rls")._policies(), _rev("v2_0019_auth_definers").POLICIES_REPLACED
    for table, (old, new) in step2a.items():
        assert old == step1[table], table
        assert new.startswith(f"({old} OR ") and "tenancy.current_scope() = 'PLATFORM'" in new, table
        assert "bank_id IS NULL" in new and "tenancy.current_user_id()" in new, table
