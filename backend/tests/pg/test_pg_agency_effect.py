"""services/bank/agency_effect over the real scoped view (B13b), on the
hand-built book of test_pg_analytics_b: the tenant-bound read, the NULL-due
rule, and a foreign bank seeing nothing."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import database
from app.services.bank.agency_effect import agency_effect
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
