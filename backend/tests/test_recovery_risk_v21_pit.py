"""recovery_risk 2.1.0 — the six added features obey the point-in-time boundary.

Every feature reads events with day < t and window [t - W, t). The boundary
is the one thing a feature can get wrong silently, so each of the six is
checked on a hand-built micro-ledger where the answer is known by
construction, at three positions of the same event:

    day t - 1   -> included
    day t       -> EXCLUDED (the upper edge is open)
    day t + 1   -> EXCLUDED (the future)

plus: an outcome-window payment cannot move any of them, and the same input
gives the same output twice. These call the panel's own helpers with explicit
tables, so nothing here depends on what a simulator seed happens to draw.
The adapter side of the same six is held to the panel by
tests/test_ledger_phase3_adapter_equality.py.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.ml.simulation.ledger.panel import (
    _call_history, _payment_history, _visit_history,
)

T = 300
LIVE = ["L1", "L2"]
EMI = np.array([1000.0, 1000.0])


def _pays(rows):
    return pd.DataFrame(rows, columns=["loan_id", "payment_day", "amount",
                                       "initial_status", "final_status",
                                       "status_effective_day"])


def _calls(rows):
    return pd.DataFrame(rows, columns=["loan_id", "day", "answered", "payment_intent"])


def _visits(rows):
    return pd.DataFrame(rows, columns=["loan_id", "day", "agent_id", "met",
                                       "outcome", "default_reason"])


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------------------
# paid_ratio_1m and pay_amount_cv_6m
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("day, expect", [(T - 1, 0.5), (T, 0.0), (T + 1, 0.0)])
def test_paid_ratio_1m_boundary(day, expect):
    p = _pays([("L1", day, 500.0, "VERIFIED", "VERIFIED", day)])
    out = _payment_history(p, LIVE, T, EMI, 30)
    assert out.loc["L1", "paid_ratio_1m"] == expect
    assert out.loc["L2", "paid_ratio_1m"] == 0.0            # nothing paid: 0, not NaN


def test_paid_ratio_1m_window_is_thirty_days_and_clipped():
    p = _pays([("L1", T - 30, 800.0, "VERIFIED", "VERIFIED", T - 30),   # inside [t-30, t)
               ("L1", T - 31, 800.0, "VERIFIED", "VERIFIED", T - 31),   # outside
               ("L2", T - 5, 9000.0, "VERIFIED", "VERIFIED", T - 5)])
    out = _payment_history(p, LIVE, T, EMI, 30)
    assert out.loc["L1", "paid_ratio_1m"] == 0.8
    assert out.loc["L2", "paid_ratio_1m"] == 1.5                     # clip


def test_paid_ratio_1m_ignores_a_payment_reversed_before_as_of():
    p = _pays([("L1", T - 10, 700.0, "VERIFIED", "REVERSED", T - 2)])
    out = _payment_history(p, LIVE, T, EMI, 30)
    assert out.loc["L1", "paid_ratio_1m"] == 0.0
    # ... but the reversal only counts once it has taken effect
    p = _pays([("L1", T - 10, 700.0, "VERIFIED", "REVERSED", T + 5)])
    out = _payment_history(p, LIVE, T, EMI, 30)
    assert out.loc["L1", "paid_ratio_1m"] == 0.7


def test_pay_amount_cv_6m_needs_two_payments_and_is_population_cv():
    one = _pays([("L1", T - 10, 500.0, "VERIFIED", "VERIFIED", T - 10)])
    assert _nan(_payment_history(one, LIVE, T, EMI, 30).loc["L1", "pay_amount_cv_6m"])
    two = _pays([("L1", T - 10, 500.0, "VERIFIED", "VERIFIED", T - 10),
                 ("L1", T - 40, 1500.0, "VERIFIED", "VERIFIED", T - 40)])
    cv = _payment_history(two, LIVE, T, EMI, 30).loc["L1", "pay_amount_cv_6m"]
    # mean 1000, population std 500 -> 0.5 (sample std would give 0.707)
    assert cv == 0.5
    assert _nan(_payment_history(two, LIVE, T, EMI, 30).loc["L2", "pay_amount_cv_6m"])


@pytest.mark.parametrize("day, counted", [(T - 1, True), (T, False), (T + 1, False)])
def test_pay_amount_cv_6m_boundary(day, counted):
    p = _pays([("L1", T - 100, 1000.0, "VERIFIED", "VERIFIED", T - 100),
               ("L1", day, 3000.0, "VERIFIED", "VERIFIED", day)])
    cv = _payment_history(p, LIVE, T, EMI, 30).loc["L1", "pay_amount_cv_6m"]
    assert (cv == 0.5) if counted else _nan(cv)


def test_pay_amount_cv_6m_window_edge_is_180_days():
    p = _pays([("L1", T - 180, 1000.0, "VERIFIED", "VERIFIED", T - 180),   # inside
               ("L1", T - 1, 3000.0, "VERIFIED", "VERIFIED", T - 1)])
    assert _payment_history(p, LIVE, T, EMI, 30).loc["L1", "pay_amount_cv_6m"] == 0.5
    p = _pays([("L1", T - 181, 1000.0, "VERIFIED", "VERIFIED", T - 181),   # outside
               ("L1", T - 1, 3000.0, "VERIFIED", "VERIFIED", T - 1)])
    assert _nan(_payment_history(p, LIVE, T, EMI, 30).loc["L1", "pay_amount_cv_6m"])


# ---------------------------------------------------------------------------
# call_answer_rate_3m, intent_rate_6m, days_since_last_call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("day, counted", [(T - 1, True), (T, False), (T + 1, False)])
def test_call_features_boundary(day, counted):
    c = _calls([("L1", day, True, True)])
    out = _call_history(c, LIVE, T)
    if counted:
        assert out.loc["L1", "call_answer_rate_3m"] == 1.0
        assert out.loc["L1", "intent_rate_6m"] == 1.0
        assert out.loc["L1", "days_since_last_call"] == 1.0
    else:
        assert _nan(out.loc["L1", "call_answer_rate_3m"])
        assert _nan(out.loc["L1", "intent_rate_6m"])
        assert _nan(out.loc["L1", "days_since_last_call"])
    for f in ("call_answer_rate_3m", "intent_rate_6m", "days_since_last_call"):
        assert _nan(out.loc["L2", f])                 # never called: abstain


def test_call_answer_rate_3m_is_answered_over_attempted_in_ninety_days():
    c = _calls([("L1", T - 90, True, False),     # inside [t-90, t)
                ("L1", T - 50, False, None),
                ("L1", T - 10, False, None),
                ("L1", T - 91, True, True)])     # outside the 3m window
    out = _call_history(c, LIVE, T)
    assert out.loc["L1", "call_answer_rate_3m"] == round(1 / 3, 3)
    assert out.loc["L1", "days_since_last_call"] == 10.0   # attempt, answered or not


def test_intent_rate_6m_is_over_answered_calls_only():
    c = _calls([("L1", T - 20, True, True),
                ("L1", T - 40, True, False),
                ("L1", T - 60, False, None),      # unanswered: not in the denominator
                ("L1", T - 181, True, True)])     # outside the 6m window
    out = _call_history(c, LIVE, T)
    assert out.loc["L1", "intent_rate_6m"] == 0.5
    unanswered_only = _calls([("L1", T - 20, False, None)])
    assert _nan(_call_history(unanswered_only, LIVE, T).loc["L1", "intent_rate_6m"])
    assert _call_history(unanswered_only, LIVE, T).loc["L1", "call_answer_rate_3m"] == 0.0


# ---------------------------------------------------------------------------
# last_visit_outcome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("day, expect", [(T - 1, "RTP"), (T, "NONE"), (T + 1, "NONE")])
def test_last_visit_outcome_boundary(day, expect):
    v = _visits([("L1", day, "A1", True, "RTP", None)])
    out = _visit_history(v, LIVE, T)
    assert out.loc["L1", "last_visit_outcome"] == expect
    assert out.loc["L2", "last_visit_outcome"] == "NONE"


def test_last_visit_outcome_is_the_newest_visit_met_or_not_at_any_age():
    v = _visits([("L1", T - 200, "A1", True, "PTP", None),
                 ("L1", T - 3, "A1", False, "NOT_AVAILABLE", None),
                 ("L2", T - 400, "A1", True, "DISPUTE", None)])
    out = _visit_history(v, LIVE, T)
    assert out.loc["L1", "last_visit_outcome"] == "NOT_AVAILABLE"
    assert out.loc["L2", "last_visit_outcome"] == "DISPUTE"          # older than any window


def test_a_future_visit_cannot_overwrite_the_last_outcome():
    v = _visits([("L1", T - 3, "A1", True, "PTP", None),
                 ("L1", T + 3, "A1", True, "RTP", None)])
    assert _visit_history(v, LIVE, T).loc["L1", "last_visit_outcome"] == "PTP"


# ---------------------------------------------------------------------------
# The outcome window cannot reach any of them; determinism
# ---------------------------------------------------------------------------

def test_an_outcome_window_payment_moves_none_of_the_six():
    base = _pays([("L1", T - 10, 500.0, "VERIFIED", "VERIFIED", T - 10),
                  ("L1", T - 40, 500.0, "VERIFIED", "VERIFIED", T - 40)])
    future = pd.concat([base, _pays([("L1", T + 15, 5000.0, "VERIFIED", "VERIFIED", T + 15)])])
    a = _payment_history(base, LIVE, T, EMI, 30)
    b = _payment_history(future, LIVE, T, EMI, 30)
    pd.testing.assert_frame_equal(a, b)
    c = _calls([("L1", T - 5, True, True)])
    cf = pd.concat([c, _calls([("L1", T + 2, False, None)])])
    pd.testing.assert_frame_equal(_call_history(c, LIVE, T), _call_history(cf, LIVE, T))


def test_the_six_are_deterministic():
    p = _pays([("L1", T - 10, 500.0, "VERIFIED", "VERIFIED", T - 10),
               ("L1", T - 70, 900.0, "VERIFIED", "VERIFIED", T - 70)])
    c = _calls([("L1", T - 5, True, True), ("L1", T - 9, False, None)])
    v = _visits([("L1", T - 2, "A1", True, "REVISIT", None)])
    for fn, arg in ((_payment_history, (p, LIVE, T, EMI, 30)),
                    (_call_history, (c, LIVE, T)),
                    (_visit_history, (v, LIVE, T))):
        pd.testing.assert_frame_equal(fn(*arg), fn(*arg))


def test_the_spec_names_exactly_these_six_beyond_2_0_0():
    from app.ml.pipeline.config import RECOVERY_RISK_V2, RECOVERY_RISK_V21

    added = set(RECOVERY_RISK_V21.all_features) - set(RECOVERY_RISK_V2.all_features)
    assert added == {"paid_ratio_1m", "pay_amount_cv_6m", "call_answer_rate_3m",
                     "intent_rate_6m", "days_since_last_call", "last_visit_outcome"}
    assert RECOVERY_RISK_V21.version == "2.1.0"
    assert RECOVERY_RISK_V21.gates == RECOVERY_RISK_V2.gates
    assert RECOVERY_RISK_V21.target == RECOVERY_RISK_V2.target
    for f in added - {"last_visit_outcome"}:
        assert f in RECOVERY_RISK_V21.expected_sign
