"""The bank's usage & cost page (GET /bank/usage): tenant isolation, and the
aggregates it reports (F11).

Mirrors tests/test_bank_audit.py's shape and its reason for existing: a bank
reading another bank's LLM spend is not a wrong number on a dashboard, it is
one lender seeing another's cost structure.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import _get_token_payload, get_current_user
from app.main import app
from app.models.llm_call import LLMCall
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from tests._db import DEFAULT_TENANT, create_schema, drop_schema, make_engine, make_session_factory

OURS = DEFAULT_TENANT["bank_id"]
THEIRS = "11111111-2222-3333-4444-555555555555"

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)

_SEQ = itertools.count(1)


@pytest.fixture()
def db():
    create_schema(engine)
    s = TestingSession()
    # llm_calls.bank_id carries a real FK (unlike audit_logs, which does not)
    # -- THEIRS must be a real bank row or the insert in _call() violates it.
    s.add(Bank(id=THEIRS, code="OTB", legal_name="Other Bank Ltd.", display_name="Other Bank",
               timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    s.commit()
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


def _call(db, *, bank_id=OURS, feature="briefing", model="claude-haiku-4-5-20251001",
         provider="anthropic", input_tokens=1000, output_tokens=200, cache_tokens=0,
         cost=0.0025, minutes_ago=5):
    r = LLMCall(created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
               bank_id=bank_id, provider=provider, model=model, feature=feature,
               input_tokens=input_tokens, output_tokens=output_tokens, cache_tokens=cache_tokens,
               cost=cost)
    db.add(r)
    db.commit()
    return r


def test_a_bank_reads_only_its_own_calls(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, feature="briefing", cost=0.01)
    _call(db, feature="case_ranking", cost=0.02)
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    assert body["totals"]["calls"] == 2
    assert body["totals"]["cost_usd"] == pytest.approx(0.03)
    assert {r["feature"] for r in body["by_feature"]} == {"briefing", "case_ranking"}


def test_another_banks_calls_are_never_returned(db):
    """The one that matters: a leak here is one lender reading another's cost
    structure, not a wrong figure -- checked on every shape the page shows,
    not just the totals tile."""
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, feature="briefing", cost=0.01)
    _call(db, bank_id=THEIRS, feature="case_ranking", cost=99.0)
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    assert body["totals"]["calls"] == 1
    assert body["totals"]["cost_usd"] == pytest.approx(0.01)
    assert {r["feature"] for r in body["by_feature"]} == {"briefing"}
    assert sum(r["cost_usd"] for r in body["by_day"]) == pytest.approx(0.01)


def test_an_unattributed_call_is_counted_not_charged_to_anyone(db):
    """A row from before every call site was wired (2026-10-07), or any
    future caller that omits bank_id — a known, counted gap, not a leak and
    not a silent free ride."""
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, cost=0.01)
    _call(db, bank_id=None, cost=5.0)
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    assert body["totals"]["calls"] == 1                          # not charged to this bank
    assert body["coverage"]["pending_attribution"] == 1          # but declared
    assert "cannot be charged" in body["coverage"]["note"]


def test_a_call_with_no_known_price_is_flagged_not_zeroed(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, cost=0.01)
    _call(db, model="some-future-model", cost=None)
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    assert body["totals"]["calls"] == 2
    assert body["totals"]["cost_usd"] == pytest.approx(0.01)     # the unknown one excluded, not zeroed-in
    assert body["totals"]["unpriced_calls"] == 1


def test_unpriced_calls_are_flagged_on_the_breakdowns_too(db):
    """Not just the totals tile -- the same row the by-feature table and the
    by-day chart render must say "unpriced", end to end through the API,
    not only inside usage_read.py's own return value."""
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, feature="briefing", cost=0.01)
    _call(db, feature="briefing", model="some-future-model", cost=None)
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    briefing = next(r for r in body["by_feature"] if r["feature"] == "briefing")
    assert briefing["unpriced_calls"] == 1
    assert briefing["cost_usd"] == pytest.approx(0.01)
    assert sum(d["unpriced_calls"] for d in body["by_day"]) == 1
    assert body["totals"]["unpriced_calls"] == 1


def test_calls_break_down_by_day(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, cost=0.01, minutes_ago=5)
    _call(db, cost=0.02, minutes_ago=60 * 24 + 5)   # yesterday
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    assert len(body["by_day"]) == 2
    assert sum(d["calls"] for d in body["by_day"]) == 2


def test_the_window_narrows_what_is_returned(db):
    actor = _user(db, UserRole.BANK_ADMIN, bank_id=OURS)
    _call(db, cost=0.01, minutes_ago=5)
    _call(db, cost=0.02, minutes_ago=60 * 24 * 60)   # well outside the 30-day default
    body = _client(db, actor).get("/api/v1/bank/usage").json()
    assert body["totals"]["calls"] == 1


@pytest.mark.parametrize("role", [UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN, UserRole.FIELD_AGENT])
def test_an_agency_user_cannot_read_the_bank_usage_page(db, role):
    user = _user(db, role, bank_id=OURS, agency_id=DEFAULT_TENANT["agency_id"])
    assert _client(db, user).get("/api/v1/bank/usage").status_code == 403


def test_a_bank_analyst_cannot_read_it_either():
    """llm_usage.read is (BANK_ADMIN, BANK_TECHOPS) only — core/permissions.py's
    catalog, not restated here as a literal; this proves the gate, not the list."""
    from app.core.permissions import has_capability
    assert has_capability(UserRole.BANK_ADMIN, "llm_usage.read")
    assert has_capability(UserRole.BANK_TECHOPS, "llm_usage.read")
    assert not has_capability(UserRole.BANK_ANALYST, "llm_usage.read")
