"""B13b (v2_0013) on Postgres: each honesty rule of the new views, on a small
hand-built book, plus portfolio_state() against its one Python definition.

The book (bank B1, agencies A1 and A2; bank B2 has nothing):
  L1  read 2025-01-31 CURRENT, 2025-02-28 BUCKET_1, 2025-03-05 -> a counted pair
      (dated in the past: the scorecard bounds open placements by current_date)
  L2  read 2025-01-31, then only 2025-02-18 (10 days short of month-end) -> stale
  L3  read 2025-01-31 only -> missing in February
All three are placed with A1. March is incomplete (latest reading 03-05), so
it forms no pair. The database is this module's own; its CHECK constraints
are dropped so generic filler values insert (they are not under test).
"""
from __future__ import annotations

import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from app.core import database
from app.models.loan import DPDBucket, LoanStatus, portfolio_state
from tests.pg.conftest import drop_database, new_database, run_alembic

B1, B2, A1, A2 = (str(uuid.uuid4()) for _ in range(4))
L1, L2, L3, CUST = (str(uuid.uuid4()) for _ in range(4))
P1, P2, P3 = (str(uuid.uuid4()) for _ in range(3))
CASE1, AGENT1, USER1 = (str(uuid.uuid4()) for _ in range(3))
IST = timezone(timedelta(hours=5, minutes=30))


def _filler(col: sa.Column):
    t = col.type
    if isinstance(t, sa.Uuid):
        return str(uuid.uuid4())
    if isinstance(t, sa.Enum):
        return t.enums[0]
    if isinstance(t, sa.DateTime):
        return datetime(2025, 1, 15, 12, tzinfo=timezone.utc)
    if isinstance(t, sa.Date):
        return date(2025, 1, 15)
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.Numeric, sa.Float)):
        return 1
    if isinstance(t, sa.JSON):
        return {}
    return uuid.uuid4().hex[: min(getattr(t, "length", None) or 20, 10)]


def _insert(conn, name: str, **values):
    table = database.Base.metadata.tables[name]
    row = dict(values)
    for c in table.columns:
        if c.name not in row and not c.nullable and c.server_default is None and c.default is None:
            row[c.name] = _filler(c)
    conn.execute(table.insert().values(**row))


def _history(conn, loan, as_of, bucket, dpd, agency=A1, month_end=False, outstanding=100_000):
    _insert(conn, "lending.loan_dpd_history", loan_id=loan, as_of_date=as_of, bank_id=B1, dpd=dpd,
            dpd_bucket=bucket, loan_status="ACTIVE", overdue_amount=5_000, total_outstanding=outstanding,
            outstanding_principal=outstanding, npa_flag=False, loan_type="PERSONAL", agency_id=agency,
            is_month_end=month_end, source="LEDGER", is_backfill=False, observed_pit=True)


@pytest.fixture(scope="module")
def book():
    from alembic import command
    import app.models  # noqa: F401
    url = new_database("analytics_b")
    run_alembic(url, command.upgrade, "head")
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            for stmt in conn.execute(text(
                    "SELECT format('ALTER TABLE %I.%I DROP CONSTRAINT %I', n.nspname, c.relname, k.conname) "
                    "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE k.contype = 'c' AND k.conislocal AND NOT c.relispartition")).scalars().all():
                conn.execute(text(stmt))
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            for b in (B1, B2):
                _insert(conn, "tenancy.banks", id=b, timezone="Asia/Kolkata")
            for a in (A1, A2):
                _insert(conn, "tenancy.agencies", id=a, bank_id=B1)
            _insert(conn, "lending.customers", id=CUST, bank_id=B1)
            for loan, pl in ((L1, P1), (L2, P2), (L3, P3)):
                _insert(conn, "lending.loans", id=loan, bank_id=B1, customer_id=CUST, loan_type="PERSONAL",
                        branch_code="NONE", npa_since=None)
                _insert(conn, "collections.placements", id=pl, bank_id=B1, agency_id=A1, loan_id=loan,
                        status="ACTIVE", placed_on=date(2025, 1, 5), ended_on=None,
                        exposure_at_placement=100_000, expected_recovery_inr=20_000,
                        sla_first_visit_due=date(2025, 1, 12), dpd_bucket_at_placement="BUCKET_1")
            _history(conn, L1, date(2025, 1, 31), "CURRENT", 0, month_end=True)
            _history(conn, L1, date(2025, 2, 28), "BUCKET_1", 12, month_end=True)
            _history(conn, L1, date(2025, 3, 5), "BUCKET_1", 17)
            _history(conn, L2, date(2025, 1, 31), "CURRENT", 0, month_end=True)
            _history(conn, L2, date(2025, 2, 18), "BUCKET_2", 40)
            _history(conn, L3, date(2025, 1, 31), "BUCKET_1", 5, month_end=True)
            # One case, visits on 2025-01-10 and 2025-01-15 IST; payments at +3 days (the first visit's),
            # +9 days (the SECOND visit's: attributed to the latest prior visit) and one hour BEFORE the
            # first visit (nobody's).
            _insert(conn, "collections.cases", id=CASE1, bank_id=B1, agency_id=A1, loan_id=L1, customer_id=CUST,
                    placement_id=P1, agent_id=AGENT1)
            _insert(conn, "workforce.agents", id=AGENT1, bank_id=B1, agency_id=A1, user_id=USER1, exited_on=None)
            visit_at = datetime(2025, 1, 10, 11, 0, tzinfo=IST)
            _insert(conn, "collections.visits", bank_id=B1, agency_id=A1, case_id=CASE1, agent_id=AGENT1,
                    check_in_time=visit_at, customer_met=True, within_contact_hours=True, geo_verified=True,
                    consent_given=True)
            _insert(conn, "collections.visits", bank_id=B1, agency_id=A1, case_id=CASE1, agent_id=AGENT1,
                    check_in_time=visit_at + timedelta(days=5), customer_met=False, within_contact_hours=True,
                    geo_verified=True, consent_given=None)          # not met: consent was never asked
            for delta, amount in ((timedelta(days=3), 2_000), (timedelta(days=9), 7_000), (timedelta(hours=-1), 500)):
                _insert(conn, "collections.payments", bank_id=B1, agency_id=A1, case_id=CASE1, loan_id=L1,
                        agent_id=AGENT1, amount=amount, mode="CASH", status="VERIFIED",
                        payment_date=visit_at + delta, settlement_offer_id=None)
        _refresh(eng)
        yield eng
    finally:
        eng.dispose()
        drop_database(url)


def _refresh(eng):
    with eng.begin() as conn:
        for mv in ("mv_field_activity_daily", "mv_portfolio_daily", "mv_bucket_transitions_monthly",
                   "mv_agency_scorecard_monthly"):
            conn.execute(text(f"REFRESH MATERIALIZED VIEW analytics.{mv}"))


def _as(eng, sql, ctx=None, role="tiq_app", params=None):
    with eng.connect() as conn:
        with conn.begin():
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            if ctx:
                database._set_tenant(conn, {"user_id": "t", **ctx})
            return conn.execute(text(sql), params or {}).all()


BANK1 = {"bank_id": B1, "agency_id": None, "scope": "BANK"}


def test_portfolio_state_in_sql_is_the_python_definition(pg_engine):
    npa = [None, date(2024, 2, 29), date(2025, 1, 31), date(2025, 9, 1), date(2025, 9, 2)]
    as_of = [date(2025, 2, 28), date(2026, 1, 31), date(2026, 2, 28), date(2026, 9, 1)]
    grid = list(itertools.product([b.value for b in DPDBucket] + ["BOGUS"], [s.value for s in LoanStatus],
                                  npa, as_of))
    with pg_engine.connect() as conn:
        got = [conn.execute(text("SELECT analytics.portfolio_state(:b, :s, :n, :a)"),
                            {"b": b, "s": s, "n": n, "a": a}).scalar() for b, s, n, a in grid]
    assert got == [portfolio_state(b, s, n, a) for b, s, n, a in grid]


def test_a_day_holds_only_the_loans_read_that_day(book):
    rows = _as(book, "SELECT sum(accounts) FROM analytics.portfolio_daily_scoped WHERE as_of_date = '2025-02-18'", BANK1)
    assert rows[0][0] == 1                   # only L2: nothing is carried forward


def test_a_pair_is_counted_stale_or_missing_never_carried(book):
    rows = _as(book, "SELECT from_state, to_state, accounts, excluded_stale_pairs, excluded_missing_pairs "
                     "FROM analytics.bucket_transitions_monthly_scoped WHERE month_end = '2025-02-28' "
                     "ORDER BY from_state, to_state", BANK1)
    got = {(f, t): (a, s, m) for f, t, a, s, m in rows}
    assert got[("CURRENT", "SMA_0")] == (1, 0, 0)           # L1
    assert got[("CURRENT", "SMA_1")] == (0, 1, 0)           # L2, read 10 days before month-end
    assert got[("SMA_0", "NO_READING")] == (0, 0, 1)        # L3
    march = _as(book, "SELECT count(*) FROM analytics.bucket_transitions_monthly_scoped "
                      "WHERE month_end = '2025-03-31'", BANK1)
    assert march[0][0] == 0                                  # March is not complete


def test_the_scoped_views_are_tenant_bound(book):
    views = ("portfolio_daily_scoped", "bucket_transitions_monthly_scoped", "agency_scorecard_monthly_scoped",
             "collections_daily_scoped", "field_activity_daily_scoped", "v_visit_to_pay")
    for ctx, expect_rows in ((None, False), ({"bank_id": B2, "agency_id": None, "scope": "BANK"}, False),
                             ({"bank_id": B1, "agency_id": A2, "scope": "AGENCY"}, False),
                             ({"bank_id": B1, "agency_id": None, "scope": None}, False),   # never widens to the bank
                             ({"bank_id": B1, "agency_id": A1, "scope": None}, False),     # nor to its agency
                             ({"bank_id": B1, "agency_id": A1, "scope": "AGENT"}, False),  # a field agent reads none
                             ({"bank_id": B1, "agency_id": A1, "scope": "AGENCY"}, True), (BANK1, True)):
        counts = {v: _as(book, f"SELECT count(*) FROM analytics.{v}", ctx)[0][0] for v in views}
        if expect_rows:
            assert all(counts[v] > 0 for v in ("portfolio_daily_scoped", "agency_scorecard_monthly_scoped",
                                               "v_visit_to_pay")), (ctx, counts)
        else:
            assert counts == {v: 0 for v in views}, (ctx, counts)


def test_the_app_role_reads_wrappers_never_the_materialized_views(book):
    for mv in ("mv_portfolio_daily", "mv_bucket_transitions_monthly", "mv_agency_scorecard_monthly"):
        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            _as(book, f"SELECT 1 FROM analytics.{mv}", BANK1)
    assert _as(book, "SELECT count(*) FROM analytics.mv_portfolio_daily", role="tiq_jobs")[0][0] > 0


def test_unpriced_visits_read_null_until_a_rate_exists(book):
    sql = ("SELECT field_cost, visits FROM analytics.agency_scorecard_monthly_scoped "
           "WHERE month_start = '2025-01-01' AND visits > 0")
    assert _as(book, sql, BANK1) == [(None, 2)]
    with book.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        _insert(conn, "strategy.cost_rates", bank_id=B1, channel="FIELD_VISIT", unit="PER_ATTEMPT",
                rate_inr=180, valid_from=date(2025, 1, 1), valid_to=None)
    _refresh(book)
    assert _as(book, sql, BANK1) == [(360, 2)]


def test_collectible_due_is_null_when_unread_or_uncovered_never_understated(book):
    def due(month):
        return _as(book, "SELECT collectible_due, collectible_due_unread FROM analytics.agency_scorecard_monthly_scoped "
                         f"WHERE month_start = '{month}' AND active_placements_eom > 0", BANK1)
    with book.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        _insert(conn, "lending.loan_instalments", bank_id=B1, loan_id=L1, instalment_no=1, due_date=date(2025, 1, 20),
                amount_due=4_000, is_current_schedule=True, source="LEDGER")
    _refresh(book)
    assert due("2025-01-01") == [(None, 3)]           # covered, but no placement has a December reading
    assert due("2025-02-01") == [(None, 0)]           # every opening reading exists, but no instalment is due
    with book.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        _insert(conn, "lending.loan_instalments", bank_id=B1, loan_id=L1, instalment_no=2, due_date=date(2025, 2, 20),
                amount_due=4_000, is_current_schedule=True, source="LEDGER")
    _refresh(book)
    assert due("2025-02-01") == [(3 * 5_000 + 4_000, 0)]   # three opening overdues plus the instalment


def test_visit_to_pay_attributes_each_verified_payment_to_the_latest_prior_visit(book):
    rows = _as(book, "SELECT visit_date, paid_within_7d, paid_amount_7d FROM analytics.v_visit_to_pay "
                     "ORDER BY visit_date", BANK1)
    assert rows == [(date(2025, 1, 10), True, 2_000), (date(2025, 1, 15), True, 7_000)]



def test_consent_is_missing_only_on_a_met_visit(book):
    """January: one met visit WITH consent, one not-met visit with none asked: nothing missing. A met
    visit without consent (March) is missing, in the scorecard and in field activity alike."""
    sc = "SELECT consent_missing FROM analytics.agency_scorecard_monthly_scoped WHERE month_start = :m AND visits > 0"
    fa = "SELECT sum(consent_missing_visits) FROM analytics.field_activity_daily_scoped WHERE activity_date = :d"
    assert _as(book, sc, BANK1, params={"m": date(2025, 1, 1)}) == [(0,)]
    assert _as(book, fa, BANK1, params={"d": date(2025, 1, 15)})[0][0] == 0
    with book.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        _insert(conn, "collections.visits", bank_id=B1, agency_id=A1, case_id=CASE1, agent_id=AGENT1,
                check_in_time=datetime(2025, 3, 3, 11, 0, tzinfo=IST), customer_met=True,
                within_contact_hours=True, geo_verified=True, consent_given=None)
    _refresh(book)
    assert _as(book, sc, BANK1, params={"m": date(2025, 3, 1)}) == [(1,)]
    assert _as(book, fa, BANK1, params={"d": date(2025, 3, 3)})[0][0] == 1
