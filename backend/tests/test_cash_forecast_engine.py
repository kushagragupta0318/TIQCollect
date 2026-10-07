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
