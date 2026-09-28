"""The bank Command Center's KPI catalog and Overview route (P3, C01 + C03).

The numbers themselves need 43's Postgres analytics views
(tests/pg/test_pg_bank_overview.py). What is pinned here: every KPI is
defined once and fully, every query reads only a *_scoped view and names the
caller's bank, a KPI that cannot be computed says why instead of showing a
number, and only the bank roles reach the route.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import _get_token_payload, get_current_user
from app.main import app
from app.models.user import User, UserRole
from app.services.bank import kpi_catalog as K
from tests._db import DEFAULT_TENANT, create_schema, make_engine, make_session_factory


def test_twelve_kpis_each_defined_once_in_two_rows_of_six():
    ids = [k.id for k in K.KPIS]
    assert len(ids) == 12 == len(set(ids))
    assert [r["kpis"] for r in K.ROWS] == [ids[:6], ids[6:]]
    for k in K.KPIS:
        assert k.label and len(k.basis) > 40 and k.drill, k.id
        assert k.unit in {"inr", "pct", "score", "rs"} and k.kind in {"stock", "month", "transition", "pending"}
        assert (k.kind == "pending") == (not k.sql) == bool(k.pending), k.id


def test_every_query_reads_only_scoped_views_and_names_the_callers_bank():
    """43: the app role has no grant on mv_*; the *_scoped views filter by the
    session's bank, and each query also filters on :bank."""
    for k in K.KPIS:
        if not k.sql:
            continue
        read = set(re.findall(r"analytics\.(\w+)", k.sql))
        assert read <= set(K.VIEWS) | {"dim_portfolio_state"}, (k.id, read)
        assert not any(v.startswith("mv_") for v in read), k.id
        assert "bank_id = :bank" in k.sql, k.id
        assert set(read) & set(K.VIEWS) <= set(k.views), (k.id, "declared views must cover what the SQL reads")


def test_money_strings_match_the_frontend_pulse_format():
    """bank/theme/format.ts pulseMoney: crores from ₹1 Cr up, lakhs below, Python grouping."""
    assert K.money(12_345_678_901) == "₹1,234.6 Cr"
    assert K.money(8_520_000) == "₹85.2 L"
    assert K.pct(0.1484) == "14.8%"


@pytest.mark.parametrize("unit,higher,now,prev,trend,up,good", [
    ("pct", False, 0.12, 0.10, "+2.0 pp vs x", True, False),     # GNPA rising: up arrow, bad
    ("pct", True, 0.12, 0.10, "+2.0 pp vs x", True, True),       # cure rising: good
    ("inr", False, 90.0, 100.0, "-10.0% vs x", False, True),     # exposure falling: good
    ("pct", True, 0.1, 0.1, "+0.0 pp vs x", None, None),         # flat
])
def test_trend_direction_and_goodness_are_independent(unit, higher, now, prev, trend, up, good):
    k = K.KpiDef("t", "T", "book", unit, higher, "b" * 50, "d", "stock", (), "x")
    assert K._trend(k, now, prev, "vs x") == (trend, up, good)


def test_no_prior_reading_is_said_not_zeroed():
    k = K.KPIS[0]
    assert K._trend(k, 1.0, None, "vs 31 Aug 2026") == ("no comparable reading vs 31 Aug 2026", None, None)


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()


def test_without_the_analytics_views_every_kpi_says_why_and_shows_no_number(db):
    ov = K.compute_overview(db, DEFAULT_TENANT["bank_id"])
    assert ov.as_of is None and ov.totals == []
    assert all(not k["available"] and k["value"] == "—" and k["reason"] for k in ov.kpis)
    pending = {k["id"] for k in ov.kpis if k["reason"].startswith("definition pending")}
    assert pending == {"visit_to_pay"}
    assert ov.narrative[-1].startswith("Not yet available:")


def _client(db, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[_get_token_payload] = lambda: {"sid": None}
    return TestClient(app)


@pytest.fixture()
def cleanup():
    yield
    app.dependency_overrides.clear()


def _user(db, role: UserRole, **tenant) -> User:
    u = User(email=f"{role.value.lower()}@girivanfinance.test", phone=f"98765{len(role.value):05d}",
             full_name="Test Person", hashed_password="x", role=role, is_active=True, **tenant)
    db.add(u)
    db.commit()
    return u


@pytest.mark.parametrize("role", [UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS])
def test_a_bank_user_gets_the_overview(db, cleanup, role):
    user = _user(db, role, bank_id=DEFAULT_TENANT["bank_id"])
    r = _client(db, user).get("/api/v1/bank/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["kpis"]) == 12 and [row["id"] for row in body["rows"]] == ["book", "outcome"]
    assert body["narrative"]["generated_by"] == "rules"


@pytest.mark.parametrize("role", [UserRole.AGENCY_MANAGER, UserRole.AGENCY_ADMIN, UserRole.FIELD_AGENT])
def test_agency_roles_are_refused(db, cleanup, role):
    user = _user(db, role, **DEFAULT_TENANT)
    assert _client(db, user).get("/api/v1/bank/overview").status_code == 403
