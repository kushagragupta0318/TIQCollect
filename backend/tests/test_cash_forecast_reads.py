"""E06 cash forecast — the DB reads (app/strategy/cash_forecast.py).

Same convention as test_history.py / test_transitions.py: a fake session
returns canned rows for the raw SQL (schema-qualified table names inside a
`text()` query do not go through SQLAlchemy's schema_translate_map, so a real
SQLite engine cannot run them against `make_engine()`'s mapped schemas — the
SQL itself is covered in tests/pg against real Postgres). This tests the
bucketing, scoping and abstain logic: everything these functions actually
decide beyond "run the query".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.errors import AppException, ErrorCode
from app.strategy import cash_forecast as CF

AS_OF = date(2026, 10, 7)
BANK = "bank-1"
OTHER_BANK = "bank-2"


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def mappings(self):
        return self


class _FakeSession:
    """Routes a query by the identity of the `text()` object (each read
    function in cash_forecast.py has exactly one module-level SQL constant),
    so one fake session can stand in for build_cash_forecast's several reads."""

    def __init__(self, table: dict):
        self.table = table
        self.calls: list[tuple] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))
        return _FakeResult(self.table.get(sql, []))


def _dt(days_before: date, offset_days: int = 0) -> datetime:
    return datetime.combine(days_before, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=offset_days)


# ── read_weekly_payments ─────────────────────────────────────────────────────

def test_abstains_on_no_payments_at_all():
    s = _FakeSession({CF._PAYMENTS_SQL: []})
    with pytest.raises(AppException) as e:
        CF.read_weekly_payments(s, BANK, as_of=AS_OF)
    assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY


def test_abstains_under_the_minimum_weeks():
    rows = [(AS_OF - timedelta(weeks=w, days=-1), 1000.0) for w in range(CF.MIN_WEEKS_HISTORY - 1)]
    s = _FakeSession({CF._PAYMENTS_SQL: rows})
    with pytest.raises(AppException) as e:
        CF.read_weekly_payments(s, BANK, as_of=AS_OF)
    assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY


def test_buckets_into_as_of_anchored_weeks_in_chronological_order():
    rows = [
        (AS_OF - timedelta(days=1), 1000.0), (AS_OF - timedelta(days=3), 500.0),   # week 0 (most recent)
        (AS_OF - timedelta(days=10), 777.0),                                      # week 1
    ]
    # pad to the minimum so this is a valid, non-abstaining read
    rows += [(AS_OF - timedelta(days=7 * w + 1), 100.0) for w in range(2, CF.MIN_WEEKS_HISTORY)]
    s = _FakeSession({CF._PAYMENTS_SQL: rows})
    history = CF.read_weekly_payments(s, BANK, as_of=AS_OF)
    assert history.amounts[-1] == pytest.approx(1500.0)     # most recent week sorts last (chronological)
    assert history.amounts[-2] == pytest.approx(777.0)
    assert history.week_starts[-1] == AS_OF - timedelta(weeks=1)
    assert s.calls[0][1]["bank"] == BANK   # queried with the caller's own bank id


def test_a_row_outside_the_window_is_dropped_not_bucketed_negative():
    # a payment from before `start` would, if it leaked through, bucket past
    # weeks_back; defensive bound, since the SQL's own WHERE should exclude it.
    rows = [(AS_OF - timedelta(weeks=CF.HISTORY_WEEKS_BACK + 5), 50000.0)]
    rows += [(AS_OF - timedelta(days=7 * w + 1), 100.0) for w in range(CF.MIN_WEEKS_HISTORY)]
    s = _FakeSession({CF._PAYMENTS_SQL: rows})
    history = CF.read_weekly_payments(s, BANK, as_of=AS_OF)
    assert history.amounts.sum() == pytest.approx(100.0 * CF.MIN_WEEKS_HISTORY)


# ── PTP schedule + honor rate ────────────────────────────────────────────────

def test_ptp_schedule_buckets_by_week_and_clips_to_the_last_week():
    rows = [
        (AS_OF + timedelta(days=3), 2000.0),          # week 0
        (AS_OF + timedelta(days=10), 3000.0),         # week 1
        (AS_OF + timedelta(weeks=200), 999.0),        # far beyond horizon -> clipped into the last week
    ]
    s = _FakeSession({CF._PTP_SCHEDULE_SQL: rows})
    scheduled = CF.read_ptp_schedule(s, BANK, as_of=AS_OF, horizon_weeks=CF.HORIZON_WEEKS)
    assert scheduled.shape == (CF.HORIZON_WEEKS,)
    assert scheduled[0] == pytest.approx(2000.0)
    assert scheduled[1] == pytest.approx(3000.0)
    assert scheduled[-1] == pytest.approx(999.0)
    assert s.calls[0][1]["bank"] == BANK


def test_ptp_honor_rate_is_rupee_weighted():
    rows = [
        ("HONORED", 1000.0, 1000.0),
        ("PARTIALLY_HONORED", 1000.0, 400.0),
        ("BROKEN", 1000.0, 0.0),
    ]
    s = _FakeSession({CF._PTP_HONOR_SQL: rows})
    rate, n = CF.read_ptp_honor_rate(s, BANK, as_of=AS_OF)
    assert n == 3
    assert rate == pytest.approx((1000.0 + 400.0 + 0.0) / 3000.0)


def test_ptp_honor_rate_is_none_with_no_resolved_history():
    s = _FakeSession({CF._PTP_HONOR_SQL: []})
    rate, n = CF.read_ptp_honor_rate(s, BANK, as_of=AS_OF)
    assert rate is None and n == 0


# ── recovery_risk leg ────────────────────────────────────────────────────────
# The SQL itself excludes PTP'd loans and keeps only each loan's latest
# prediction (tested against real Postgres in tests/pg); here the row shape
# is already "the latest prediction per loan, with no active PTP", and what
# this function owns is the money math and the de-duplication fallback.

def test_recovery_risk_monetises_one_minus_probability_times_overdue():
    rows = [("loan-1", 0.3, 8000.0)]   # P(pay) = 0.7
    s = _FakeSession({CF._RECOVERY_RISK_SQL: rows})
    total, n = CF.read_recovery_risk_next_cycle(s, BANK, as_of=AS_OF)
    assert n == 1
    assert total == pytest.approx(0.7 * 8000.0)


def test_recovery_risk_keeps_only_the_first_row_seen_per_loan():
    # newest-first ordering is the SQL's job; this proves the de-dup itself
    # keeps the FIRST row per loan_id and ignores a repeat.
    rows = [("loan-1", 0.2, 10000.0), ("loan-1", 0.9, 10000.0)]
    s = _FakeSession({CF._RECOVERY_RISK_SQL: rows})
    total, n = CF.read_recovery_risk_next_cycle(s, BANK, as_of=AS_OF)
    assert n == 1
    assert total == pytest.approx(0.8 * 10000.0)


def test_recovery_risk_is_zero_not_an_abstain_with_no_rows():
    s = _FakeSession({CF._RECOVERY_RISK_SQL: []})
    total, n = CF.read_recovery_risk_next_cycle(s, BANK, as_of=AS_OF)
    assert total == 0.0 and n == 0


# ── end-to-end build_cash_forecast on a small, complete book ────────────────

def test_build_cash_forecast_end_to_end():
    base = AS_OF
    payment_rows = [
        (base - timedelta(weeks=w) + timedelta(days=2),
         5000.0 + 40.0 * w + (200.0 if w % 3 == 0 else -100.0))
        for w in range(1, 26)   # 25 weeks: enough for a forecast AND >= 1 backtest fold
    ]
    ptp_schedule_rows = [(AS_OF + timedelta(days=5), 3000.0)]
    ptp_honor_rows = [("HONORED", 2000.0, 2000.0)]
    recovery_rows = [("loan-2", 0.4, 6000.0)]

    table = {
        CF._PAYMENTS_SQL: payment_rows,
        CF._PTP_SCHEDULE_SQL: ptp_schedule_rows,
        CF._PTP_HONOR_SQL: ptp_honor_rows,
        CF._RECOVERY_RISK_SQL: recovery_rows,
    }
    s = _FakeSession(table)
    run = CF.build_cash_forecast(s, BANK, as_of=AS_OF)

    assert len(run.week_starts) == CF.HORIZON_WEEKS == len(run.p50)
    assert run.week_starts[0] == AS_OF
    assert all(p >= 0 for p in run.p10)
    assert all(run.p10[i] <= run.p50[i] <= run.p90[i] for i in range(CF.HORIZON_WEEKS))
    assert run.history_weeks == 25
    assert run.ptp_honor_rate == pytest.approx(1.0)
    assert run.recovery_informed_loans == 1
    assert run.recovery_informed_total == pytest.approx(0.6 * 6000.0)
    assert run.backtest.n_folds >= 1
    # every read was made with the caller's own bank id, not a default
    assert all(params.get("bank") == BANK for _sql, params in s.calls)
