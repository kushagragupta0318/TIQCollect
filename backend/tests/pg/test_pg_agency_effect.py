"""services/bank/agency_effect over the real scoped view (B13b), on the
hand-built book of test_pg_analytics_b: the tenant-bound read, the NULL-due
rule, and a foreign bank seeing nothing."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import database
from app.services.bank.agency_effect import agency_effect, latest_month
from tests.pg.test_pg_analytics_b import A1, B1, B2, L1, _insert, _refresh, book  # noqa: F401 (fixture)


def _effects(eng, bank_id, **kw):
    with eng.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE tiq_app"))
            database._set_tenant(conn, {"user_id": "t", "bank_id": bank_id, "agency_id": None, "scope": "BANK"})
            return agency_effect(Session(bind=conn), bank_id=bank_id, **kw)


def test_the_effect_reads_the_scoped_view_and_skips_an_unknown_due(book):  # noqa: F811
    _refresh(book)
    feb = _effects(book, B1, month_start=date(2025, 2, 1))
    a1 = next(r for r in feb if r["agency_id"] == A1)
    assert (a1["raw_rate"], a1["index"], a1["multiplier"], a1["months_unread"]) == (None, None, 1.0, 1)

    with book.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        _insert(conn, "lending.loan_instalments", bank_id=B1, loan_id=L1, instalment_no=2,
                due_date=date(2025, 2, 20), amount_due=4_000, is_current_schedule=True, source="LEDGER")
    _refresh(book)
    a1 = next(r for r in _effects(book, B1, month_start=date(2025, 2, 1)) if r["agency_id"] == A1)
    assert a1["months_unread"] == 0 and a1["raw_rate"] is not None and a1["n"] > 0
    assert 0.0 <= a1["index"] <= 100.0


def test_another_bank_reads_no_effect(book):  # noqa: F811
    _refresh(book)
    assert _effects(book, B2, month_start=date(2025, 2, 1)) == []


def test_latest_month_is_not_the_still_running_month(book):  # noqa: F811
    """P1 (L1) is still ACTIVE (ended_on=None), so the scorecard view's own
    `months` CTE (v2_0013) generate_series runs it through the CURRENT
    month-in-progress for active-placement tracking — by design, not a bug
    in the view. That row has no placed/visit/payment activity yet (none of
    it happened), so picking it as "the" reporting month, as plain
    `max(month_start)` on the view would, degrades every ratio metric to
    None. latest_month must anchor on the book's last REAL reading
    (mv_portfolio_daily, same anchor exposure/compliance already use), not
    on the view's own forward-looking row."""
    _refresh(book)
    with book.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE tiq_app"))
            database._set_tenant(conn, {"user_id": "t", "bank_id": B1, "agency_id": None, "scope": "BANK"})
            last = latest_month(Session(bind=conn), bank_id=B1)
    assert last == date(2025, 3, 1), last
