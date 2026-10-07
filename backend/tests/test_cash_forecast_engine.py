"""E06 cash forecast — the pure engine (app/strategy/cash_forecast.py). No DB:
Holt's linear trend, the residual bands, the bottom-up/top-down reconciliation,
and the rolling-origin backtest, each tested against hand-worked numpy series.
The DB reads and the endpoint are covered in test_cash_forecast_reads.py and
test_cash_forecast_api.py.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.strategy import cash_forecast as CF


def test_holt_fit_recovers_a_pure_linear_trend():
    # y = 100, 110, 120, ... — a perfect trend has a smoothing constant that
    # drives one-step SSE to ~0 (alpha near 1 reproduces the trend exactly
    # after the first couple of points).
    y = np.array([100.0 + 10.0 * i for i in range(12)])
    level, trend, residuals, alpha, beta = CF.fit_holt(y)
    assert trend == pytest.approx(10.0, abs=1.0)
    assert np.sum(residuals[2:] ** 2) < 50.0  # near-zero once the filter has settled


def test_forecast_holt_is_floored_at_zero():
    # A hard downward trend extrapolated 13 weeks out must not go negative.
    point = CF.forecast_holt(level=100.0, trend=-50.0, horizon=13)
    assert point.shape == (13,)
    assert (point >= 0.0).all()
    assert point[-1] == 0.0  # by week 13 the linear extrapolation is deep negative


def test_forecast_holt_is_monotone_in_horizon_for_a_flat_series():
    point = CF.forecast_holt(level=1000.0, trend=0.0, horizon=13)
    assert np.allclose(point, 1000.0)


def test_residual_bands_are_ordered_and_widen_with_horizon():
    residuals = np.array([-50.0, -10.0, 0.0, 5.0, 40.0, -5.0, 10.0, -20.0])
    point = np.full(13, 500.0)
    bands = CF.residual_bands(residuals, point)
    assert (bands["p10"] <= bands["p50"]).all()
    assert (bands["p50"] <= bands["p90"]).all()
    # sqrt(h) growth: the width at week 13 must exceed the width at week 1.
    width1 = bands["p90"][0] - bands["p10"][0]
    width13 = bands["p90"][-1] - bands["p10"][-1]
    assert width13 > width1


def test_residual_bands_never_go_negative():
    residuals = np.array([-900.0, -800.0, -700.0, 100.0, 200.0])
    point = np.full(13, 50.0)  # a small point forecast, large negative residuals
    bands = CF.residual_bands(residuals, point)
    assert (bands["p10"] >= 0.0).all()
    assert (bands["p50"] >= 0.0).all()
    assert (bands["p90"] >= 0.0).all()


def test_reconcile_blends_equally_when_bottom_up_has_signal():
    bottom_up = np.full(13, 200.0)
    top_down = np.full(13, 600.0)
    out = CF.reconcile(bottom_up, top_down)
    assert np.allclose(out, 400.0)


def test_reconcile_falls_back_to_top_down_alone_when_bottom_up_is_all_zero():
    bottom_up = np.zeros(13)
    top_down = np.full(13, 600.0)
    out = CF.reconcile(bottom_up, top_down)
    assert np.allclose(out, top_down)


def test_backtest_reports_no_folds_on_a_short_series():
    y = np.full(CF.MIN_WEEKS_HISTORY, 500.0)  # too short for even one fold
    result = CF.rolling_origin_backtest(y, horizon=CF.HORIZON_WEEKS)
    assert result.n_folds == 0
    assert result.mape is None
    assert result.calibrated is False
    assert "fold" in result.reason


def test_backtest_calibrates_on_a_flat_predictable_series():
    # A long, perfectly flat series: Holt forecasts the same constant forward,
    # every fold scores MAPE ~0, well under the ceiling.
    y = np.full(CF.MIN_WEEKS_HISTORY + CF.HORIZON_WEEKS + 20, 1000.0)
    result = CF.rolling_origin_backtest(y, horizon=CF.HORIZON_WEEKS, fold_step=4)
    assert result.n_folds >= CF.MIN_BACKTEST_FOLDS
    assert result.mape == pytest.approx(0.0, abs=1e-6)
    assert result.calibrated is True


def test_backtest_does_not_calibrate_on_a_wildly_erratic_series():
    rng = np.random.default_rng(0)
    y = rng.uniform(0, 5000, size=CF.MIN_WEEKS_HISTORY + CF.HORIZON_WEEKS + 20)
    result = CF.rolling_origin_backtest(y, horizon=CF.HORIZON_WEEKS, fold_step=4)
    assert result.n_folds >= CF.MIN_BACKTEST_FOLDS
    assert result.calibrated is False
    assert result.mape > CF.CALIBRATION_MAPE_CEILING


# ── residual_bands: p50 must never drift off `point` (2026-10-07 bug) ──────

def test_p50_is_always_exactly_point_never_shifted_by_the_residual_median():
    # A heavily negative-skewed residual distribution (what a reporting-lag
    # tail produces) used to drag p50 below `point`, then — scaled by a
    # growing sqrt(h) — down to a flat 0. p50 must now equal `point` exactly,
    # at every horizon step, regardless of how skewed the residuals are.
    residuals = np.array([-900.0, -850.0, -800.0, -700.0, 10.0, 20.0])  # median << 0
    point = np.array([500.0, 500.0, 500.0, 500.0, 500.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    bands = CF.residual_bands(residuals, point)
    assert np.array_equal(bands["p50"], point)


def test_p90_keeps_widening_even_when_residuals_skew_negative():
    # The companion symptom reported live: even p90 flatlined to 0 by week 6,
    # because the SAME growing negative shift that broke p50 was also
    # applied to p90. p90 must now be point PLUS a non-negative, growing
    # spread, so it can never fall below point and never collapses to 0
    # while point itself is positive.
    residuals = np.array([-900.0, -850.0, -800.0, -700.0, 10.0, 20.0])
    point = np.full(13, 500.0)
    bands = CF.residual_bands(residuals, point)
    assert (bands["p90"] >= point).all()
    assert np.all(np.diff(bands["p90"]) >= 0), "p90 must widen monotonically with horizon"


def test_residual_bands_start_h_continues_the_growth_rather_than_resetting():
    residuals = np.array([-50.0, -10.0, 0.0, 5.0, 40.0, -5.0, 10.0, -20.0])
    point = np.full(5, 500.0)
    from_one = CF.residual_bands(residuals, point, start_h=1)
    # The same 5 points, but as if they were steps 3..7 of a longer horizon —
    # the band at step k (k=3..7) must be at least as wide as it was at
    # step k when counted from 1 (sqrt is increasing), not reset to sqrt(1).
    from_three = CF.residual_bands(residuals, point, start_h=3)
    assert (from_three["p90"] - from_three["p50"] >= from_one["p90"] - from_one["p50"]).all()
    assert (from_one["p50"] - from_one["p10"] <= from_three["p50"] - from_three["p10"]).all()


# ── trim_trailing_reporting_lag ─────────────────────────────────────────────

def test_trims_only_a_trailing_run_of_exact_zeros():
    # Long enough that the MIN_WEEKS_HISTORY floor isn't what's limiting the
    # trim (trim_trailing_reporting_lag is only ever called on a series that
    # already cleared that floor).
    y = np.array([100.0, 200.0, 150.0, 0.0, 300.0, 400.0, 350.0, 420.0, 390.0, 410.0, 0.0, 0.0])
    trimmed, lag = CF.trim_trailing_reporting_lag(y)
    assert lag == 2
    assert trimmed.tolist() == y[:-2].tolist()


def test_does_not_trim_when_the_most_recent_week_is_nonzero():
    y = np.array([0.0, 0.0, 100.0, 200.0, 300.0, 250.0, 280.0, 310.0, 260.0, 300.0])
    trimmed, lag = CF.trim_trailing_reporting_lag(y)
    assert lag == 0
    assert trimmed.tolist() == y.tolist()


def test_trim_is_capped_at_max_reporting_lag_weeks():
    y = np.concatenate([np.full(CF.MIN_WEEKS_HISTORY + CF.MAX_REPORTING_LAG_WEEKS + 5, 100.0),
                        np.zeros(CF.MAX_REPORTING_LAG_WEEKS + 3)])
    trimmed, lag = CF.trim_trailing_reporting_lag(y)
    assert lag == CF.MAX_REPORTING_LAG_WEEKS
    assert trimmed[-1] == 0.0, "a lag longer than the cap leaves some real zero weeks in the fit"


def test_trim_never_goes_below_min_weeks_history():
    y = np.zeros(CF.MIN_WEEKS_HISTORY + 2)
    y[:3] = 100.0   # only the first 3 weeks are non-zero
    trimmed, lag = CF.trim_trailing_reporting_lag(y)
    assert trimmed.shape[0] >= CF.MIN_WEEKS_HISTORY
    assert lag == 2  # capped by the MIN_WEEKS_HISTORY floor, not by the 6-week cap


# ── bootstrap_total_bands: the 13-week TOTAL's own band (2026-10-07 audit) ──

def test_bootstrap_total_bands_are_ordered_and_deterministic():
    residuals = np.array([-50.0, -10.0, 0.0, 5.0, 40.0, -5.0, 10.0, -20.0])
    point = np.full(13, 500.0)
    out1 = CF.bootstrap_total_bands(residuals, point)
    out2 = CF.bootstrap_total_bands(residuals, point)
    assert out1["p10"] <= out1["p50"] <= out1["p90"]
    assert out1 == out2, "the default seed must make this reproducible"


def test_bootstrap_total_bands_are_narrower_than_the_naive_sum_of_weekly_bands():
    # The defect this replaces: summing each week's own p10/p90 overstates
    # the total's tails (independent errors partially cancel in a sum).
    residuals = np.array([-900.0, -400.0, -100.0, 50.0, 300.0, 700.0, -250.0, 150.0])
    point = np.full(13, 2000.0)
    weekly = CF.residual_bands(residuals, point)
    naive_spread = float(np.sum(weekly["p90"] - weekly["p10"]))
    total = CF.bootstrap_total_bands(residuals, point)
    assert (total["p90"] - total["p10"]) < naive_spread


def test_bootstrap_total_bands_falls_back_to_the_point_sum_with_no_residuals():
    point = np.full(13, 500.0)
    out = CF.bootstrap_total_bands(np.array([]), point)
    assert out == {"p10": 6500.0, "p50": 6500.0, "p90": 6500.0}


def test_bootstrap_total_bands_respects_start_h_like_residual_bands():
    residuals = np.array([-900.0, -400.0, -100.0, 50.0, 300.0, 700.0, -250.0, 150.0])
    point = np.full(5, 2000.0)
    from_one = CF.bootstrap_total_bands(residuals, point, start_h=1)
    from_six = CF.bootstrap_total_bands(residuals, point, start_h=6)
    # Later-starting weeks carry more per-week uncertainty (bigger sqrt(h)),
    # so the total's spread must be at least as wide.
    assert (from_six["p90"] - from_six["p10"]) >= (from_one["p90"] - from_one["p10"])


# ── BacktestResult.as_dict() names the ceiling either way ───────────────────

def test_backtest_as_dict_names_the_ceiling_on_both_success_and_failure():
    y = np.full(CF.MIN_WEEKS_HISTORY + CF.HORIZON_WEEKS + 20, 1000.0)
    ok = CF.rolling_origin_backtest(y, horizon=CF.HORIZON_WEEKS, fold_step=4).as_dict()
    assert ok["calibrated"] is True
    assert ok["calibration_ceiling"] == CF.CALIBRATION_MAPE_CEILING
    assert ok["min_folds"] == CF.MIN_BACKTEST_FOLDS
    assert "within" in ok["reason"] and "50%" in ok["reason"]

    rng = np.random.default_rng(0)
    erratic = rng.uniform(0, 5000, size=CF.MIN_WEEKS_HISTORY + CF.HORIZON_WEEKS + 20)
    bad = CF.rolling_origin_backtest(erratic, horizon=CF.HORIZON_WEEKS, fold_step=4).as_dict()
    assert bad["calibrated"] is False
    assert "exceeds" in bad["reason"] and "50%" in bad["reason"]
