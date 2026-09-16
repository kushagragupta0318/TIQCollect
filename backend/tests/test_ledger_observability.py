"""The willingness-observability channels (2026-09-15, later) — what they must hold to.

Three `CallLog` columns the ledger can now emit — DECLINED as a refusal on
the phone, `duration_seconds`, `verbal_payment_date` — plus the panel
features derived from them and from the payment ledger. Four properties:

  1. OFF IS OFF. With the flags off the random stream is untouched and the
     book is the one every committed artifact was measured on.
  2. The channels obey the point-in-time boundary like everything else
     (event day < t; commitment status from payments before t only).
  3. A commitment's status is DERIVED from money, never stored: kept early
     by an early payment, broken only once due + grace has passed, open in
     between — and a payment at or after as_of cannot keep it.
  4. The realism report gates the new bands only when the channel is on.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.panel import _call_history, _commitment_history, build_panel
from app.ml.simulation.ledger.realism import realism_report

SMALL = LedgerConfig(n_borrowers=200, months=6, seed=5)
ON = LedgerConfig(n_borrowers=200, months=6, seed=5, observe_declines=True,
                  observe_call_duration=True, observe_verbal_commitments=True)
T = 300
LIVE = ["L1", "L2"]
EMI = np.array([1000.0, 1000.0])


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------------------
# 1. Off is off
# ---------------------------------------------------------------------------

def test_flags_off_emit_nothing_new_and_leave_the_stream_alone():
    a = LedgerSimulator(SMALL).run(intercept=-4.1562)
    assert a.calls.duration_seconds.isna().all()
    assert (a.calls.verbal_due_day == -1).all()
    # DECLINED still appears — from the flat not-answered draw, as before.
    assert "DECLINED" in set(a.calls.outcome)
    b = LedgerSimulator(SMALL).run(intercept=-4.1562)
    pd.testing.assert_frame_equal(a.payments, b.payments)
    pd.testing.assert_frame_equal(a.calls, b.calls)


def test_channel_noise_scale_of_one_is_the_identity():
    from dataclasses import replace
    a = LedgerSimulator(ON).run(intercept=-4.1562)
    b = LedgerSimulator(replace(ON, channel_noise_scale=1.0)).run(intercept=-4.1562)
    pd.testing.assert_frame_equal(a.calls, b.calls)
    pd.testing.assert_frame_equal(a.payments, b.payments)


def test_flags_on_emit_the_three_channels_with_the_products_shape():
    led = LedgerSimulator(ON).run(intercept=-4.1562)
    c = led.calls
    ans = c[c.answered]
    assert ans.duration_seconds.notna().all() and (ans.duration_seconds > 0).all()
    assert c[~c.answered].duration_seconds.isna().all()
    assert (c[~c.answered].verbal_due_day == -1).all()
    made = ans[ans.verbal_due_day >= 0]
    assert len(made) > 0
    assert ((made.verbal_due_day - made.day) >= ON.commitment_horizon_lo).all()
    assert ((made.verbal_due_day - made.day) <= ON.commitment_horizon_hi).all()
    # A declined call is not an answered one, and carries no intent.
    dec = c[c.outcome == "DECLINED"]
    assert len(dec) > 0 and (~dec.answered).all() and dec.payment_intent.isna().all()


# ---------------------------------------------------------------------------
# 2/3. Commitment status is derived, point-in-time
# ---------------------------------------------------------------------------

def _calls(rows):
    return pd.DataFrame(rows, columns=["loan_id", "day", "answered", "payment_intent",
                                       "outcome", "duration_seconds", "verbal_due_day"])


def _pays(rows):
    return pd.DataFrame(rows, columns=["loan_id", "payment_day", "amount",
                                       "initial_status", "final_status", "status_effective_day"])


CFG = LedgerConfig()      # grace 2, kept ratio 0.5


def test_commitment_kept_by_money_before_as_of():
    c = _calls([("L1", T - 20, True, True, "ANSWERED", 80.0, T - 12)])
    p = _pays([("L1", T - 15, 600.0, "VERIFIED", "VERIFIED", T - 15)])
    out = _commitment_history(c, p, LIVE, T, EMI, CFG)
    assert out.loc["L1", "last_commit_status"] == "KEPT"
    assert out.loc["L1", "commit_kept_ratio_6m"] == 1.0
    assert out.loc["L1", "days_since_commit_kept"] == 15.0
    assert out.loc["L2", "last_commit_status"] == "NONE"
    assert _nan(out.loc["L2", "commit_kept_ratio_6m"])


def test_commitment_broken_only_after_due_plus_grace():
    c = _calls([("L1", T - 20, True, True, "ANSWERED", 80.0, T - 3)])     # due+grace = T-1 < T
    out = _commitment_history(c, _pays([]), LIVE, T, EMI, CFG)
    assert out.loc["L1", "last_commit_status"] == "BROKEN"
    assert out.loc["L1", "commit_broken_6m"] == 1.0
    c = _calls([("L1", T - 20, True, True, "ANSWERED", 80.0, T - 2)])     # due+grace = T, not yet
    out = _commitment_history(c, _pays([]), LIVE, T, EMI, CFG)
    assert out.loc["L1", "last_commit_status"] == "OPEN"
    assert out.loc["L1", "commit_live_at_asof"] == 1.0


@pytest.mark.parametrize("pay_day", [T, T + 1])
def test_a_payment_at_or_after_as_of_cannot_keep_a_commitment(pay_day):
    c = _calls([("L1", T - 5, True, True, "ANSWERED", 80.0, T + 3)])
    p = _pays([("L1", pay_day, 900.0, "VERIFIED", "VERIFIED", pay_day)])
    out = _commitment_history(c, p, LIVE, T, EMI, CFG)
    assert out.loc["L1", "last_commit_status"] == "OPEN"


def test_money_below_the_kept_ratio_or_outside_the_window_does_not_count():
    c = _calls([("L1", T - 20, True, True, "ANSWERED", 80.0, T - 12)])
    p = _pays([("L1", T - 15, 400.0, "VERIFIED", "VERIFIED", T - 15),      # < 0.5 x EMI
               ("L1", T - 21, 900.0, "VERIFIED", "VERIFIED", T - 21)])     # before the call
    out = _commitment_history(c, p, LIVE, T, EMI, CFG)
    assert out.loc["L1", "last_commit_status"] == "BROKEN"


def test_a_commitment_named_at_or_after_as_of_is_invisible():
    for day in (T, T + 1):
        c = _calls([("L1", day, True, True, "ANSWERED", 80.0, day + 5)])
        out = _commitment_history(c, _pays([]), LIVE, T, EMI, CFG)
        assert out.loc["L1", "last_commit_status"] == "NONE"
        assert out.loc["L1", "commitments_6m"] == 0.0


@pytest.mark.parametrize("day, counted", [(T - 1, True), (T, False), (T + 1, False)])
def test_declines_and_durations_obey_the_boundary(day, counted):
    c = _calls([("L1", day, False, None, "DECLINED", None, -1),
                ("L2", day, True, True, "ANSWERED", 120.0, -1)])
    out = _call_history(c, LIVE, T)
    if counted:
        assert out.loc["L1", "declined_rate_6m"] == 1.0
        assert out.loc["L2", "mean_call_duration_6m"] == 120.0
        assert out.loc["L2", "last_call_duration"] == 120.0
        assert out.loc["L2", "intent_rate_30d"] == 1.0
    else:
        assert _nan(out.loc["L1", "declined_rate_6m"])
        assert _nan(out.loc["L2", "mean_call_duration_6m"])
        assert _nan(out.loc["L2", "intent_rate_30d"])


def test_declined_rate_is_over_reached_calls_only():
    c = _calls([("L1", T - 5, False, None, "NO_ANSWER", None, -1),
                ("L1", T - 4, False, None, "DECLINED", None, -1),
                ("L1", T - 3, True, False, "ANSWERED", 40.0, -1)])
    out = _call_history(c, LIVE, T)
    assert out.loc["L1", "declined_rate_6m"] == 0.5           # 1 declined of 2 reached
    assert out.loc["L1", "days_since_negative_intent"] == 3.0  # answered without intent


# ---------------------------------------------------------------------------
# 4. Realism gates the new bands only when the channel is on
# ---------------------------------------------------------------------------

def test_realism_reports_the_new_bands_only_when_on():
    off = LedgerSimulator(SMALL).run(intercept=-4.1562)
    rep_off = realism_report(off, build_panel(off, SMALL), SMALL)
    names_off = {c["check"] for c in rep_off["checks"]}
    assert "commitment_kept_rate" not in names_off
    on = LedgerSimulator(ON).run(intercept=-4.1562)
    rep_on = realism_report(on, build_panel(on, ON), ON)
    names_on = {c["check"] for c in rep_on["checks"]}
    assert {"declined_share_of_reached", "commitment_share_of_answered",
            "commitment_kept_rate", "median_call_duration_s"} <= names_on
    for c in rep_on["checks"]:
        if c["check"] in ("declined_share_of_reached", "commitment_share_of_answered",
                          "median_call_duration_s"):
            assert c["status"] == "PASS", c


# ---------------------------------------------------------------------------
# 5. The structured disposition (2026-09-15, later still)
# ---------------------------------------------------------------------------

from app.ml.simulation.ledger.panel import _disposition_history  # noqa: E402

DISP = LedgerConfig(n_borrowers=200, months=6, seed=5, observe_declines=True,
                    observe_call_duration=True, observe_verbal_commitments=True,
                    pre_scoring_call_days=3, pre_scoring_call_attempts=3,
                    pre_scoring_until_reached=True, observe_disposition=True,
                    disposition_read_noise=0.30)


def _dcalls(rows):
    return pd.DataFrame(rows, columns=["loan_id", "day", "disposition"])


def _dvisits(rows):
    return pd.DataFrame(rows, columns=["loan_id", "day", "disposition"])


def test_disposition_is_recorded_only_where_someone_was_reached():
    led = LedgerSimulator(DISP).run(intercept=-4.1562)
    c, v = led.calls, led.visits
    assert c[c.answered].disposition.notna().all()
    assert c[~c.answered].disposition.isna().all()
    assert v[v.met].disposition.notna().all()
    assert v[~v.met].disposition.isna().all()
    assert set(c.disposition.dropna()) <= {"WILL_PAY", "MAY_PAY", "NO_COMMITMENT",
                                          "REFUSES", "HARDSHIP", "DISPUTE"}


def test_disposition_is_a_noisy_read_not_the_latent():
    """Two readings of the same borrower on nearby days disagree sometimes,
    and every class carries a spread of true willingness."""
    led = LedgerSimulator(DISP).run(intercept=-4.1562)
    c = led.calls[led.calls.disposition.notna()].sort_values(["loan_id", "day"])
    c["prev"] = c.groupby("loan_id").disposition.shift(1)
    c["gap"] = c.day - c.groupby("loan_id").day.shift(1)
    close = c[(c.gap <= 3) & c.prev.notna()]
    assert len(close) > 50
    assert 0.05 < (close.disposition != close.prev).mean() < 0.8


def test_disposition_off_emits_nothing():
    led = LedgerSimulator(SMALL).run(intercept=-4.1562)
    assert led.calls.disposition.isna().all() and led.visits.disposition.isna().all()


@pytest.mark.parametrize("day, expect", [(T - 1, "WILL_PAY"), (T, "NONE"), (T + 1, "NONE")])
def test_disposition_features_obey_the_boundary(day, expect):
    c = _dcalls([("L1", day, "WILL_PAY")])
    out = _disposition_history(c, _dvisits([]), LIVE, T)
    assert out.loc["L1", "latest_disposition"] == expect
    assert out.loc["L1", "disposition_3d"] == expect
    if expect == "NONE":
        assert _nan(out.loc["L1", "latest_disposition_score"])
        assert _nan(out.loc["L1", "days_since_disposition"])
        assert out.loc["L1", "disposition_count_30d"] == 0.0
    else:
        assert out.loc["L1", "latest_disposition_score"] == 2.0
        assert out.loc["L1", "days_since_disposition"] == 1.0
        assert out.loc["L1", "positive_disposition_rate_30d"] == 1.0
    assert out.loc["L2", "latest_disposition"] == "NONE"


def test_disposition_windows_rates_and_trend():
    c = _dcalls([("L1", T - 40, "REFUSES"),      # outside 30d, inside 90d
                 ("L1", T - 20, "MAY_PAY"),
                 ("L1", T - 2, "WILL_PAY")])
    v = _dvisits([("L1", T - 10, "REFUSES")])
    out = _disposition_history(c, v, LIVE, T)
    assert out.loc["L1", "latest_disposition"] == "WILL_PAY"
    assert out.loc["L1", "disposition_3d"] == "WILL_PAY"
    assert out.loc["L1", "disposition_count_30d"] == 3.0
    assert out.loc["L1", "positive_disposition_rate_30d"] == round(2 / 3, 3)
    assert out.loc["L1", "negative_disposition_rate_30d"] == round(1 / 3, 3)
    # latest 2.0 minus mean of earlier (-2, 1, -2) = -1  -> +3
    assert out.loc["L1", "disposition_trend_90d"] == 3.0
    only = _disposition_history(_dcalls([("L1", T - 5, "MAY_PAY")]), _dvisits([]), LIVE, T)
    assert _nan(only.loc["L1", "disposition_trend_90d"])


def test_a_future_reading_cannot_overwrite_the_latest():
    c = _dcalls([("L1", T - 5, "REFUSES"), ("L1", T + 2, "WILL_PAY")])
    out = _disposition_history(c, _dvisits([]), LIVE, T)
    assert out.loc["L1", "latest_disposition"] == "REFUSES"
    assert out.loc["L1", "latest_disposition_score"] == -2.0


def test_sweep_until_reached_retires_only_on_a_sweep_contact():
    led = LedgerSimulator(DISP).run(intercept=-4.1562)
    c = led.calls.copy(); c["pos"] = c.day % 30
    sweep = c[c.pos >= 27]
    per = sweep.groupby(["loan_id", sweep.day // 30]).size()
    assert per.max() <= 3
    # retries happen: some accounts are attempted more than once in a sweep
    assert (per > 1).mean() > 0.2
    # and an account reached on the first day is attempted again only at the
    # ORDINARY hazard (which never switches off), while an unreached one is
    # swept again almost surely.
    first = sweep[sweep.pos == 27].copy()
    first["cyc"] = first.day // 30
    hit = first.answered | (first.outcome == "DECLINED")
    later = sweep[sweep.pos > 27]
    later_keys = set(zip(later.loan_id, later.day // 30))
    again_reached = np.mean([(r.loan_id, r.cyc) in later_keys for r in first[hit].itertuples()])
    again_missed = np.mean([(r.loan_id, r.cyc) in later_keys for r in first[~hit].itertuples()])
    assert again_reached < 0.35 < 0.85 < again_missed, (again_reached, again_missed)
