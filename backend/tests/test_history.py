"""E04 input: HistoricalPanel from loan_dpd_history (app/strategy/history.py).

No DB: a fake session returns rows whose `state` is already what the SQL function
analytics.portfolio_state would give, so this tests the pivot and panel-building,
not the SQL (that belongs in tests/pg against the real function). What it holds:
a bank too short abstains; month-end rows pivot into (loan x month) state indices
with -1 for unobserved; balances follow; segment grain is (loan_type, region_id);
an UNKNOWN state is left unobserved, not mapped to a real one.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.core.errors import AppException, ErrorCode
from app.strategy import history as Hst
from app.strategy.states import CURRENT, N_STATES, NPA_SUB, SMA_0, STATE_INDEX, STATES

BANK = "bank-1"


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows


class _FakeSession:
    def __init__(self, rows): self.rows, self.params = rows, None
    def execute(self, _sql, params):
        self.params = params
        return _FakeResult(self.rows)


def _row(loan: str, m: date, state: str, outstanding: float = 1000.0,
         loan_type: str = "PERSONAL", region: str = "WEST") -> dict:
    return {"loan_id": loan, "as_of_date": m, "state": state,
            "total_outstanding": outstanding, "loan_type": loan_type, "region_id": region}


def _months(n: int) -> list[date]:
    return [date(2026, mo, 28) for mo in range(1, n + 1)]


def _one_loan_series(loan: str, states: list[str], **kw) -> list[dict]:
    return [_row(loan, m, s, **kw) for m, s in zip(_months(len(states)), states)]


def test_a_bank_with_too_few_month_ends_abstains():
    rows = _one_loan_series("L1", ["CURRENT"] * (Hst.MIN_BANK_MONTHS - 1))
    with pytest.raises(AppException) as e:
        Hst.read_panel(_FakeSession(rows), BANK)
    assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY
    # empty book abstains too
    with pytest.raises(AppException):
        Hst.read_panel(_FakeSession([]), BANK)


def test_month_end_rows_pivot_into_a_state_matrix_with_unobserved_as_minus_one():
    n = Hst.MIN_BANK_MONTHS
    rows = _one_loan_series("L1", ["CURRENT", "SMA_0", "SMA_0", "CURRENT", "CURRENT", "SMA_0"][:n])
    # a second loan that only appears in the last 3 months
    rows += [_row("L2", m, "CURRENT") for m in _months(n)[-3:]]
    reading = Hst.read_panel(_FakeSession(rows), BANK)
    assert reading.loans == 2 and reading.months == n
    panel = reading.panel
    # L1's series is fully observed; L2's first (n-3) months are -1
    l1 = panel.state[0]
    assert STATES[l1[0]] == "CURRENT" and STATES[l1[1]] == "SMA_0"
    l2 = panel.state[1]
    assert (l2[: n - 3] == -1).all() and l2[-1] == CURRENT


def test_balances_follow_the_rows():
    rows = _one_loan_series("L1", ["CURRENT"] * Hst.MIN_BANK_MONTHS, outstanding=50000.0)
    panel = Hst.read_panel(_FakeSession(rows), BANK).panel
    assert (panel.balance[0] == 50000.0).all()


def test_segment_grain_is_loan_type_and_region():
    n = Hst.MIN_BANK_MONTHS
    rows = _one_loan_series("L1", ["CURRENT"] * n, loan_type="PERSONAL", region="WEST")
    rows += _one_loan_series("L2", ["CURRENT"] * n, loan_type="GOLD", region="EAST")
    panel = Hst.read_panel(_FakeSession(rows), BANK).panel
    assert set(panel.segment_keys) == {Hst.segment_key("PERSONAL", "WEST"), Hst.segment_key("GOLD", "EAST")}
    # each loan maps to its own segment
    assert panel.segment[0] != panel.segment[1]


def test_an_unknown_state_is_left_unobserved_not_mapped_to_a_real_state():
    n = Hst.MIN_BANK_MONTHS
    series = ["CURRENT"] * (n - 1) + ["UNKNOWN"]       # the SQL fn's data-defect marker
    rows = _one_loan_series("L1", series)
    panel = Hst.read_panel(_FakeSession(rows), BANK).panel
    assert panel.state[0, -1] == -1, "UNKNOWN must be unobserved, not a real state index"
    assert panel.state[0, 0] == CURRENT


def test_the_reading_is_bank_scoped_and_window_capped():
    s = _FakeSession(_one_loan_series("L1", ["CURRENT"] * Hst.MIN_BANK_MONTHS))
    Hst.read_panel(s, BANK, as_of=date(2026, 6, 30))
    assert s.params["bank"] == BANK and s.params["as_of"] == date(2026, 6, 30)
    # only the most recent `months` are kept
    long = _one_loan_series("L1", ["CURRENT"] * 12)
    r = Hst.read_panel(_FakeSession(long), BANK, months=7)
    assert r.months == 7 and r.month_ends[-1] == date(2026, 12, 28)


def test_the_panel_is_marked_synthetic_when_asked():
    rows = _one_loan_series("L1", ["CURRENT"] * Hst.MIN_BANK_MONTHS)
    assert Hst.read_panel(_FakeSession(rows), BANK, synthetic=True).panel.synthetic is True
    assert Hst.read_panel(_FakeSession(rows), BANK).panel.synthetic is False
