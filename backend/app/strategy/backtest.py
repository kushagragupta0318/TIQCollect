# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New (core of task E04, plan §7.1 "Backtest, and this is what
#   makes it credible"). The Command Center simulator had no backtest of any
#   kind: nothing in collections-platform/command-center ever compared a
#   projection with what happened, so its bands (models/stress_simulator.py
#   :281-283) were never shown to mean anything — and, being per-state
#   percentiles summed (routers/simulate.py:139-141), they could not have.
#
#   This harness starts the engine K months back, fitted ONLY on transitions
#   that completed by the start month (no look-ahead: `count_transitions`
#   refuses a window that ends after the origin), with the Baseline scenario
#   and status-quo levers, and asks how often the bucket shares that actually
#   happened fall inside the simulated p10–p90 band. A nominal 80% band that
#   covers 97% is too wide to be useful; one that covers 40% is overconfident.
#   Both are findings; `BacktestReport.passes` makes the second a failure.
#
#   The shock volatility is FITTED from the pre-origin window by default
#   (`estimate_shock_sigma`), so the backtest validates the engine as it
#   would be run, not CC's hand-set 0.2.
# ───────────────────────────────────────────────────────────────────────────
"""Backtest the Monte Carlo engine against a historical monthly panel.

Pure computation: the caller supplies per-account, per-month-end states (from
`states.portfolio_state` over `loan_dpd_history`) and balances.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from app.strategy.monte_carlo import (
    BASELINE, ENGINE_VERSION, EngineConfig, Portfolio, RecoveryParams, SegmentMatrices, simulate,
)
from app.strategy.states import DIRECTION, LIVE, N_STATES, STATES

_WORSE = (DIRECTION == 1)
_BETTER = (DIRECTION == -1)


@dataclass(frozen=True)
class HistoricalPanel:
    """state[a, m]: state index of account a at month end m (-1 = not observed).
    balance: (n, M) or (n,) total_outstanding. segment[a]: index into keys."""
    state: np.ndarray
    balance: np.ndarray
    segment: np.ndarray
    segment_keys: tuple[str, ...]
    segment_loan_types: tuple[str | None, ...] | None = None
    synthetic: bool = False

    def __post_init__(self):
        st = np.asarray(self.state)
        if st.ndim != 2 or st.shape[1] < 2:
            raise ValueError("state must be (accounts, months) with at least 2 months")
        if st.min() < -1 or st.max() >= N_STATES:
            raise ValueError("state holds state indices 0..7, or -1 for unobserved")
        n = st.shape[0]
        bal = np.asarray(self.balance, dtype=np.float64)
        if bal.shape not in ((n,), st.shape):
            raise ValueError("balance must be (accounts,) or (accounts, months)")
        seg = np.asarray(self.segment)
        if seg.shape != (n,) or seg.min() < 0 or seg.max() >= len(self.segment_keys):
            raise ValueError("segment must index segment_keys, one per account")
        object.__setattr__(self, "state", st.astype(np.int8))
        object.__setattr__(self, "balance", bal)
        object.__setattr__(self, "segment", seg.astype(np.int32))
        object.__setattr__(self, "segment_keys", tuple(self.segment_keys))

    @property
    def n_months(self) -> int:
        return int(self.state.shape[1])

    def balance_at(self, m: int) -> np.ndarray:
        return self.balance if self.balance.ndim == 1 else self.balance[:, m]


def _pair_counts(state: np.ndarray, segment: np.ndarray, n_segments: int, m: int) -> np.ndarray:
    a, b = state[:, m], state[:, m + 1]
    ok = (a >= 0) & (b >= 0)
    key = (segment[ok].astype(np.int64) * N_STATES + a[ok]) * N_STATES + b[ok]
    return np.bincount(key, minlength=n_segments * N_STATES * N_STATES).reshape(n_segments, N_STATES, N_STATES)


def count_transitions(panel: HistoricalPanel, start: int, stop: int) -> np.ndarray:
    """(S, 8, 8) counts of month-end pairs (m, m+1) with start <= m and m+1 <= stop."""
    if not 0 <= start < stop <= panel.n_months - 1:
        raise ValueError(f"need 0 <= start < stop <= {panel.n_months - 1}")
    S = len(panel.segment_keys)
    return sum(_pair_counts(panel.state, panel.segment, S, m) for m in range(start, stop)).astype(np.float64)


def estimate_shock_sigma(panel: HistoricalPanel, start: int, stop: int, min_moves: float = 5.0) -> float:
    """Method-of-moments estimate of the engine's `shock_sigma` from history.

    The engine's shift s moves the log-odds of a deterioration against an
    improvement by 2s in every live row of a segment (states.DIRECTION). So
    per (segment, row, month), r = 0.5 * log((W + .5) / (B + .5)) moves by s,
    where W and B count deteriorations and improvements; its sampling variance
    is ~ 0.25 * (1/W + 1/B) (exact to first order under the multinomial).
    sigma^2 = mean over series of [ var_months(r) - mean(sampling var) ],
    series weighted by months - 1, clipped at 0. Rows averaging fewer than
    `min_moves` of either kind are skipped (the log-ratio is biased there).

    CAVEAT: with persistent shocks (EngineConfig.shock_persistence) a short
    window's sample variance understates the stationary variance; no
    correction is attempted. Read the estimate as a floor.
    """
    S = len(panel.segment_keys)
    months = range(start, stop)
    if len(months) < 3:
        raise ValueError("need at least 3 monthly transitions to estimate a variance")
    W = np.empty((len(months), S, N_STATES))
    B = np.empty_like(W)
    for k, m in enumerate(months):
        c = _pair_counts(panel.state, panel.segment, S, m)
        W[k] = (c * _WORSE).sum(axis=2)
        B[k] = (c * _BETTER).sum(axis=2)
    num = den = 0.0
    for s in range(S):
        for i in LIVE:
            w, b = W[:, s, i], B[:, s, i]
            if w.mean() < min_moves or b.mean() < min_moves:
                continue
            r = 0.5 * np.log((w + 0.5) / (b + 0.5))
            v = 0.25 * (1.0 / (w + 0.5) + 1.0 / (b + 0.5))
            dof = len(r) - 1
            num += dof * (r.var(ddof=1) - v.mean())
            den += dof
    if den == 0:
        raise ValueError("no row has enough moves to estimate the shock volatility")
    return float(math.sqrt(max(num / den, 0.0)))


@dataclass(frozen=True)
class BacktestReport:
    engine_version: str
    origin: int
    horizon_months: int
    band: tuple[float, float]
    nominal: float
    coverage: float
    coverage_by_month: tuple[float, ...]
    n_cells: int
    n_covered: int
    n_degenerate: int
    cohort_accounts: int
    excluded_accounts: int
    fit_window: tuple[int, int]
    transitions_used: int
    shock_sigma_used: float
    sigma_estimated: bool
    n_paths: int
    seed: int
    synthetic: bool
    cells: tuple[dict, ...]

    def passes(self, tolerance: float = 0.15) -> bool:
        """Observed coverage within `tolerance` of nominal."""
        return abs(self.coverage - self.nominal) <= tolerance

    def summary(self) -> dict:
        return {"engine_version": self.engine_version, "origin": self.origin,
                "horizon_months": self.horizon_months, "band": list(self.band),
                "nominal": self.nominal, "coverage": self.coverage,
                "coverage_by_month": list(self.coverage_by_month), "n_cells": self.n_cells,
                "n_degenerate": self.n_degenerate, "cohort_accounts": self.cohort_accounts,
                "excluded_accounts": self.excluded_accounts, "fit_window": list(self.fit_window),
                "shock_sigma_used": self.shock_sigma_used, "sigma_estimated": self.sigma_estimated,
                "synthetic": self.synthetic}


def backtest(panel: HistoricalPanel, *, horizon_months: int = 6, origin: int | None = None,
             fit_months: int | None = None, n_paths: int = 500, seed: int = 0,
             config: EngineConfig | None = None, recovery: RecoveryParams | None = None,
             band: tuple[float, float] = (10.0, 90.0), estimate_sigma: bool = True) -> BacktestReport:
    """Start the engine at `origin` (default: `horizon_months` before the last
    month), fit on the `fit_months` transitions before it (default: all), run
    Baseline with status-quo levers, and score the p-band coverage of the
    actual state shares, month by month.

    The cohort is every account observed at the origin and at every month end
    of the horizon; accounts that drop out are excluded and counted, never
    imputed. A cell whose band is a single point that the actual equals (a
    state nobody can reach, both sides 0) is degenerate and not scored.
    """
    M = panel.n_months
    origin = M - 1 - horizon_months if origin is None else origin
    if horizon_months < 1 or origin + horizon_months > M - 1:
        raise ValueError("origin + horizon_months must lie within the panel")
    start = 0 if fit_months is None else origin - fit_months
    if start < 0 or origin - start < 1:
        raise ValueError("the fit window must hold at least one transition before the origin")
    lo_q, hi_q = band
    if not 0 <= lo_q < hi_q <= 100:
        raise ValueError("band must be (low, high) percentiles with low < high")

    counts = count_transitions(panel, start, origin)  # pairs ending at or before origin: no look-ahead
    matrices = SegmentMatrices(panel.segment_keys, counts, loan_types=panel.segment_loan_types,
                               synthetic=panel.synthetic, source=f"backtest fit months {start}..{origin}")
    config = config or EngineConfig()
    sigma_estimated = False
    if estimate_sigma and origin - start >= 3:
        config = replace(config, shock_sigma=estimate_shock_sigma(panel, start, origin))
        sigma_estimated = True

    window = panel.state[:, origin:origin + horizon_months + 1]
    at_origin = window[:, 0] >= 0
    cohort = at_origin & np.all(window >= 0, axis=1)
    idx = np.flatnonzero(cohort)
    if idx.size == 0:
        raise ValueError("no account is observed through the whole backtest window")
    portfolio = Portfolio(state=panel.state[idx, origin], balance=panel.balance_at(origin)[idx],
                          segment=panel.segment[idx], synthetic=panel.synthetic)
    res = simulate(portfolio, matrices, scenario=BASELINE, n_paths=n_paths,
                   horizon_months=horizon_months, seed=seed, config=config, recovery=recovery)

    cnt = res.paths.state_count                                    # (P, K+1, 8)
    sim_share = cnt / cnt.sum(axis=-1, keepdims=True)
    lo = np.percentile(sim_share, lo_q, axis=0)
    hi = np.percentile(sim_share, hi_q, axis=0)
    p50 = np.percentile(sim_share, 50, axis=0)
    cells, by_month = [], []
    n_cov = n_deg = 0
    for k in range(1, horizon_months + 1):
        actual = np.bincount(panel.state[idx, origin + k], minlength=N_STATES) / idx.size
        cov_k = n_k = 0
        for s in range(N_STATES):
            degenerate = lo[k, s] == hi[k, s] == actual[s]
            covered = bool(lo[k, s] <= actual[s] <= hi[k, s])
            cells.append({"month": k, "state": STATES[s], "actual": float(actual[s]),
                          "p_low": float(lo[k, s]), "p50": float(p50[k, s]), "p_high": float(hi[k, s]),
                          "covered": covered, "degenerate": bool(degenerate)})
            if degenerate:
                n_deg += 1
                continue
            n_k += 1
            cov_k += covered
        by_month.append(cov_k / n_k if n_k else float("nan"))
        n_cov += cov_k
    n_cells = len(cells) - n_deg
    return BacktestReport(
        engine_version=ENGINE_VERSION, origin=origin, horizon_months=horizon_months, band=(lo_q, hi_q),
        nominal=(hi_q - lo_q) / 100.0, coverage=n_cov / n_cells if n_cells else float("nan"),
        coverage_by_month=tuple(by_month), n_cells=n_cells, n_covered=n_cov, n_degenerate=n_deg,
        cohort_accounts=int(idx.size), excluded_accounts=int(at_origin.sum() - idx.size),
        fit_window=(start, origin), transitions_used=int(counts.sum()),
        shock_sigma_used=float(config.shock_sigma), sigma_estimated=sigma_estimated,
        n_paths=n_paths, seed=seed, synthetic=panel.synthetic, cells=tuple(cells),
    )
