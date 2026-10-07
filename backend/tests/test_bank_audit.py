"""The bank audit trail (GET /bank/audit): who may read it, and what it does
NOT claim to show.

The isolation test is the point of the file. An audit page is where a bank
reads its agencies' behaviour, so a scope that leaks is not a wrong number on
a dashboard — it is one lender reading another's operations. The honesty
tests are the other half: known issue 6 means rows written with no actor and
no entity tenant carry no bank, and a page that quietly dropped them would
make an incomplete trail look like a quiet week.
"""
from __future__ import annotations

import itertools
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import _get_token_payload, get_current_user
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.user import User, UserRole
from app.services.bank import audit_read as ar
from tests._db import (DEFAULT_TENANT, create_schema, drop_schema, make_engine,
                       make_session_factory)

OURS = DEFAULT_TENANT["bank_id"]
THEIRS = "11111111-2222-3333-4444-555555555555"

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)


#: Unique per user row. The schema is dropped between tests, but a counter is
#: cheaper to reason about than hoping two roles never share a name length.
_SEQ = itertools.count(1)


@pytest.fixture()
def db():
    """A FRESH schema per test. Without the drop, audit rows accumulate across
    tests in one module and every `total ==` assertion below would be counting
    the previous test's rows as well as its own -- passing or failing for the
    wrong reason, which is worse than either."""
    create_schema(engine)
    s = TestingSession()
    yield s
    s.close()
    app.dependency_overrides.clear()
    drop_schema(engine)


def _client(db, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[_get_token_payload] = lambda: {"sid": None}
    return TestClient(app)


def _user(db, role: UserRole, **tenant) -> User:
    n = next(_SEQ)
    u = User(email=f"{role.value.lower()}.{n}@girivanfinance.test",
             phone=f"9{n:09d}", full_name="Test Person",
             hashed_password="x", role=role, is_active=True, **tenant)
    db.add(u)
    db.commit()
    return u


def _row(db, *, action=AuditAction.LOGIN, bank_id=OURS, agency_id=None, user_id=None,
         minutes_ago=5, entity_type="User", success=True):
    r = AuditLog(created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
                 action=action, user_id=user_id, bank_id=bank_id, agency_id=agency_id,
                 entity_type=entity_type, entity_id=None, success=success, details={})
    db.add(r)
    db.commit()
    return r


def test_a_bank_reads_its_own_rows_and_its_agencies(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _row(db, action=AuditAction.MODEL_PROMOTED, user_id=actor.id)
    _row(db, action=AuditAction.PLACEMENT_CREATED, agency_id=DEFAULT_TENANT["agency_id"])
    body = _client(db, actor).get("/api/v1/bank/audit").json()
    actions = {e["action"] for e in body["entries"]}
    assert actions == {"MODEL_PROMOTED", "PLACEMENT_CREATED"}
    assert body["total"] == 2


def test_another_banks_rows_are_never_returned(db):
    """The one that matters: a leak here is one lender reading another's
    operations, not a wrong figure."""
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _row(db, action=AuditAction.LOGIN, user_id=actor.id)
    _row(db, action=AuditAction.AGENCY_SUSPENDED, bank_id=THEIRS)
    _row(db, action=AuditAction.MODEL_PROMOTED, bank_id=THEIRS)
    body = _client(db, actor).get("/api/v1/bank/audit").json()
    assert body["total"] == 1
    assert {e["action"] for e in body["entries"]} == {"LOGIN"}
    assert "AGENCY_SUSPENDED" not in body["counts_by_action"]


def test_an_unattributed_row_is_counted_not_listed(db):
    """Known issue 6. Including it would hand this bank somebody else's event;
    dropping it silently would make the trail look complete when it is not."""
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _row(db, action=AuditAction.LOGIN, user_id=actor.id)
    _row(db, action=AuditAction.PTP_UPDATED, bank_id=None)       # system wrote it, no tenant
    body = _client(db, actor).get("/api/v1/bank/audit").json()
    assert body["total"] == 1                                     # not listed
    assert body["coverage"]["pending_attribution"] == 1           # but declared
    assert "cannot be attributed" in body["coverage"]["note"]


def test_the_window_and_the_filters_narrow_what_is_returned(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _row(db, action=AuditAction.LOGIN, user_id=actor.id, minutes_ago=5)
    _row(db, action=AuditAction.DATA_EXPORT, user_id=actor.id, minutes_ago=10)
    _row(db, action=AuditAction.LOGIN, user_id=actor.id, minutes_ago=60 * 24 * 30)   # outside
    c = _client(db, actor)
    assert c.get("/api/v1/bank/audit").json()["total"] == 2                     # default window
    assert c.get("/api/v1/bank/audit?action=DATA_EXPORT").json()["total"] == 1
    assert c.get(f"/api/v1/bank/audit?actor_id={actor.id}").json()["total"] == 2
    # quote(): an ISO timestamp's "+00:00" offset decodes as a SPACE in a query
    # string unless it is encoded, and the route then 422s on an unparseable
    # datetime. Real clients hit this too; it is not a server bug.
    old = quote((datetime.now(timezone.utc) - timedelta(days=60)).isoformat())
    assert c.get(f"/api/v1/bank/audit?since={old}").json()["total"] == 3         # widened


def test_an_inverted_range_is_refused_rather_than_returning_nothing(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    now = datetime.now(timezone.utc)
    r = _client(db, actor).get(f"/api/v1/bank/audit?since={quote(now.isoformat())}"
                               f"&until={quote((now - timedelta(days=1)).isoformat())}")
    assert r.status_code == 422


@pytest.mark.parametrize("role", [UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN, UserRole.FIELD_AGENT])
def test_an_agency_user_cannot_read_the_bank_trail(db, role):
    user = _user(db, role, bank_id=OURS, agency_id=DEFAULT_TENANT["agency_id"])
    assert _client(db, user).get("/api/v1/bank/audit").status_code == 403


def test_the_export_is_scoped_the_same_way_and_audits_itself(db):
    """An export of the audit trail that is itself unaudited was a real defect
    on the manager side (fixed 2026-09-10); the bank's must not reintroduce it."""
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _row(db, action=AuditAction.LOGIN, user_id=actor.id)
    _row(db, action=AuditAction.MODEL_PROMOTED, bank_id=THEIRS)
    r = _client(db, actor).get("/api/v1/bank/audit/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "LOGIN" in r.text and "MODEL_PROMOTED" not in r.text      # same scope as the list
    written = db.query(AuditLog).filter(AuditLog.action == AuditAction.DATA_EXPORT).all()
    assert len(written) == 1
    assert written[0].user_id == actor.id and written[0].bank_id == OURS
    assert written[0].details["endpoint"] == "/bank/audit/export"
    assert "rows" in written[0].details and "format" in written[0].details


def test_the_sensitive_list_names_only_actions_that_exist():
    """A page that highlights an action AuditAction does not declare would
    highlight nothing, silently."""
    declared = {a.value for a in AuditAction}
    assert set(ar.SENSITIVE_ACTIONS) <= declared, set(ar.SENSITIVE_ACTIONS) - declared
