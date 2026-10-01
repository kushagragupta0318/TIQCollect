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
#
# 2026-09-24 (later) — The cohort was chosen with LOOK-AHEAD. It kept only
#   accounts observed at EVERY month end of the horizon, so what the future
#   panel happened to contain decided who was scored — the thing the fit
#   window is careful never to do. The cohort is now every account observed
#   at the origin. After it, an absorbing state (WRITTEN_OFF, RESOLVED) is
#   carried forward through gaps, since a closed loan legitimately stops
#   reporting; a live account with no reading that month is excluded from
#   that month's actual shares, and the share excluded is reported per month
#   with `exclusion_high` set above EXCLUSION_FLAG_SHARE, because the
#   simulated shares still describe the whole cohort.
#   The engine also needs each NPA's age at the origin now (the 12-month
#   Doubtful rule is deterministic in mc-1.1.0), so it is derived from the
#   panel's own run of NPA month ends — a lower bound, counted, where the run
#   reaches back to the panel's first month or a gap.
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
from app.strategy.states import ABSORBING, DIRECTION, LIVE, N_STATES, NPA, STATES

_WORSE = (DIRECTION == 1)
_BETTER = (DIRECTION == -1)
# Above this share of the cohort unreadable in a month, the actual shares and
# the simulated ones describe visibly different populations: flag it.
EXCLUSION_FLAG_SHARE = 0.05


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




def npa_age_at(panel: HistoricalPanel, origin: int) -> tuple[np.ndarray, np.ndarray]:
    """Whole months each account has been an NPA at month end `origin`.

    The age is the length of the unbroken run of NPA month ends ending at the
    origin, minus one (entered this month end = 0), exactly as `npa_since`
    defines the spell (DATA-MODEL-V2 §9.5). Returns (age, lower_bound):
    age is -1 for accounts not in NPA at the origin; lower_bound marks runs
    that reach back to the panel's first month or to an unobserved month,
    where the true age can only be larger.
    """
    n = panel.state.shape[0]
    in_npa = np.isin(panel.state[:, :origin + 1], NPA)
    age = np.full(n, -1, dtype=np.int64)
    lower_bound = np.zeros(n, dtype=bool)
    running = in_npa[:, origin].copy()
    length = running.astype(np.int64)
    for m in range(origin - 1, -1, -1):
        still = running & in_npa[:, m]
        ended = running & ~in_npa[:, m]
        lower_bound |= ended & (panel.state[:, m] < 0)   # the run met a gap, not a non-NPA reading
        length += still
        running = still
    lower_bound |= running                               # the run reaches the panel's first month
    age[in_npa[:, origin]] = length[in_npa[:, origin]] - 1
    return age, lower_bound & in_npa[:, origin]


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
    cohort_accounts: int                        # observed at the origin: nothing later decides this
    excluded_share_by_month: tuple[float, ...]  # live accounts with no reading, per horizon month
    max_excluded_share: float
    exclusion_high: bool                        # max_excluded_share > EXCLUSION_FLAG_SHARE
    npa_age_lower_bound_accounts: int
    fit_window: tuple[int, int]
    transitions_used: int
    shock_sigma_used: float
    sigma_estimated: bool
    n_paths: int
    seed: int
    synthetic: bool
    cells: tuple[dict, ...]

    def passes(self, tolerance: float = 0.15) -> bool:
        """Observed coverage within `tolerance` of nominal, on a population
        close enough to the simulated one to be comparable."""
        return abs(self.coverage - self.nominal) <= tolerance and not self.exclusion_high

    def summary(self) -> dict:
        return {"engine_version": self.engine_version, "origin": self.origin,
                "horizon_months": self.horizon_months, "band": list(self.band),
                "nominal": self.nominal, "coverage": self.coverage,
                "coverage_by_month": list(self.coverage_by_month), "n_cells": self.n_cells,
                "n_degenerate": self.n_degenerate, "cohort_accounts": self.cohort_accounts,
                "excluded_share_by_month": list(self.excluded_share_by_month),
                "max_excluded_share": self.max_excluded_share, "exclusion_high": self.exclusion_high,
                "npa_age_lower_bound_accounts": self.npa_age_lower_bound_accounts,
                "fit_window": list(self.fit_window), "shock_sigma_used": self.shock_sigma_used,
                "sigma_estimated": self.sigma_estimated, "synthetic": self.synthetic,
                "passes": self.passes()}


def backtest(panel: HistoricalPanel, *, horizon_months: int = 6, origin: int | None = None,
             fit_months: int | None = None, n_paths: int = 500, seed: int = 0,
             config: EngineConfig | None = None, recovery: RecoveryParams | None = None,
             band: tuple[float, float] = (10.0, 90.0), estimate_sigma: bool = True) -> BacktestReport:
    """Start the engine at `origin` (default: `horizon_months` before the last
    month), fit on the `fit_months` transitions before it (default: all), run
    Baseline with status-quo levers, and score the p-band coverage of the
    actual state shares, month by month.

    The cohort is every account observed at the origin — nothing after the
    origin decides who is in it. Later, an absorbing state is carried forward
    through missing months; a live account with no reading is left out of
    that month's actual shares and counted in `excluded_share_by_month`. A
    cell whose band is a single point that the actual equals (a state nobody
    can reach, both sides 0) is degenerate and not scored.
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

    idx = np.flatnonzero(panel.state[:, origin] >= 0)
    if idx.size == 0:
        raise ValueError("no account is observed at the origin")
    age, age_lower = npa_age_at(panel, origin)
    portfolio = Portfolio(state=panel.state[idx, origin], balance=panel.balance_at(origin)[idx],
                          segment=panel.segment[idx],
                          npa_age_months=np.where(age[idx] >= 0, age[idx], np.nan),
                          synthetic=panel.synthetic)
    res = simulate(portfolio, matrices, scenario=BASELINE, n_paths=n_paths,
                   horizon_months=horizon_months, seed=seed, config=config, recovery=recovery)

    cnt = res.paths.state_count                                    # (P, K+1, 8)
    sim_share = cnt / cnt.sum(axis=-1, keepdims=True)
    lo = np.percentile(sim_share, lo_q, axis=0)
    hi = np.percentile(sim_share, hi_q, axis=0)
    p50 = np.percentile(sim_share, 50, axis=0)
    cells, by_month, excluded = [], [], []
    n_cov = n_deg = 0
    current = panel.state[idx, origin].astype(np.int64)
    for k in range(1, horizon_months + 1):
        obs = panel.state[idx, origin + k].astype(np.int64)
        carried = np.isin(current, ABSORBING)                   # a closed loan stops reporting
        current = np.where(obs >= 0, obs, np.where(carried, current, -1))
        known = current >= 0
        excluded.append(float(1.0 - known.mean()))
        actual = np.bincount(current[known], minlength=N_STATES) / max(int(known.sum()), 1)
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
    max_excl = max(excluded)
    return BacktestReport(
        engine_version=ENGINE_VERSION, origin=origin, horizon_months=horizon_months, band=(lo_q, hi_q),
        nominal=(hi_q - lo_q) / 100.0, coverage=n_cov / n_cells if n_cells else float("nan"),
        coverage_by_month=tuple(by_month), n_cells=n_cells, n_covered=n_cov, n_degenerate=n_deg,
        cohort_accounts=int(idx.size), excluded_share_by_month=tuple(excluded),
        max_excluded_share=max_excl, exclusion_high=bool(max_excl > EXCLUSION_FLAG_SHARE),
        npa_age_lower_bound_accounts=int(age_lower[idx].sum()),
        fit_window=(start, origin), transitions_used=int(counts.sum()),
        shock_sigma_used=float(config.shock_sigma), sigma_estimated=sigma_estimated,
        n_paths=n_paths, seed=seed, synthetic=panel.synthetic, cells=tuple(cells),
    )
