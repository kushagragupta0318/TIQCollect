"""services/bank/agency_scorecard over the real scoped view (B13b), the same
hand-built book test_pg_agency_effect.py uses: the tenant-bound read, and a
foreign bank seeing nothing. compute_metrics' own arithmetic is unit-tested
in test_agency_scorecard.py (no DB needed there); this file only proves
fetch_rows/agency_scorecard/leaderboard actually read the real view.

NOT RUN — Docker was down when this was written (2026-09-29), same as 2b's
own test_pg_agency_effect.py at the time it landed. Written against the real
column names and the established _effects()-style connection pattern; run it
before trusting it.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import database
from app.services.bank.agency_scorecard import agency_scorecard, leaderboard
from tests.pg.test_pg_analytics_b import A1, B1, B2, book  # noqa: F401 (fixture)


def _bound_session(eng, bank_id):
    conn = eng.connect()
    trans = conn.begin()
    conn.execute(text("SET LOCAL ROLE tiq_app"))
    database._set_tenant(conn, {"user_id": "t", "bank_id": bank_id, "agency_id": None, "scope": "BANK"})
    return Session(bind=conn), trans, conn


def test_the_scorecard_reads_the_real_view_and_carries_the_performance_index(book):  # noqa: F811
    from tests.pg.test_pg_analytics_b import _refresh
    _refresh(book)
    adb, trans, conn = _bound_session(book, B1)
    try:
        card = agency_scorecard(adb, bank_id=B1, agency_id=A1, month_start=date(2025, 2, 1))
        assert card["agency_id"] == A1
        assert "collection_efficiency" in card
        assert "performance_index" in card   # from agency_effect, not recomputed
        # CI-found (PR#25): the dict itself is None only when the estimator had
        # NOTHING for this window at all — a real dict with index: None (no raw/
        # peer rate to shrink) is the common case and must be guarded separately,
        # or `0.0 <= None` raises TypeError on Python 3 (no total ordering with
        # None, unlike Python 2's fallback).
        pi = card["performance_index"]
        if pi is not None and pi["index"] is not None:
            assert 0.0 <= pi["index"] <= 100.0
    finally:
        trans.rollback()
        conn.close()


def test_a_foreign_bank_sees_an_empty_scorecard_not_someone_elses(book):  # noqa: F811
    from tests.pg.test_pg_analytics_b import _refresh
    _refresh(book)
    adb, trans, conn = _bound_session(book, B2)
    try:
        card = agency_scorecard(adb, bank_id=B2, agency_id=A1, month_start=date(2025, 2, 1))
        assert card["n_rows"] == 0
    finally:
        trans.rollback()
        conn.close()


def test_leaderboard_ranks_every_agency_in_the_bank(book):  # noqa: F811
    from tests.pg.test_pg_analytics_b import _refresh
    _refresh(book)
    adb, trans, conn = _bound_session(book, B1)
    try:
        ranked = leaderboard(adb, bank_id=B1, month_start=date(2025, 2, 1))
        assert isinstance(ranked, list)
        scored = [r for r in ranked if r["index"] is not None]
        assert scored == sorted(scored, key=lambda r: -r["index"])
    finally:
        trans.rollback()
        conn.close()
