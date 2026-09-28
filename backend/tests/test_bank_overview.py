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


# ── C02: the global filter ──────────────────────────────────────────────────
from datetime import date  # noqa: E402

from app.services.bank.kpi_filter import FilterError, KpiFilter  # noqa: E402


@pytest.mark.parametrize("kw", [dict(period="weekly"), dict(period="custom"), dict(start=date(2026, 9, 1)),
                                dict(period="custom", start=date(2026, 9, 2), end=date(2026, 9, 1)),
                                dict(product="TRACTOR"), dict(bucket="BUCKET_9"), dict(security="MAYBE")])
def test_an_invalid_filter_is_refused(kw):
    with pytest.raises(FilterError):
        KpiFilter(**kw)


@pytest.mark.parametrize("period,window", [
    ("mtd", (date(2026, 9, 1), date(2026, 9, 22))),
    ("l30", (date(2026, 8, 24), date(2026, 9, 22))),
    ("qtd", (date(2026, 7, 1), date(2026, 9, 22))),
    ("fytd", (date(2026, 4, 1), date(2026, 9, 22))),     # the Indian financial year starts in April
])
def test_period_windows_end_on_the_reading(period, window):
    assert KpiFilter(period=period).window(date(2026, 9, 22)) == window
    assert KpiFilter(period="fytd").window(date(2027, 2, 10)) == (date(2026, 4, 1), date(2027, 2, 10))


def test_each_view_is_filtered_only_by_the_dimensions_it_carries():
    f = KpiFilter(geo="g", agency="a", product="HOME", bucket="NPA", security="SECURED")
    clause, params = f.clause("portfolio_daily_scoped")
    assert {"f_geo", "f_agency", "f_product", "f_bucket", "f_security"} == set(params)
    assert "dim_region" in clause and "dim_product" in clause
    clause, params = f.clause("agency_scorecard_monthly_scoped")
    assert set(params) == {"f_geo", "f_agency"} and "loan_type" not in clause
    assert f.unsupported(["agency_scorecard_monthly_scoped"]) == {"product", "bucket", "security"}
    assert KpiFilter(agency="a").unsupported(["collections_daily_scoped"]) == set()
    assert f.clause("bucket_transitions_monthly_scoped", "t")[0].count("t.") == 4   # alias-qualified


def test_every_kpi_declares_views_the_filter_knows():
    from app.services.bank.kpi_filter import VIEW_DIMENSIONS
    assert {v for k in K.KPIS for v in k.views} <= set(VIEW_DIMENSIONS)


def test_the_route_refuses_a_bad_period_and_another_banks_agency(db, cleanup):
    import uuid
    from app.models.tenancy import Agency, Bank
    other = Bank(id=str(uuid.uuid4()), code="OTHERBK", legal_name="Another Finance Ltd", display_name="Another",
                 brand={}, status="ACTIVE", is_demo=True)
    db.add(other)
    db.flush()
    foreign = Agency(id=str(uuid.uuid4()), bank_id=other.id, code="AGY-OTHER", legal_name="Other Agency LLP",
                     contacts=[], status="ACTIVE", is_demo=True)
    db.add(foreign)
    db.commit()
    c = _client(db, _user(db, UserRole.BANK_ADMIN, bank_id=DEFAULT_TENANT["bank_id"]))
    assert c.get("/api/v1/bank/overview?period=custom").status_code == 422
    missing = c.get(f"/api/v1/bank/overview?agency={uuid.uuid4()}")
    theirs = c.get(f"/api/v1/bank/overview?agency={foreign.id}")
    assert missing.status_code == theirs.status_code == 404 and missing.json() == theirs.json()
    assert c.get(f"/api/v1/bank/overview?agency={DEFAULT_TENANT['agency_id']}&product=HOME").status_code == 200


def test_filter_options_are_the_callers_banks_only(db, cleanup):
    c = _client(db, _user(db, UserRole.BANK_ANALYST, bank_id=DEFAULT_TENANT["bank_id"]))
    body = c.get("/api/v1/bank/filters").json()
    assert [p["value"] for p in body["periods"]] == ["mtd", "l30", "qtd", "fytd", "custom"]
    assert {a["value"] for a in body["agencies"]} == {DEFAULT_TENANT["agency_id"]}
    assert len(body["products"]) == 8 and len(body["buckets"]) == 5
