"""A13b S1a: every audit row carries a tenant, or no bank or agency can read it
once RLS is enforced (audit_logs' WITH CHECK refuses a NULL-bank row from any
principal but PLATFORM, and write_audit would swallow the refusal).

- A row with a user takes that user's bank and agency (tenancy_listener).
- A row no user wrote passes its entity's tenant; the writers are checked by
  AST below, and the behaviour at each writer by its own suite
  (ptp_lifecycle, ingest_feed_v2, placement_service, bank_agency_onboarding).
tests/pg/test_pg_audit_tenant.py proves such rows pass WITH CHECK as tiq_app.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.core.audit import stage_audit, write_audit
from app.models.audit_log import AuditAction, AuditLog
from app.models.tenancy_listener import TenantMismatchError
from app.models.user import User, UserRole
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BACKEND = pathlib.Path(__file__).resolve().parents[1]

ACTORS = {
    UserRole.AGENCY_MANAGER: (TEST_BANK_ID, TEST_AGENCY_ID),
    UserRole.FIELD_AGENT: (TEST_BANK_ID, TEST_AGENCY_ID),
    UserRole.BANK_ADMIN: (TEST_BANK_ID, None),
    UserRole.SERVICE: (TEST_BANK_ID, None),
    UserRole.PLATFORM_ADMIN: (None, None),
}


@pytest.fixture
def db():
    engine = make_engine()
    create_schema(engine)
    # No default tenant: every tenant id below is given, so nothing is filled by the test harness.
    s = make_session_factory(engine, info={})()
    for n, (role, (bank, agency)) in enumerate(ACTORS.items()):
        s.add(User(id=test_id(f"user:{role.value}"), bank_id=bank, agency_id=agency, role=role,
                   email=f"{role.value.lower()}@audit-tenant.test", phone=f"98100000{n:02d}",
                   full_name=f"{role.value.title()} Tester", hashed_password="x"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


@pytest.mark.parametrize("role", list(ACTORS))
def test_an_actors_row_carries_the_actors_tenant(db, role):
    assert write_audit(db, action=AuditAction.LOGIN, user_id=test_id(f"user:{role.value}")) is True
    row = db.query(AuditLog).one()
    assert (row.bank_id, row.agency_id) == ACTORS[role]


def test_a_row_no_one_wrote_carries_the_tenant_it_is_given(db):
    stage_audit(db, action=AuditAction.PTP_UPDATED, user_id=None, entity_type="PTP", entity_id="p-1",
                bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.commit()
    row = db.query(AuditLog).one()
    assert (row.user_id, row.bank_id, row.agency_id) == (None, TEST_BANK_ID, TEST_AGENCY_ID)


def test_a_given_tenant_that_contradicts_the_actor_is_refused(db):
    """Fail closed: a row cannot claim a tenant its actor does not belong to."""
    stage_audit(db, action=AuditAction.CASE_UPDATED, user_id=test_id("user:AGENCY_MANAGER"),
                bank_id=test_id("bank:elsewhere"))
    with pytest.raises(TenantMismatchError):
        db.flush()
    db.rollback()
    # write_audit's contract is unchanged: the refusal is logged, never raised.
    assert write_audit(db, action=AuditAction.CASE_UPDATED, user_id=test_id("user:AGENCY_MANAGER"),
                       bank_id=test_id("bank:elsewhere")) is False
    assert db.query(AuditLog).count() == 0


def _audit_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in ("AuditLog", "stage_audit", "write_audit"):
                yield node


def test_every_row_no_user_wrote_names_its_tenant():
    """A literal `user_id=None` audit write must name both tenant columns
    (agency_id=None, deliberately, for a bank-level entity). A user_id held in a
    variable is not seen here: those are the pre-authentication writers, which
    S1b moves behind a SECURITY DEFINER writer."""
    offenders, seen = [], 0
    for root in ("app", "scripts"):
        for path in sorted((BACKEND / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for call in _audit_calls(tree):
                kw = {k.arg: k.value for k in call.keywords if k.arg}
                uid = kw.get("user_id")
                if isinstance(uid, ast.Constant) and uid.value is None:
                    seen += 1
                    if not {"bank_id", "agency_id"} <= set(kw):
                        offenders.append(f"{path.relative_to(BACKEND)}:{call.lineno}")
    assert seen >= 9, f"the scan found only {seen} system audit writes; is it still looking?"
    assert offenders == [], offenders
