"""The audit rows the application writes pass audit_logs' WITH CHECK as tiq_app,
because each carries a tenant: its actor's (tenancy_listener), or for a row no
user wrote, its entity's (core/audit.py).

Every transaction below runs AS tiq_app (SET LOCAL ROLE from the engine's
begin event, so write_audit's own commit does not drop it), with the tenant
bound the way get_current_user binds it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, text

from app.core import database
from app.core.audit import stage_audit, write_audit
from app.models.audit_log import AuditAction
from app.models.tenancy import Agency, Bank
from app.models.user import User, UserRole, tenant_scope
from tests.pg.conftest import drop_database, new_database, run_alembic

B1, A1, B2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
USERS = {
    UserRole.AGENCY_MANAGER: (B1, A1),
    UserRole.FIELD_AGENT: (B1, A1),
    UserRole.BANK_ADMIN: (B1, None),
    UserRole.SERVICE: (B1, None),
    UserRole.PLATFORM_ADMIN: (None, None),
}
UID = {role: str(uuid.uuid4()) for role in USERS}


@pytest.fixture(scope="module")
def url():
    from alembic import command
    u = new_database("audit_tenant")
    try:
        run_alembic(u, command.upgrade, "head")
        owner = create_engine(u)
        with database.SessionLocal(bind=owner) as s:          # seeded as the owner: not under test
            for bank, code in ((B1, "GRV"), (B2, "KSF")):
                s.add(Bank(id=bank, code=code, legal_name=f"{code} Finance Ltd", display_name=f"{code} Finance",
                           timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
            s.flush()                                         # banks first: the agency's FK needs them
            s.add(Agency(id=A1, bank_id=B1, code="AGY-1", legal_name="Nilgiri Recoveries Pvt. Ltd.",
                         trade_name="Nilgiri Recoveries", status="ACTIVE", contacts=[], is_demo=True))
            s.flush()
            for n, (role, (bank, agency)) in enumerate(USERS.items()):
                s.add(User(id=UID[role], bank_id=bank, agency_id=agency, role=role, hashed_password="x",
                           email=f"{role.value.lower()}@nilgiri.test", phone=f"97100000{n:02d}",
                           full_name=f"{role.value.title()} Pg"))
            s.commit()
        owner.dispose()
        yield u
    finally:
        drop_database(u)


@pytest.fixture
def as_app(url):
    """An engine whose every transaction runs as tiq_app."""
    eng = create_engine(url)
    event.listen(eng, "begin", lambda conn: conn.exec_driver_sql("SET LOCAL ROLE tiq_app"))
    try:
        yield eng
    finally:
        eng.dispose()


def _session(eng, role: UserRole):
    s = database.SessionLocal(bind=eng)
    bank, agency = USERS[role]
    database.apply_tenant_context(s, bank_id=bank, agency_id=agency, scope=tenant_scope(role, agency),
                                  user_id=UID[role])
    return s


def _landed(url, entity_id: str) -> list[tuple]:
    eng = create_engine(url)                                   # read back as the owner, around RLS
    try:
        with eng.connect() as c:
            return c.execute(text("SELECT action::text, user_id::text, bank_id::text, agency_id::text "
                                  "FROM audit.audit_logs WHERE entity_id = :e"), {"e": entity_id}).all()
    finally:
        eng.dispose()


@pytest.mark.parametrize("role", list(USERS))
def test_an_actors_row_lands_as_tiq_app(as_app, url, role):
    entity = str(uuid.uuid4())
    s = _session(as_app, role)
    try:
        assert s.execute(text("SELECT current_user")).scalar() == "tiq_app"
        ok = write_audit(s, action=AuditAction.LOGIN, user_id=UID[role], entity_type="User", entity_id=entity)
    finally:
        s.close()
    assert ok is True
    bank, agency = USERS[role]
    assert _landed(url, entity) == [(AuditAction.LOGIN.value, UID[role], bank, agency)]


def test_a_system_row_in_a_field_agents_request_lands_as_tiq_app(as_app, url):
    """payment_service's PTP_UPDATED: no user wrote it, and it is written in
    the field agent's own request, so it carries the PTP's tenant."""
    entity = str(uuid.uuid4())
    s = _session(as_app, UserRole.FIELD_AGENT)
    try:
        stage_audit(s, action=AuditAction.PTP_UPDATED, user_id=None, entity_type="PTP", entity_id=entity,
                    bank_id=B1, agency_id=A1)
        s.commit()
    finally:
        s.close()
    assert _landed(url, entity) == [(AuditAction.PTP_UPDATED.value, None, B1, A1)]


@pytest.mark.parametrize("bank,agency", [(None, None), (B2, None), (B1, str(uuid.uuid4()))],
                         ids=["no-tenant", "another-bank", "another-agency"])
def test_a_row_outside_the_callers_tenant_is_refused(as_app, url, bank, agency):
    """No tenant (a pre-authentication refusal: S1b's SECURITY DEFINER writer
    is for these), or someone else's: the policy refuses, write_audit reports
    False, and nothing lands."""
    entity = str(uuid.uuid4())
    s = _session(as_app, UserRole.AGENCY_MANAGER)
    try:
        ok = write_audit(s, action=AuditAction.LOGIN_FAILED, user_id=None, entity_type="User", entity_id=entity,
                         bank_id=bank, agency_id=agency)
    finally:
        s.close()
    assert ok is False
    assert _landed(url, entity) == []
