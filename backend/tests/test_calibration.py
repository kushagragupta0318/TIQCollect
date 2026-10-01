"""E04: calibrate() ties history -> backtest -> honesty stamp (no DB).

A fake session feeds read_panel real-shaped rows (state already as the SQL fn would
give), then the REAL backtest runs on the built panel. What this holds: a coverage
figure comes back stamped synthetic; the stamp travels in as_dict; and a book too
short to hold a fit+scoring window abstains rather than returning a number.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import AppException, ErrorCode
from app.strategy import calibration as C
from app.strategy.honesty import STAMP_FIELDS

BANK = "bank-1"


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows


class _FakeSession:
    def __init__(self, rows): self.rows = rows
    def execute(self, _sql, params):
        return _FakeResult(self.rows)


def _book(n_loans: int, n_months: int) -> list[dict]:
    """A book with heavy two-way movement: half the loans oscillate CURRENT<->SMA_0
    each month, so every month has many deteriorations AND improvements — enough
    for estimate_shock_sigma (needs >=5 of each per segment)."""
    months = [date(2026, (mo % 12) + 1, 28) for mo in range(n_months)]
    rows = []
    for li in range(n_loans):
        for mi, m in enumerate(months):
            # loans 0..n/2 oscillate by month parity; the rest hold CURRENT.
            if li < n_loans // 2:
                state = "SMA_0" if (li + mi) % 2 == 0 else "CURRENT"
            else:
                state = "CURRENT"
            rows.append({"loan_id": f"L{li}", "as_of_date": m, "state": state,
                         "total_outstanding": 100000.0, "loan_type": "PERSONAL", "region_id": "WEST"})
    return rows


def _thin_book(n_months: int) -> list[dict]:
    """A near-static book: one loan holds CURRENT. No movement to estimate
    volatility from — calibrate must abstain, not raise."""
    months = [date(2026, (mo % 12) + 1, 28) for mo in range(n_months)]
    return [{"loan_id": "L0", "as_of_date": m, "state": "CURRENT",
             "total_outstanding": 100000.0, "loan_type": "PERSONAL", "region_id": "WEST"} for m in months]


def test_calibrate_returns_a_coverage_figure_stamped_synthetic():
    reading = C.calibrate(_FakeSession(_book(60, 10)), BANK, n_paths=40, seed=1)
    r = reading.report
    assert 0.0 <= r.coverage <= 1.0 and 0.0 < r.nominal <= 1.0
    assert r.horizon_months >= 2
    assert reading.stamp.synthetic is True
    assert reading.calibrated == r.passes()


def test_the_stamp_travels_in_the_result_dict():
    d = C.calibrate(_FakeSession(_book(50, 9)), BANK, n_paths=30, seed=2).as_dict()
    assert set(d) >= STAMP_FIELDS
    assert "coverage" in d and "passes" in d and "basis" in d
    assert d["synthetic"] is True
    assert "SYNTHETIC:" in d["text"]
    assert "loans" in d["basis"] and "coverage over" in d["basis"]


def test_a_book_too_short_to_fit_and_score_abstains():
    with pytest.raises(AppException) as e:
        C.calibrate(_FakeSession(_book(40, C.MIN_CALIBRATION_MONTHS - 1)), BANK, n_paths=20)
    assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY


def test_a_book_too_sparse_to_estimate_volatility_abstains_not_raises():
    """A long but static book cannot estimate shock volatility; calibrate must
    abstain with a reason, not let the ValueError 500 the page."""
    with pytest.raises(AppException) as e:
        C.calibrate(_FakeSession(_thin_book(10)), BANK, n_paths=20)
    assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY
    assert "too sparse to calibrate" in e.value.detail


def test_the_horizon_is_sized_to_the_panel():
    # 7 months -> horizon capped at months-3 = 4, never the default 6.
    reading = C.calibrate(_FakeSession(_book(50, 7)), BANK, n_paths=20, seed=3)
    assert reading.report.horizon_months == 4
    # a long panel honours the requested default
    reading = C.calibrate(_FakeSession(_book(50, 14)), BANK, horizon_months=6, n_paths=20, seed=3)
    assert reading.report.horizon_months == 6


def test_data_version_defaults_to_the_latest_month_end():
    reading = C.calibrate(_FakeSession(_book(50, 9)), BANK, n_paths=20, seed=4)
    assert reading.stamp.data_version == reading.month_ends[-1].isoformat()
