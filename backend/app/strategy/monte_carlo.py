# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New (task E02, plan §7.1). REBUILT, not ported, from the
#   Command Center simulator (collections-platform/command-center/backend:
#   models/stress_simulator.py, models/stress_simulator_data.py,
#   routers/simulate.py). What survives from CC is the 8-state space, the
#   macro sensitivities (as labelled ASSUMPTIONS), the four presets and the
#   idea of IFRS-9 staging. Everything that computes a number was replaced,
#   because each of these produced a figure a risk committee would read:
#
#   1. PERCENTILES OF A SUM WERE TAKEN AS A SUM OF PERCENTILES.
#      stress_simulator.py:281-283 takes p10/p50/p90 of every state share
#      separately; simulate.py:139-141 then adds the p10 of Sub-standard,
#      Doubtful and Written-off and calls it "gnpa_p10". The p10 of a sum is
#      not the sum of p10s — different paths are low in different states — so
#      the band was neither a percentile of GNPA nor any path that happened.
#      Here every metric is computed PER PATH and only then are p5/p10/p50/
#      p90/p95, the mean and its standard error taken across paths
#      (`Band.of`). test_true_percentiles_are_taken_per_path pins it.
#
#   2. EXPOSURE WAS ANCHORED ON THE SANCTIONED AMOUNT. simulate.py:55 sums
#      `loan_amount`; a book three years into amortisation was stressed at
#      its day-one size. `Portfolio.balance` is `total_outstanding`, per
#      account, the figure the KPI engine uses (plan §7.1).
#
#   3. "ACCOUNTS AFFECTED" WAS WRITE-OFF CRORES x 50. simulate.py:166-167 and
#      :270-271. There is no account anywhere in that engine — it evolves
#      shares — so it multiplied money by a constant and printed an account
#      count. Here accounts ARE simulated, and WRITE_OFF_ACCOUNTS,
#      RECOVERED_ACCOUNTS and NPA_ACCOUNTS are counted from the paths.
#
#   4. THE CURE BOOST WAS UNFITTED, AND ITS OWN COMMENT WAS WRONG.
#      stress_simulator.py:223 `cure_mult = 0.5 + capacity/100`; the comment
#      at :222 calls 75 "the neutral point (multiplier ~ 1.0)", but 0.5 +
#      0.75 = 1.25, and RiskSimulator.jsx:40 defaults capacity to 100, so the
#      page's default run multiplied every cure probability by 1.5 before any
#      lever was touched. Levers here act through elasticities on the
#      log-odds scale, all in `EngineConfig` with their rationale, all ZERO
#      at the status-quo levers (so the status quo reproduces the observed
#      matrices exactly), and `fit_cure_elasticity` is how the capacity one
#      is meant to be replaced by a fitted value.
#
#   5. ONE MATRIX, IID GAUSSIAN NOISE. stress_simulator.py:186-188 prefers a
#      single portfolio-wide matrix whenever it loads — product and tier then
#      select only the sensitivities — and :232 adds N(0, 0.2) logit noise
#      drawn independently each month from a GLOBAL seed (:228
#      `np.random.seed(42)`). Here: one matrix per segment, drawn per path
#      from a Dirichlet posterior on the observed COUNTS (a thin segment is
#      visibly more uncertain); a systematic factor common to every segment
#      plus an idiosyncratic one per segment, both AR(1), so stress moves
#      segments together and persists; and a SeedSequence per chunk, so a
#      run is reproducible from its seed and touches no global state.
#
#   Also fixed on the way, found while reading:
#   - ECL had no PD. stress_simulator.py:288 is share x "LGD" x exposure; the
#     5% / 20% "LGD" of DEFAULT_LGD (:58) were coverage ratios wearing an LGD
#     label. Here ECL = PD x LGD x EAD per path, PD by first passage into
#     default (not "mass sitting in NPA at month 12", :97-106, which forgets
#     every account that defaulted and cured).
#   - GNPA included written-off balances and counted RESOLVED accounts in the
#     denominator (simulate.py:109). GNPA % here is the RBI ratio: NPA
#     balance over the live book.
#   - The CC row adjustment could not keep a row a distribution without a
#     patch: when shifted off-diagonals summed past 1 it rescaled them and
#     hard-set the diagonal to 0.01 (stress_simulator.py:270-273). The shift
#     here is a multinomial-logit tilt with "stay" as the reference
#     category, which is a distribution by construction.
#
#   What is NOT claimed. Every elasticity, the shock volatility and its
#   correlation, the Beta recovery fractions and the macro sensitivities are
#   ASSUMPTIONS (see EngineConfig / RecoveryParams / MACRO_SENSITIVITY) until
#   fitted; `SimulationResult.assumptions` lists them on every run so a
#   figure never travels without its caveats. backtest.py is what makes the
#   bands checkable.
#
# 2026-09-24 (later) — mc-1.1.0, after the coordinator's audit of daadc17.
#   Numbers change, so the version moves (a rule change is a model change).
#
#   RULED DEVIATIONS — each differs from CC's RiskSimulator on purpose:
#   - GNPA % differs from CC's RiskSimulator because CC divided NPA +
#     WRITTEN-OFF by the WHOLE state vector including RESOLVED
#     (routers/simulate.py:109); the RBI ratio is NPA over the live book.
#   - ECL differs from CC's RiskSimulator because CC's was share x "LGD" x
#     exposure with no PD (stress_simulator.py:288); here it is PD (first
#     passage into default) x LGD x EAD.
#   - Recovered cash differs from CC's RiskSimulator because CC had none —
#     its "recovery rate" was the RESOLVED share (simulate.py:112-115); here
#     cash is counted only on exits from delinquency into CURRENT or
#     RESOLVED, so roll-backs short of CURRENT are not cash.
#   - Parallelism differs from CC's RiskSimulator because CC looped paths in
#     Python on one core; here chunks run on THREADS, not processes, since
#     Celery's prefork workers are daemonic and cannot start child
#     processes. Results do not depend on the thread count.
#
#   GATES FIXED:
#   1. WRITTEN_OFF was in IFRS-9 stage 3 (states.IFRS9_STAGE, copied from
#      CC's STAGE_STATES at stress_simulator.py:64 under a comment that
#      wrongly credited the brief). ECL therefore double-counted WRITE_OFFS
#      and rose with a harsher write-off policy. Written-off balances are now
#      derecognised: no EAD, no ECL. test_write_offs_leave_the_ecl_base.
#   2. NPA_SUB -> NPA_DOUBTFUL was a random monthly hazard (the observed
#      matrix cell, 1/12 in the synthetic book) while states.py called it a
#      REGULATORY 12-month rule. The engine now keeps each account's NPA
#      entry month on every path and moves it at exactly 12 months; matrix
#      mass between the two NPA states is folded into staying put, and entry
#      into NPA from outside is always Sub-standard.
#      test_doubtful_is_reached_by_age_and_only_by_age.
#   3. Synthetic honesty. `synthetic` flipped to False the moment real counts
#      arrived, while every elasticity, LGD and Beta mean was still unfitted,
#      and headline() / to_records() dropped the caveats. The run now carries
#      `calibrated_by_backtest`, `synthetic_inputs`, `synthetic_warning` and
#      `assumptions` in headline() and run_record(), and every to_records()
#      row carries `calibrated_by_backtest` and `synthetic_warning`. The
#      warning clears only when `simulate(calibration=...)` is handed a
#      PASSING backtest on real (non-synthetic) history for this engine
#      version — never because of where the counts came from.
#
#   DECLARED, NOT TUNED: the Sector Shock preset. On CC's sector loadings it
#   is harsher than Severely Adverse for HOME (+2.11 log-odds vs +1.905) and
#   GOLD (+2.44 vs +1.725), and milder than Adverse for PERSONAL (+0.1225 vs
#   +0.271). That is CC's calibration, not a finding. It is recorded in the
#   preset, in ASSUMPTIONS, and as an E05/E08 recalibration item, and
#   test_sector_shock_calibration_is_declared_not_tuned pins those numbers so
#   the recalibration has to change a test on purpose.
# ───────────────────────────────────────────────────────────────────────────
"""Account-level Monte Carlo for a collections book (plan §7.1, task E02).

Pure computation: numpy only, no database, no ORM. Callers pass plain arrays.

THE MODEL, month by month, for every account on every path
----------------------------------------------------------
1.  Per path, each segment's 8x8 matrix is DRAWN from its Dirichlet posterior:
    row i ~ Dirichlet(counts_i + prior_i), the prior being `prior_strength`
    pseudo-transitions spread in proportion to the book's POOLED row (plus a
    tiny floor on structurally reachable cells nobody observed). A segment
    with 20 observed transitions in a row is uncertain; one with 20,000 is not.

2.  A shift s[p, t, seg] on the log-odds scale:
        s = mu_seg + sigma * ( sqrt(w) * Z[p, t] + sqrt(1 - w) * eta[p, t, seg] )
    mu_seg is the scenario's deterministic macro shift for the segment's
    product; Z is ONE systematic factor per path per month, common to every
    segment; eta is idiosyncratic. Both are stationary AR(1) N(0, 1), so
    sigma is the stationary SD of a segment's shift and w is the correlation
    between two segments' shifts.

3.  The row is TILTED (multinomial logit, "stay" as reference):
        p'_ij  ∝  p_ij * exp( d_ij * s  +  [d_ij = -1] * L_i )
    d_ij = +1 for a deterioration, -1 for an improvement, 0 for staying
    (states.DIRECTION). L_i is the levers' log-odds uplift on improving,
    zero at the status-quo levers. A settlement programme then adds a
    monthly acceptance hazard h on NPA rows, as a ninth outcome that lands in
    RESOLVED at (1 - discount) of the balance.
    NPA_SUB -> NPA_DOUBTFUL is NOT in the matrix: every account's NPA entry
    month is tracked on every path and the move happens at exactly 12 months
    (the RBI rule, states.DOUBTFUL_AFTER_MONTHS). Matrix mass between the two
    NPA states is folded into staying put; a new NPA always enters as Sub.

4.  Each account draws its next state by inverse CDF. The lookup is a
    Chen–Asau guide table: one table read settles ~97% of draws (measured
    on the synthetic 50k book: 3.4% walk), a short threshold walk the rest —
    exact, not an approximation (test_the_guide_table_draw_is_exact). Measured in the
    fieldops-test image (numpy 2.2): ~27 ns per account-step, against ~83 ns
    for a take-and-compare over the 8 thresholds and ~240 ns for one flat
    np.searchsorted over row-offset cumulative rows.

5.  Cash: a cure into CURRENT or a resolution into RESOLVED FROM A DELINQUENT
    STATE recovers balance x f, f ~ Beta(a, b) for that from-state and
    destination; a settlement recovers balance x (1 - discount). Roll-backs
    that stop short of CURRENT (SMA_2 -> SMA_1) involve a payment the engine
    does not count, so recovered cash is conservative. Balances are the
    total_outstanding at the start (EAD) and are not amortised.

6.  Cost: commission = placement_rate x recovered cash x commission_pct of
    the from-state; field cost = visits x rate, visits scaling with capacity
    and the delinquent account count on the path.

Outputs are PER PATH first; percentiles, mean and standard error across paths
last. See SimulationResult.
"""
from __future__ import annotations

import math
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Mapping, Sequence

import numpy as np

from app.strategy.states import (
    ABSORBING, CURRENT, DEFAULT_STATES, DELINQUENT, DIRECTION, DOUBTFUL_AFTER_MONTHS,
    DPD_RANGE, IFRS9_STAGE, LIVE, N_STATES, NPA, NPA_DOUBTFUL, NPA_SUB, REACHABLE,
    RESOLVED, STATE_INDEX, STATES, WRITTEN_OFF,
)

ENGINE_VERSION = "mc-1.1.0"  # mc-1.0.0 = daadc17; see CHANGELOG for what moved
PERCENTILES = (5, 10, 50, 90, 95)
_N_OUT = N_STATES + 1  # 8 states + SETTLED (a ninth outcome that lands in RESOLVED)
_SETTLED = N_STATES
_BETTER = (DIRECTION == -1).astype(np.float64)
_NEVER = np.int16(np.iinfo(np.int16).max)  # "no Doubtful promotion pending"

# ── Macro scenarios ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MacroScenario:
    """A macro shock, as CHANGES from today (not levels)."""
    name: str = "Baseline"
    gdp: float = 0.0           # real GDP growth, percentage points (negative = slowdown)
    cpi: float = 0.0           # CPI inflation, percentage points
    repo_bps: float = 0.0      # policy repo rate, basis points
    unemployment: float = 0.0  # unemployment rate, percentage points
    sector: float = 0.0        # sector index, % (negative = stress); product-weighted


# CC's regulatory-style presets, values unchanged (RiskSimulator.jsx:8-13).
# RECALIBRATION DUE (E05/E08), declared rather than tuned by eye: on
# MACRO_SENSITIVITY, "sector_shock" shifts HOME by +2.11 and GOLD by +2.44 —
# harsher than "severely_adverse" (+1.905, +1.725) — and PERSONAL by +0.1225,
# milder than "adverse" (+0.271). A secured-heavy book therefore reads the
# sector preset as its worst case. Pinned by
# test_sector_shock_calibration_is_declared_not_tuned.
PRESETS: dict[str, MacroScenario] = {
    "baseline": MacroScenario("Baseline"),
    "adverse": MacroScenario("Adverse", gdp=-1.5, cpi=1.2, repo_bps=50, unemployment=1.0, sector=-2.0),
    "severely_adverse": MacroScenario("Severely Adverse", gdp=-4.0, cpi=3.0, repo_bps=150,
                                      unemployment=2.5, sector=-5.0),
    "sector_shock": MacroScenario("Sector Shock", gdp=-0.5, cpi=0.5, repo_bps=25,
                                  unemployment=0.5, sector=-8.0),
}
BASELINE = PRESETS["baseline"]

# Log-odds shift toward deterioration per unit of each macro change. ASSUMPTION:
# CC's hand-authored coefficients (stress_simulator_data.py:128-135, "Prime"
# tier, i.e. multiplier 1.0), never fitted there and not fitted here. `repo`
# is per 100 bps. Mapped onto the repo's LoanType members; the three CC never
# had are borrowed and say so.
MACRO_SENSITIVITY: dict[str, dict[str, float]] = {
    "CREDIT_CARD": {"gdp": -0.05, "cpi": 0.02, "repo": 0.08, "unemployment": 0.15, "sector": -0.05},
    "PERSONAL":    {"gdp": -0.04, "cpi": 0.03, "repo": 0.07, "unemployment": 0.14, "sector": 0.00},
    "HOME":        {"gdp": -0.02, "cpi": 0.05, "repo": 0.20, "unemployment": 0.05, "sector": -0.25},
    "AUTO":        {"gdp": -0.03, "cpi": 0.04, "repo": 0.10, "unemployment": 0.08, "sector": -0.20},
    "BUSINESS":    {"gdp": -0.15, "cpi": 0.08, "repo": 0.18, "unemployment": 0.04, "sector": -0.25},  # CC "SME"
    "GOLD":        {"gdp": -0.01, "cpi": 0.02, "repo": 0.05, "unemployment": 0.02, "sector": -0.30},  # borrowed: CC "Agri"
    "MICROFINANCE": {"gdp": -0.01, "cpi": 0.02, "repo": 0.05, "unemployment": 0.02, "sector": -0.30},  # borrowed: CC "Agri"
    "EDUCATION":   {"gdp": -0.04, "cpi": 0.03, "repo": 0.07, "unemployment": 0.14, "sector": 0.00},  # borrowed: CC "Personal Loan"
}
DEFAULT_SENSITIVITY_LOAN_TYPE = "PERSONAL"


def macro_shift(scenario: MacroScenario, loan_type: str | None, scale: float = 1.0) -> float:
    """Deterministic log-odds shift toward deterioration for one product."""
    b = MACRO_SENSITIVITY.get(loan_type or DEFAULT_SENSITIVITY_LOAN_TYPE,
                              MACRO_SENSITIVITY[DEFAULT_SENSITIVITY_LOAN_TYPE])
    return scale * (b["gdp"] * scenario.gdp + b["cpi"] * scenario.cpi
                    + b["repo"] * scenario.repo_bps / 100.0
                    + b["unemployment"] * scenario.unemployment + b["sector"] * scenario.sector)


# ── Levers the bank controls ─────────────────────────────────────────────────

# commission, % of verified collection, by the state the money was recovered
# FROM — the unit of agency_contract_terms.commission_pct (DATA-MODEL-V2).
# ASSUMPTION: a typical Indian slab, rising with the bucket.
DEFAULT_COMMISSION_PCT: dict[str, float] = {
    "SMA_0": 5.0, "SMA_1": 8.0, "SMA_2": 10.0, "NPA_SUB": 15.0, "NPA_DOUBTFUL": 20.0,
}


@dataclass(frozen=True)
class Levers:
    """What the bank can set. The DEFAULTS are the status quo the transition
    matrices were observed under, and every lever effect is measured from
    `EngineConfig.baseline` — so `Levers()` reproduces the observed book."""
    placement_rate: float = 0.60        # share of delinquent accounts placed with field agencies
    agency_capacity: float = 1.0        # field visit volume, multiple of the status quo
    commission_pct: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_COMMISSION_PCT))
    settlement_discount: float = 0.0    # share of balance waived; 0 = no settlement programme
    settlement_accept_max: float = 0.10       # monthly acceptance at a very deep discount
    settlement_accept_midpoint: float = 0.30  # discount at which half of that is reached
    settlement_accept_steepness: float = 12.0
    legal_threshold_days: float = 90.0  # DPD at which legal action starts
    writeoff_policy_months: int | None = None  # write off NPAs at this age; None = observed hazard

    def validate(self) -> None:
        if not 0.0 <= self.placement_rate <= 1.0:
            raise ValueError("placement_rate must be in [0, 1]")
        if self.agency_capacity <= 0:
            raise ValueError("agency_capacity must be > 0")
        if not 0.0 <= self.settlement_discount < 1.0:
            raise ValueError("settlement_discount must be in [0, 1)")
        if not 0.0 <= self.settlement_accept_max <= 1.0:
            raise ValueError("settlement_accept_max must be in [0, 1]")
        if self.legal_threshold_days <= 0:
            raise ValueError("legal_threshold_days must be > 0")
        if self.writeoff_policy_months is not None and self.writeoff_policy_months < 1:
            raise ValueError("writeoff_policy_months must be >= 1 or None")
        unknown = set(self.commission_pct) - set(STATE_INDEX)
        if unknown:
            raise ValueError(f"commission_pct keys are not states: {sorted(unknown)}")
        if any(not 0.0 <= v <= 100.0 for v in self.commission_pct.values()):
            raise ValueError("commission_pct values are percentages in [0, 100]")

    def commission_by_state(self) -> np.ndarray:
        out = np.zeros(N_STATES)
        for code, pct in self.commission_pct.items():
            out[STATE_INDEX[code]] = pct
        return out

    def settlement_hazard(self) -> float:
        """Monthly acceptance probability of a settlement offer at this discount.

        A logistic in the discount, re-based so that a zero discount is zero
        acceptance (no programme), rising to `settlement_accept_max`. The
        shape is a placeholder for the settlement model (plan §9): pass its
        fitted parameters in, do not trust these.
        """
        d = self.settlement_discount
        if d <= 0.0 or self.settlement_accept_max <= 0.0:
            return 0.0
        k, m = self.settlement_accept_steepness, self.settlement_accept_midpoint
        s0 = 1.0 / (1.0 + math.exp(k * m))
        s = 1.0 / (1.0 + math.exp(-k * (d - m)))
        return float(self.settlement_accept_max * (s - s0) / (1.0 - s0))


# ── Recovery fractions and IFRS-9 ────────────────────────────────────────────


def fit_beta_moments(fractions: Sequence[float] | np.ndarray) -> tuple[float, float]:
    """Method-of-moments Beta(a, b) for observed recovery fractions in (0, 1)."""
    x = np.asarray(fractions, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 2 or np.any((x <= 0) | (x >= 1)):
        raise ValueError("need >= 2 fractions strictly inside (0, 1)")
    m, v = float(x.mean()), float(x.var(ddof=1))
    if v <= 0 or v >= m * (1 - m):
        raise ValueError("variance incompatible with a Beta distribution")
    k = m * (1 - m) / v - 1.0
    return m * k, (1 - m) * k


# ASSUMPTION: cash recovered, as a share of total_outstanding, on each kind of
# exit. A cure clears the arrears — about one EMI per bucket for a ~36-month
# book (~3-4% of outstanding each); a resolution from early buckets is a
# closure at close to the full balance, from NPA a negotiated or legal
# recovery. Replace with fit_beta_moments on the book's own payments.
_DEFAULT_CURE_MEAN = {"SMA_0": 0.04, "SMA_1": 0.08, "SMA_2": 0.12, "NPA_SUB": 0.20, "NPA_DOUBTFUL": 0.30}
_DEFAULT_RESOLVE_MEAN = {"SMA_0": 0.95, "SMA_1": 0.90, "SMA_2": 0.85, "NPA_SUB": 0.60, "NPA_DOUBTFUL": 0.40}


@dataclass(frozen=True)
class RecoveryParams:
    """Beta(a, b) of the recovered fraction, per FROM-state, for the two exits."""
    cure_a: tuple[float, ...]
    cure_b: tuple[float, ...]
    resolve_a: tuple[float, ...]
    resolve_b: tuple[float, ...]
    source: str = "ASSUMPTION"

    @classmethod
    def from_means(cls, cure_mean: Mapping[str, float], resolve_mean: Mapping[str, float],
                   concentration: float = 20.0, source: str = "ASSUMPTION") -> "RecoveryParams":
        def ab(means: Mapping[str, float]):
            a, b = np.ones(N_STATES), np.ones(N_STATES)
            for code, m in means.items():
                if not 0.0 < m < 1.0:
                    raise ValueError(f"mean recovery for {code} must be in (0, 1)")
                i = STATE_INDEX[code]
                a[i], b[i] = m * concentration, (1.0 - m) * concentration
            return tuple(a.tolist()), tuple(b.tolist())
        ca, cb = ab(cure_mean)
        ra, rb = ab(resolve_mean)
        return cls(ca, cb, ra, rb, source)

    @classmethod
    def default(cls) -> "RecoveryParams":
        return cls.from_means(_DEFAULT_CURE_MEAN, _DEFAULT_RESOLVE_MEAN)

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        arrs = [np.asarray(v, dtype=float) for v in (self.cure_a, self.cure_b, self.resolve_a, self.resolve_b)]
        for a in arrs:
            if a.shape != (N_STATES,) or np.any(a <= 0):
                raise ValueError("Beta parameters must be 8 positive numbers per exit")
        return tuple(arrs)  # type: ignore[return-value]


@dataclass(frozen=True)
class Ifrs9Params:
    """LGD per IFRS-9 stage and the PD horizons. ECL = PD x LGD x EAD.

    ASSUMPTION: unsecured-retail LGDs. CC's 5% / 20% / 70% (stress_simulator
    DEFAULT_LGD) were coverage ratios, not LGDs — its ECL had no PD term.
    Stage 2 wants a LIFETIME PD; remaining tenure is not an input, so a fixed
    36-month horizon stands in for it.
    """
    lgd_stage1: float = 0.60
    lgd_stage2: float = 0.65
    lgd_stage3: float = 0.70
    pd_horizon_stage1_months: int = 12
    pd_horizon_stage2_months: int = 36

    def lgd_by_state(self) -> np.ndarray:
        lgd = {0: 0.0, 1: self.lgd_stage1, 2: self.lgd_stage2, 3: self.lgd_stage3}
        return np.array([lgd[s] for s in IFRS9_STAGE])


# ── Engine configuration: every judgement in one place ───────────────────────


@dataclass(frozen=True)
class EngineConfig:
    """Every number the engine uses that is not an input. Rationale inline.

    Nothing here is fitted yet. `SimulationResult.assumptions` repeats this
    on every run.
    """
    # Dirichlet posterior. 2 pseudo-transitions per row is a WEAK prior: it
    # decides a segment row with ~no data (shrinking it to the book's pooled
    # row) and is noise against a row with a few hundred observations.
    prior_strength: float = 2.0
    # Pseudo-count on reachable cells (states.REACHABLE) nobody observed, so a
    # zero in a thin sample is not read as an impossibility.
    prior_floor: float = 0.05
    parameter_uncertainty: bool = True

    # Correlated shocks. sigma 0.20 is CC's per-month noise (stress_simulator
    # :232), kept as the starting value; backtest.estimate_shock_sigma fits it.
    shock_sigma: float = 0.20
    # Correlation between two segments' shifts (w). Stress in one product line
    # is usually stress in the others; 0.5 splits the difference.
    systematic_share: float = 0.50
    # Monthly AR(1) of both factors. Macro conditions persist; 0.6 gives a
    # shock a half-life of ~1.4 months and makes the fan widen with horizon.
    shock_persistence: float = 0.60
    # Multiplier on MACRO_SENSITIVITY. CC's default page ran "Near-prime", x1.4.
    sensitivity_scale: float = 1.0

    # Lever elasticities, all on the log-odds of IMPROVING vs staying, for a
    # delinquent account, per unit stated. A worked account's odds rise by
    #     g(v) = field_effect + capacity_elasticity * ln(v / v0)
    # where v is visits per placed account; the book average is then
    #     L = placement * g(v) - placement0 * g(v0)
    #       = field_effect * (p - p0) + capacity_elasticity * p * ln(c * p0 / p)
    # since fixed capacity spreads over the placed share (v = v0 c p0 / p).
    # Linear in log-odds, i.e. a first-order approximation of the mixture.
    field_effect: float = 0.50         # placed vs unplaced: odds x1.65
    capacity_elasticity: float = 0.35  # doubling visits/account: odds x1.27. Fit: fit_cure_elasticity
    commission_elasticity: float = 0.02  # per commission point, for the placed share
    legal_effect: float = 0.40         # an account wholly inside the legal regime
    baseline: Levers = field(default_factory=Levers)

    # Cost. FIELD_VISIT PER_ATTEMPT is a strategy.cost_rates row; this default
    # stands in until that table is read.
    field_visit_cost_inr: float = 300.0
    visits_per_placed_account: float = 2.0  # per month, at the status quo

    # Reproducibility and speed. Results are a function of (seed, inputs,
    # chunk_paths) — NOT of n_workers.
    chunk_paths: int = 8
    n_workers: int | None = None
    guide_bins: int = 64  # power of two, so u * K is exact in float32

    def validate(self) -> None:
        if self.prior_strength < 0 or self.prior_floor < 0:
            raise ValueError("prior_strength and prior_floor must be >= 0")
        if self.shock_sigma < 0:
            raise ValueError("shock_sigma must be >= 0")
        if not 0.0 <= self.systematic_share <= 1.0:
            raise ValueError("systematic_share must be in [0, 1]")
        if not -1.0 < self.shock_persistence < 1.0:
            raise ValueError("shock_persistence must be in (-1, 1)")
        if self.chunk_paths < 1:
            raise ValueError("chunk_paths must be >= 1")
        k = self.guide_bins
        if k < 2 or k & (k - 1):
            raise ValueError("guide_bins must be a power of two >= 2")
        self.baseline.validate()


# Printed on every result so a number never travels without its caveats.
ASSUMPTIONS: tuple[str, ...] = (
    "Transition matrices are Dirichlet posteriors on the counts supplied; they are as good as that history.",
    "Shock volatility, its cross-segment correlation and persistence (EngineConfig) are not fitted"
    " unless backtest.estimate_shock_sigma supplied shock_sigma.",
    "Macro sensitivities are CC's hand-authored coefficients (MACRO_SENSITIVITY), never fitted.",
    "Lever elasticities (field, capacity, commission, legal) are assumptions; fit_cure_elasticity replaces capacity's.",
    "Settlement acceptance curve parameters are placeholders for the settlement model (plan §9).",
    "Recovery fractions are Beta assumptions per from-state unless RecoveryParams was fitted.",
    "Balances are total_outstanding at the start (EAD) and are not amortised over the horizon.",
    "Roll-backs short of CURRENT are not counted as cash, so recovered cash is conservative.",
    "IFRS-9 stage-2 PD uses a fixed 36-month horizon as a lifetime proxy; written-off and resolved"
    " balances are derecognised (no EAD, no ECL), so WRITE_OFFS is the loss line for them.",
    "LGD per IFRS-9 stage (Ifrs9Params) is an unsecured-retail assumption, not fitted.",
    "Sector Shock preset is CC's calibration and is due for recalibration (E05/E08): it shifts HOME"
    " +2.11 and GOLD +2.44, harsher than Severely Adverse, and PERSONAL +0.12, milder than Adverse.",
    "A passing backtest validates state-share dynamics under the status quo only; lever"
    " elasticities, LGDs and recovery fractions are not tested by it.",
)

# The run-level caveat. It clears ONLY when simulate() is handed a passing
# backtest on real history for this engine version (see _calibration_status);
# never because the transition counts happen to be real.
UNCALIBRATED_WARNING = (
    "UNCALIBRATED: no backtest on this book's own history has passed for engine " + ENGINE_VERSION + ". "
    "Shock volatility, lever elasticities, LGDs and recovery fractions are assumptions; read the bands "
    "as scenario arithmetic, not a forecast."
)
SYNTHETIC_WARNING = (
    "SYNTHETIC: the book or its transition counts are generated, not observed; not evidence about any "
    "real borrower. " + UNCALIBRATED_WARNING
)


# ── Inputs: the book and its matrices ────────────────────────────────────────


def _opt_array(x, n: int, name: str, dtype=None) -> np.ndarray | None:
    if x is None:
        return None
    a = np.asarray(x, dtype=dtype)
    if a.shape != (n,):
        raise ValueError(f"{name} must have shape ({n},), got {a.shape}")
    return a


@dataclass(frozen=True)
class Portfolio:
    """The book at `as_of`, one entry per account. Plain arrays, no ORM.

    state          state index (states.STATES), e.g. from states_from_buckets
    balance        total_outstanding in INR — the EAD (never loan_amount)
    segment        index into SegmentMatrices.keys
    loan_type      optional, for sensitivities and breakdowns
    region         optional, for breakdowns
    npa_age_months optional, whole months since npa_since (write-off policy)
    weight         >1 only on a stratified subsample (Portfolio.subsample)
    """
    state: np.ndarray
    balance: np.ndarray
    segment: np.ndarray
    loan_type: np.ndarray | None = None
    region: np.ndarray | None = None
    npa_age_months: np.ndarray | None = None
    weight: np.ndarray | None = None
    synthetic: bool = False

    def __post_init__(self):
        state = np.asarray(self.state)
        n = state.shape[0] if state.ndim == 1 else -1
        if n < 1:
            raise ValueError("state must be a non-empty 1-D array")
        if not np.issubdtype(state.dtype, np.integer) or state.min() < 0 or state.max() >= N_STATES:
            raise ValueError("state must hold state indices 0..7")
        balance = np.asarray(self.balance, dtype=np.float64)
        if balance.shape != (n,) or not np.all(np.isfinite(balance)) or np.any(balance < 0):
            raise ValueError("balance must be finite, non-negative, one per account")
        segment = np.asarray(self.segment)
        if segment.shape != (n,) or not np.issubdtype(segment.dtype, np.integer) or segment.min() < 0:
            raise ValueError("segment must be non-negative integer indices, one per account")
        object.__setattr__(self, "state", state.astype(np.int8))
        object.__setattr__(self, "balance", balance)
        object.__setattr__(self, "segment", segment.astype(np.int32))
        object.__setattr__(self, "loan_type", _opt_array(self.loan_type, n, "loan_type", object))
        object.__setattr__(self, "region", _opt_array(self.region, n, "region", object))
        object.__setattr__(self, "npa_age_months", _opt_array(self.npa_age_months, n, "npa_age_months", float))
        w = _opt_array(self.weight, n, "weight", np.float64)
        if w is not None and (not np.all(np.isfinite(w)) or np.any(w <= 0)):
            raise ValueError("weight must be positive")
        object.__setattr__(self, "weight", w)

    @property
    def n(self) -> int:
        return int(self.state.shape[0])

    @property
    def weights(self) -> np.ndarray:
        return self.weight if self.weight is not None else np.ones(self.n)

    def take(self, idx: np.ndarray, weight: np.ndarray | None = None) -> "Portfolio":
        def pick(a):
            return None if a is None else a[idx]
        return Portfolio(self.state[idx], self.balance[idx], self.segment[idx], pick(self.loan_type),
                         pick(self.region), pick(self.npa_age_months),
                         weight if weight is not None else pick(self.weight), self.synthetic)

    def subsample(self, max_accounts: int, seed: int = 0) -> "Portfolio":
        """Stratified sample with Horvitz–Thompson weights.

        Strata: segment x state x balance quartile within (segment, state), so
        the balance-weighted outputs (GNPA %, cash) keep their tail. Each
        non-empty stratum keeps at least one account; weight = N_h / n_h.
        The subsample is fixed for the run: its sampling error is NOT in the
        path bands, which is why this is opt-in.
        """
        if max_accounts >= self.n:
            return self
        cell = self.segment.astype(np.int64) * N_STATES + self.state
        order = np.lexsort((self.balance, cell))
        sorted_cell = cell[order]
        starts = np.r_[0, np.flatnonzero(np.diff(sorted_cell)) + 1]
        sizes = np.diff(np.r_[starts, sorted_cell.size])
        rank = np.arange(sorted_cell.size) - np.repeat(starts, sizes)
        quartile = np.empty(self.n, dtype=np.int64)
        quartile[order] = (4 * rank) // np.repeat(sizes, sizes)
        stratum = cell * 4 + quartile
        uniq, inv, n_h = np.unique(stratum, return_inverse=True, return_counts=True)
        if uniq.size > max_accounts:
            raise ValueError(f"max_accounts={max_accounts} is below the {uniq.size} non-empty strata")
        take_h = np.clip(np.round(n_h * (max_accounts / self.n)).astype(np.int64), 1, n_h)
        rng = np.random.default_rng(np.random.SeedSequence([seed, 0x5AB5]))
        perm = rng.permutation(self.n)
        inv_perm = inv[perm]
        o = np.argsort(inv_perm, kind="stable")
        s_sorted = inv_perm[o]
        st = np.r_[0, np.flatnonzero(np.diff(s_sorted)) + 1]
        pos = np.arange(s_sorted.size) - np.repeat(st, np.diff(np.r_[st, s_sorted.size]))
        keep = pos < take_h[s_sorted]
        chosen = perm[o[keep]]
        chosen.sort()
        w = (n_h / take_h)[inv[chosen]] * self.weights[chosen]
        return self.take(chosen, weight=w)


@dataclass(frozen=True)
class SegmentMatrices:
    """Observed month-to-month transition COUNTS per segment (not probabilities).

    counts[s, i, j] = accounts in segment s that were in state i at one month
    end and in state j at the next. The shape of `mv_bucket_transitions_monthly`
    summed over months (task E01 produces these from SQL; backtest.py from a
    panel). `loan_types[s]` selects the macro sensitivities.
    """
    keys: tuple[str, ...]
    counts: np.ndarray
    loan_types: tuple[str | None, ...] | None = None
    synthetic: bool = False
    source: str = ""

    def __post_init__(self):
        c = np.asarray(self.counts, dtype=np.float64)
        keys = tuple(self.keys)
        if c.shape != (len(keys), N_STATES, N_STATES):
            raise ValueError(f"counts must have shape ({len(keys)}, 8, 8), got {c.shape}")
        if not np.all(np.isfinite(c)) or np.any(c < 0):
            raise ValueError("counts must be finite and non-negative")
        if len(set(keys)) != len(keys):
            raise ValueError("segment keys must be unique")
        if self.loan_types is not None and len(self.loan_types) != len(keys):
            raise ValueError("loan_types must have one entry per segment")
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "counts", c)

    @property
    def n_segments(self) -> int:
        return len(self.keys)

    def index_of(self, keys: Sequence[str]) -> np.ndarray:
        pos = {k: i for i, k in enumerate(self.keys)}
        try:
            return np.array([pos[k] for k in keys], dtype=np.int32)
        except KeyError as e:
            raise ValueError(f"unknown segment key {e.args[0]!r}") from None

    def dirichlet_alpha(self, config: EngineConfig) -> tuple[np.ndarray, np.ndarray]:
        """Posterior Dirichlet parameters and the rows no data describes.

        alpha[s, i] = counts[s, i] + prior_strength * pooled_row_i
                      + prior_floor on reachable cells the whole book never saw.
        A live row with no evidence in the segment OR the book is returned as
        identity ("held in place") and flagged, never invented.
        """
        c = self.counts
        pooled = c.sum(axis=0)
        prow = pooled.sum(axis=1, keepdims=True)
        pooled_p = np.divide(pooled, prow, out=np.zeros_like(pooled), where=prow > 0)
        floor = config.prior_floor * (REACHABLE & (pooled_p == 0))
        alpha = c + config.prior_strength * pooled_p[None] + floor[None]
        no_evidence = (c.sum(axis=2) == 0) & (prow[:, 0] == 0)[None, :]
        eye = np.eye(N_STATES)
        for i in ABSORBING:
            alpha[:, i, :] = eye[i]
            no_evidence[:, i] = False
        alpha[no_evidence] = eye[np.nonzero(no_evidence)[1]]
        return alpha, no_evidence

    def posterior_mean(self, config: EngineConfig | None = None) -> np.ndarray:
        alpha, _ = self.dirichlet_alpha(config or EngineConfig())
        return alpha / alpha.sum(axis=2, keepdims=True)


# ── Results ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Band:
    """Across-path statistics of a per-path metric (axis 0 = path)."""
    p5: np.ndarray
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray
    p95: np.ndarray
    mean: np.ndarray
    sem: np.ndarray

    @classmethod
    def of(cls, per_path: np.ndarray) -> "Band":
        """NaN paths (e.g. GNPA % once the live book is empty) are left out;
        a metric undefined on every path stays NaN rather than becoming 0."""
        x = np.asarray(per_path, dtype=np.float64)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            q = np.nanpercentile(x, PERCENTILES, axis=0)
            n = np.sum(np.isfinite(x), axis=0)
            mean = np.nanmean(x, axis=0)
            sd = np.nanstd(x, axis=0, ddof=1) if x.shape[0] > 1 else np.full(mean.shape, np.nan)
        sem = sd / np.sqrt(np.maximum(n, 1))
        return cls(q[0], q[1], q[2], q[3], q[4], mean, sem)

    def at(self, index) -> dict[str, float]:
        return {k: float(np.asarray(getattr(self, k))[index]) for k in ("p5", "p10", "p50", "p90", "p95", "mean", "sem")}

    def as_dict(self) -> dict[str, list]:
        return {k: np.asarray(getattr(self, k)).tolist() for k in ("p5", "p10", "p50", "p90", "p95", "mean", "sem")}


@dataclass
class PathOutputs:
    """Everything recorded PER PATH. P paths, T months, S segments.

    Stocks are at month ends 0..T; flows are during months 1..T.
    Counts are account counts (Horvitz–Thompson estimates on a subsample).
    """
    seg_state_count: np.ndarray     # (P, T+1, S, 8)
    seg_state_balance: np.ndarray   # (P, T+1, S, 8)  INR
    cure_cash: np.ndarray           # (P, T, S)  into CURRENT from delinquency
    resolve_cash: np.ndarray        # (P, T, S)  into RESOLVED from delinquency (not settlement)
    settle_cash: np.ndarray         # (P, T, S)  settlement programme
    cure_accounts: np.ndarray       # (P, T, S)
    resolve_accounts: np.ndarray    # (P, T, S)
    settle_accounts: np.ndarray     # (P, T, S)
    written_off_balance: np.ndarray  # (P, T, S)
    written_off_accounts: np.ndarray  # (P, T, S)
    commission: np.ndarray          # (P, T, S)
    field_cost: np.ndarray          # (P, T, S)
    pd_by_state: np.ndarray         # (P, S, 8) the stage-appropriate PD applied in ECL
    ecl_stage: np.ndarray           # (P, T+1, 3) ECL per IFRS-9 stage 1..3, INR
    ead_stage: np.ndarray           # (P, T+1, 3) EAD per IFRS-9 stage 1..3, INR

    @property
    def state_count(self) -> np.ndarray:
        return self.seg_state_count.sum(axis=2)

    @property
    def state_balance(self) -> np.ndarray:
        return self.seg_state_balance.sum(axis=2)

    @property
    def recovered_cash(self) -> np.ndarray:
        return self.cure_cash + self.resolve_cash + self.settle_cash

    @property
    def recovered_accounts(self) -> np.ndarray:
        return self.cure_accounts + self.resolve_accounts + self.settle_accounts

    @property
    def cost(self) -> np.ndarray:
        return self.commission + self.field_cost


def _cum0(flow: np.ndarray) -> np.ndarray:
    """(P, T) monthly flow -> (P, T+1) cumulative with 0 at month 0."""
    out = np.zeros((flow.shape[0], flow.shape[1] + 1))
    np.cumsum(flow, axis=1, out=out[:, 1:])
    return out


def gnpa_pct(state_balance: np.ndarray) -> np.ndarray:
    """RBI GNPA ratio: NPA balance / live-book balance, in %. Last axis = state."""
    live = state_balance[..., list(LIVE)].sum(axis=-1)
    npa = state_balance[..., list(NPA)].sum(axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(live > 0, 100.0 * npa / np.where(live > 0, live, 1.0), np.nan)


METRIC_UNITS: dict[str, str] = {
    "GNPA_PCT": "PCT", "STATE_SHARE": "PCT", "RECOVERED_CASH": "INR", "SETTLEMENT_CASH": "INR",
    "ECL": "INR", "WRITE_OFFS": "INR", "COST": "INR", "NET_RECOVERY": "INR",
    "WRITE_OFF_ACCOUNTS": "COUNT", "RECOVERED_ACCOUNTS": "COUNT", "NPA_ACCOUNTS": "COUNT",
}


@dataclass
class SimulationResult:
    engine_version: str
    seed: int
    n_paths: int
    horizon_months: int
    n_accounts: int
    n_simulated_accounts: int
    subsampled: bool
    segment_keys: tuple[str, ...]
    segment_loan_type: tuple[str | None, ...]
    segment_region: tuple[str | None, ...] | None
    scenario: MacroScenario
    levers: Levers
    config: EngineConfig
    paths: PathOutputs
    summary: dict[str, Band]
    ifrs9: dict[str, dict[str, Band]]
    elapsed_seconds: float
    synthetic_inputs: bool          # the book or its counts are generated
    calibrated_by_backtest: bool    # a PASSING backtest on real history was supplied
    synthetic_warning: str | None   # None only when calibrated and inputs are real
    calibration: dict | None        # the backtest the run was calibrated by, if any
    assumptions: tuple[str, ...]
    diagnostics: dict

    @property
    def recovery_at_risk(self) -> float:
        """p5 of cumulative net recovery at the horizon."""
        return float(self.summary["NET_RECOVERY"].p5[-1])

    def per_path(self, metric: str) -> np.ndarray:
        return _per_path_metrics(self.paths)[metric]

    def run_record(self) -> dict:
        """Run-level fields for strategy.simulation_runs: reproducibility and
        the caveats that must travel with every number the run produced."""
        return {
            "engine_version": self.engine_version, "seed": self.seed, "n_paths": self.n_paths,
            "horizon_months": self.horizon_months, "numpy_version": self.diagnostics.get("numpy_version"),
            "chunk_paths": self.config.chunk_paths, "subsampled": self.subsampled,
            "synthetic_inputs": self.synthetic_inputs,
            "calibrated_by_backtest": self.calibrated_by_backtest,
            "synthetic_warning": self.synthetic_warning,
            "calibration": self.calibration,
            "assumptions": list(self.assumptions),
        }

    def headline(self) -> dict:
        """Horizon values, JSON-friendly. The numbers a memo may quote — with
        the run's caveats beside them, never without."""
        h = {m: b.at(-1) for m, b in self.summary.items() if m != "STATE_SHARE"}
        h["STATE_SHARE"] = {code: self.summary["STATE_SHARE"].at((-1, i)) for i, code in enumerate(STATES)}
        return {
            **self.run_record(), "scenario": asdict(self.scenario),
            "metrics": h, "recovery_at_risk": self.recovery_at_risk,
            "ifrs9": {stage: {k: b.at(-1) for k, b in parts.items()} for stage, parts in self.ifrs9.items()},
        }

    def to_records(self, include_segments: bool = False) -> list[dict]:
        """Rows shaped for strategy.simulation_results (DATA-MODEL-V2 §4.8).

        Every row carries `calibrated_by_backtest` and `synthetic_warning`, so
        a row read on its own is not mistaken for a calibrated forecast; the
        assumptions list is run-level (run_record)."""
        rows: list[dict] = []
        caveat = {"calibrated_by_backtest": self.calibrated_by_backtest,
                  "synthetic_warning": self.synthetic_warning}

        def emit(metric, band: Band, segment_key="ALL", segment_state=None, index=None):
            for t in range(self.horizon_months + 1):
                ix = t if index is None else (t, index)
                rows.append({"metric": metric, "period_index": t, "segment_key": segment_key,
                             "segment_state": segment_state, "unit": METRIC_UNITS[metric],
                             **band.at(ix), **caveat})

        for metric, band in self.summary.items():
            if metric == "STATE_SHARE":
                for i, code in enumerate(STATES):
                    emit(metric, band, segment_state=code, index=i)
            else:
                emit(metric, band)
        if include_segments:
            seg_bal = self.paths.seg_state_balance
            seg_cash = _cum0_seg(self.paths.recovered_cash)
            for s, key in enumerate(self.segment_keys):
                emit("GNPA_PCT", Band.of(gnpa_pct(seg_bal[:, :, s, :])), segment_key=key)
                emit("RECOVERED_CASH", Band.of(seg_cash[:, :, s]), segment_key=key)
        return rows

    def breakdown(self, by: str | Mapping[str, str] = "segment") -> dict[str, dict[str, Band]]:
        """GNPA %, recovered cash and write-offs by segment, loan type, region,
        or any segment -> group mapping. Per path first, then bands."""
        if isinstance(by, str):
            if by == "segment":
                group = {k: k for k in self.segment_keys}
            elif by in ("loan_type", "region"):
                attr = self.segment_loan_type if by == "loan_type" else self.segment_region
                if attr is None or any(a is None for a in attr):
                    raise ValueError(f"segments do not each carry a single {by}")
                group = dict(zip(self.segment_keys, attr))
            else:
                raise ValueError("by must be 'segment', 'loan_type', 'region' or a mapping")
        else:
            group = dict(by)
        names = sorted(set(group.values()), key=str)
        seg_bal = self.paths.seg_state_balance
        seg_cash = _cum0_seg(self.paths.recovered_cash)
        seg_wo = _cum0_seg(self.paths.written_off_balance)
        out = {}
        for g in names:
            idx = [s for s, k in enumerate(self.segment_keys) if group.get(k) == g]
            out[str(g)] = {
                "GNPA_PCT": Band.of(gnpa_pct(seg_bal[:, :, idx, :].sum(axis=2))),
                "RECOVERED_CASH": Band.of(seg_cash[:, :, idx].sum(axis=2)),
                "WRITE_OFFS": Band.of(seg_wo[:, :, idx].sum(axis=2)),
            }
        return out


def _cum0_seg(flow: np.ndarray) -> np.ndarray:
    """(P, T, S) -> (P, T+1, S) cumulative, 0 at month 0."""
    out = np.zeros((flow.shape[0], flow.shape[1] + 1, flow.shape[2]))
    np.cumsum(flow, axis=1, out=out[:, 1:])
    return out


def _per_path_metrics(p: PathOutputs) -> dict[str, np.ndarray]:
    bal = p.state_balance
    cnt = p.state_count
    recovered = _cum0(p.recovered_cash.sum(axis=2))
    cost = _cum0(p.cost.sum(axis=2))
    tot = cnt.sum(axis=-1, keepdims=True)
    out = {
        "GNPA_PCT": gnpa_pct(bal),
        "STATE_SHARE": 100.0 * cnt / np.where(tot > 0, tot, 1.0),
        "RECOVERED_CASH": recovered,
        "SETTLEMENT_CASH": _cum0(p.settle_cash.sum(axis=2)),
        "WRITE_OFFS": _cum0(p.written_off_balance.sum(axis=2)),
        "COST": cost,
        "NET_RECOVERY": recovered - cost,
        "WRITE_OFF_ACCOUNTS": _cum0(p.written_off_accounts.sum(axis=2)),
        "RECOVERED_ACCOUNTS": _cum0(p.recovered_accounts.sum(axis=2)),
        "NPA_ACCOUNTS": cnt[..., list(NPA)].sum(axis=-1),
        "ECL": p.ecl_stage.sum(axis=-1),
    }
    return out


# ── Engine internals ─────────────────────────────────────────────────────────

# Event codes for (from-state, outcome) pairs.
_EV_NONE, _EV_CURE, _EV_RESOLVE, _EV_SETTLE, _EV_WRITE_OFF = 0, 1, 2, 3, 4


def _event_table() -> np.ndarray:
    e = np.zeros((N_STATES, _N_OUT), dtype=np.int8)
    for i in LIVE:
        e[i, WRITTEN_OFF] = _EV_WRITE_OFF
    for i in DELINQUENT:
        e[i, CURRENT] = _EV_CURE
        e[i, RESOLVED] = _EV_RESOLVE
        e[i, _SETTLED] = _EV_SETTLE
    return e.ravel()


_EVENTS = _event_table()


def legal_exposure(state: int, threshold_days: float) -> float:
    """Share of a state's DPD range at or beyond the legal threshold, in [0, 1]."""
    if state not in DPD_RANGE:
        return 0.0
    lo, hi = DPD_RANGE[state]
    if math.isinf(hi):
        return 1.0 if threshold_days <= lo else 0.0
    return float(min(max((hi - threshold_days) / (hi - lo), 0.0), 1.0))


def lever_log_odds(levers: Levers, config: EngineConfig) -> np.ndarray:
    """Per-state log-odds uplift on IMPROVING, relative to config.baseline.

    Zero for every state when levers == baseline (the observed matrices are
    then reproduced exactly). Non-zero only on delinquent rows.
    """
    b = config.baseline
    p, p0 = levers.placement_rate, b.placement_rate
    c = levers.agency_capacity / b.agency_capacity
    # p * ln(c * p0 / p): visits per placed account, for the placed share. It
    # tends to 0 as p -> 0 and is exactly 0 at the baseline (p = p0, c = 1).
    cap_term = p * math.log(c * p0 / p) if (p > 0 and p0 > 0) else 0.0
    field_term = config.field_effect * (p - p0) + config.capacity_elasticity * cap_term
    k, k0 = levers.commission_by_state(), b.commission_by_state()
    out = np.zeros(N_STATES)
    for i in DELINQUENT:
        legal = config.legal_effect * (legal_exposure(i, levers.legal_threshold_days)
                                       - legal_exposure(i, b.legal_threshold_days))
        out[i] = field_term + config.commission_elasticity * p * (k[i] - k0[i]) + legal
    return out


def _tilt(base: np.ndarray, shift: np.ndarray, better: np.ndarray, settle_h: np.ndarray) -> np.ndarray:
    """(..., 8, 8) matrices, (...) shifts -> (..., 8, 9) outcome distributions."""
    logw = DIRECTION * shift[..., None, None] + _BETTER * better[:, None]
    num = base * np.exp(logw)
    p = num / num.sum(axis=-1, keepdims=True)
    out = np.empty(p.shape[:-1] + (_N_OUT,))
    out[..., :N_STATES] = p * (1.0 - settle_h)[:, None]
    out[..., _SETTLED] = settle_h
    return out


class _TableIndex:
    """Row offsets for _tables, built once per chunk instead of every month."""

    def __init__(self, r: int, k: int):
        rows = np.repeat(np.arange(r, dtype=np.int64), N_STATES)
        self.r, self.k = r, k
        self.first_base = rows * (k + 1)
        self.bin_base = rows * k
        self.sentinel = np.full((r, 1), 2.0, np.float32)


def _tables(p9: np.ndarray, k: int, ti: "_TableIndex | None" = None) -> tuple[np.ndarray, np.ndarray]:
    """Row distributions (R, 9) -> flat thresholds (R*9, float32) and guide codes.

    thr[r, j] = P(outcome <= j) computed as 1 - (mass above j), so a row whose
    tail is exactly zero gets thresholds of exactly 1.0 and an absorbing row
    thresholds of exactly 0.0 — an impossible move stays impossible after the
    float32 cast. Column 8 is a sentinel 2.0 that ends every walk.
    guide[r, b] = #thresholds <= b/k, + 16 if a threshold falls strictly
    inside bin b (only those draws need the walk).
    """
    r = p9.shape[0]
    if ti is None or ti.r != r or ti.k != k:
        ti = _TableIndex(r, k)
    above = np.cumsum(p9[:, :0:-1], axis=1)[:, ::-1]                 # mass above j, j = 0..7
    thr = np.clip(1.0 - above, 0.0, 1.0).astype(np.float32)          # (R, 8)
    thr9 = np.concatenate([thr, ti.sentinel], axis=1).ravel()
    tk = (thr * np.float32(k)).ravel()
    first = np.minimum(np.ceil(tk), k).astype(np.int64)
    cnt = np.bincount(ti.first_base + first, minlength=r * (k + 1)).reshape(r, k + 1)
    guide = np.cumsum(cnt, axis=1, dtype=np.int8)[:, :k]
    fl = np.floor(tk)
    inside = (tk != fl) & (fl < k)
    impure = np.bincount(ti.bin_base[inside] + fl[inside].astype(np.int64), minlength=r * k) > 0
    guide = guide.reshape(-1)
    guide[impure] += np.int8(16)
    return thr9, guide


class _Buffers:
    """Per-chunk scratch arrays, allocated once and reused every month.

    A fresh multi-megabyte temporary per numpy call costs a page fault per
    page, and page faults serialise across threads on the address-space
    lock; reusing buffers through `out=` is what lets chunks run in parallel.
    """

    def __init__(self, shape: tuple[int, int]):
        self.u = np.empty(shape, np.float32)
        self.f = np.empty(shape, np.float32)
        self.bin = np.empty(shape, np.int32)
        self.key = np.empty(shape, np.int32)
        self.code = np.empty(shape, np.int8)
        self.mask = np.empty(shape, bool)


def _draw(idxk: np.ndarray, thr9: np.ndarray, guide: np.ndarray, k: int, buf: _Buffers) -> np.ndarray:
    """Inverse-CDF outcome (0..8) per element into buf.code; buf.u holds the uniforms.

    idxk = row id x k. One guide-table read settles every draw whose bin holds
    no threshold; the rest walk the thresholds from the guide's lower bound.
    Exact: outcome = #{j : thr_j <= u}, the same as a full comparison.
    """
    shift = k.bit_length() - 1
    np.multiply(buf.u, np.float32(k), out=buf.f)
    np.copyto(buf.bin, buf.f, casting="unsafe")      # floor, u >= 0
    np.add(idxk, buf.bin, out=buf.key)
    guide.take(buf.key, out=buf.code, mode="clip")
    np.greater_equal(buf.code, 16, out=buf.mask)
    imp = np.flatnonzero(buf.mask)
    if imp.size:
        cf = buf.code.reshape(-1)
        j = cf[imp] - np.int8(16)
        pos = (idxk.reshape(-1)[imp] >> shift) * _N_OUT + j
        uu = buf.u.reshape(-1)[imp]
        sub = np.arange(imp.size)
        while sub.size:
            m = uu[sub] >= thr9.take(pos[sub])
            sub = sub[m]
            pos[sub] += 1
            j[sub] += 1
        cf[imp] = j
    return buf.code


@dataclass
class _Context:
    n: int
    S: int
    T: int
    seg: np.ndarray
    state0: np.ndarray
    age0: np.ndarray | None
    bw: np.ndarray
    w: np.ndarray
    unit_weights: bool
    alpha: np.ndarray
    alpha_mean: np.ndarray
    identity_rows: np.ndarray
    mu: np.ndarray
    better: np.ndarray
    settle_h_rows: np.ndarray
    settle_recovery: float
    writeoff_months: int | None
    cure_a: np.ndarray
    cure_b: np.ndarray
    res_a: np.ndarray
    res_b: np.ndarray
    commission_frac: np.ndarray
    pd_h1: int
    pd_h2: int
    config: EngineConfig


def _run_chunk(ctx: _Context, p0: int, p1: int, ss: np.random.SeedSequence, out: PathOutputs) -> None:
    cfg = ctx.config
    pc, S, n, T, K = p1 - p0, ctx.S, ctx.n, ctx.T, cfg.guide_bins
    R = pc * S * N_STATES
    g_param, g_trans, g_rec = (np.random.Generator(np.random.PCG64(s)) for s in ss.spawn(3))
    eye = np.eye(N_STATES)

    # 1. This chunk's matrices: one Dirichlet draw per path and segment.
    if cfg.parameter_uncertainty:
        gam = g_param.standard_gamma(np.broadcast_to(ctx.alpha, (pc, S, N_STATES, N_STATES)))
        rs = gam.sum(axis=-1, keepdims=True)
        base = np.divide(gam, rs, out=np.zeros_like(gam), where=rs > 0)
        underflow = rs[..., 0] == 0  # all-tiny shapes can underflow; use the mean
        if underflow.any():
            base[underflow] = np.broadcast_to(ctx.alpha_mean, base.shape)[underflow]
    else:
        base = np.array(np.broadcast_to(ctx.alpha_mean, (pc, S, N_STATES, N_STATES)))
    base[:, ctx.identity_rows] = eye[np.nonzero(ctx.identity_rows)[1]]
    # NPA_SUB -> NPA_DOUBTFUL is the 12-month AGE RULE (states.py), applied in
    # the month loop below, never a matrix hazard. So: mass between the two
    # NPA states means "still an NPA" (folded into staying put), and entry
    # into NPA from outside is always Sub-standard.
    base[..., NPA_SUB, NPA_SUB] += base[..., NPA_SUB, NPA_DOUBTFUL]
    base[..., NPA_SUB, NPA_DOUBTFUL] = 0.0
    base[..., NPA_DOUBTFUL, NPA_DOUBTFUL] += base[..., NPA_DOUBTFUL, NPA_SUB]
    base[..., NPA_DOUBTFUL, NPA_SUB] = 0.0
    outside = [i for i in range(N_STATES) if i not in NPA]
    base[..., outside, NPA_SUB] += base[..., outside, NPA_DOUBTFUL]
    base[..., outside, NPA_DOUBTFUL] = 0.0
    if ctx.writeoff_months is not None:
        # The policy REPLACES the observed NPA write-off hazard (no double count).
        for i in NPA:
            base[..., i, i] += base[..., i, WRITTEN_OFF]
            base[..., i, WRITTEN_OFF] = 0.0

    # 2. Shocks: systematic Z (pc, T) and idiosyncratic eta (pc, T, S), AR(1).
    rho = cfg.shock_persistence
    z = g_param.standard_normal((pc, T))
    eta = g_param.standard_normal((pc, T, S))
    q = math.sqrt(1.0 - rho * rho)
    for t in range(1, T):
        z[:, t] = rho * z[:, t - 1] + q * z[:, t]
        eta[:, t] = rho * eta[:, t - 1] + q * eta[:, t]
    ws = cfg.systematic_share
    shift = ctx.mu[None, None, :] + cfg.shock_sigma * (math.sqrt(ws) * z[:, :, None] + math.sqrt(1.0 - ws) * eta)

    # 3. IFRS-9 PD per path: the path's matrix under the scenario mean (no
    #    random shock), default states made absorbing, first passage. Reaching
    #    WRITTEN_OFF counts as default here even though a written-off balance
    #    carries no ECL afterwards (derecognised, states.IFRS9_STAGE).
    m9 = _tilt(base, np.broadcast_to(ctx.mu, (pc, S)), ctx.better, ctx.settle_h_rows)
    m8 = m9[..., :N_STATES].copy()
    m8[..., RESOLVED] += m9[..., _SETTLED]
    for i in DEFAULT_STATES:
        m8[..., i, :] = eye[i]
    dflt = list(DEFAULT_STATES)
    pd1 = np.linalg.matrix_power(m8, ctx.pd_h1)[..., dflt].sum(axis=-1)
    pd2 = np.linalg.matrix_power(m8, ctx.pd_h2)[..., dflt].sum(axis=-1)
    stage = np.asarray(IFRS9_STAGE)
    out.pd_by_state[p0:p1] = np.where(stage == 1, pd1, np.where(stage == 2, pd2, np.where(stage == 3, 1.0, 0.0)))

    # 4. Account-level simulation. `state` and `idxk` (row id x K) are kept in
    #    step; aggregates are updated from the accounts that MOVED only (12.5%
    #    of account-months on the synthetic 50k book), never rebuilt from the
    #    whole book every month.
    shift_k = K.bit_length() - 1
    state = np.tile(ctx.state0, (pc, 1))
    rowbase = (np.arange(pc, dtype=np.int32)[:, None] * S + ctx.seg[None, :]) * N_STATES
    rows0 = (rowbase + state).ravel()
    cur_bal = np.bincount(rows0, weights=np.tile(ctx.bw, pc), minlength=R)
    cur_cnt = (np.bincount(rows0, minlength=R) if ctx.unit_weights
               else np.bincount(rows0, weights=np.tile(ctx.w, pc), minlength=R)).astype(np.float64)
    idxk = (rowbase + state) * np.int32(K)
    del rows0, rowbase
    out.seg_state_balance[p0:p1, 0] = cur_bal.reshape(pc, S, N_STATES)
    out.seg_state_count[p0:p1, 0] = cur_cnt.reshape(pc, S, N_STATES)
    # NPA age, per account per path. `entry` = the month end at which the
    # current NPA spell was first seen (age = now - entry; initial ages count
    # back from month 0). `promote` = the month end at which a Sub-standard
    # account turns Doubtful (entry + 12), or _NEVER — one equality test a
    # month finds exactly the accounts due, instead of re-deriving every age.
    entry = np.tile(-ctx.age0, (pc, 1)).astype(np.int16)
    promote = np.where(state == NPA_SUB, entry + DOUBTFUL_AFTER_MONTHS, _NEVER).astype(np.int16)
    state_f, idxk_f = state.reshape(-1), idxk.reshape(-1)
    entry_f, promote_f = entry.reshape(-1), promote.reshape(-1)
    buf = _Buffers((pc, n))
    ti = _TableIndex(R, K)
    bw_flat = np.tile(ctx.bw, pc)
    w_flat = None if ctx.unit_weights else np.tile(ctx.w, pc)

    def sum_by(key: np.ndarray, weights: np.ndarray) -> np.ndarray:
        return np.bincount(key, weights=weights, minlength=pc * S).reshape(pc, S)

    def move(flat: np.ndarray, outcome: np.ndarray, t: int) -> None:
        """Apply transitions to the listed elements and book their cash."""
        s_from = state_f[flat]
        s_to = np.minimum(outcome, np.int8(RESOLVED))
        row_from = idxk_f[flat] >> shift_k                  # (path, segment, state) row id
        row_to = row_from + (s_to - s_from)                 # int8 step, -7..7
        rows = np.concatenate((row_from, row_to))
        bwa = bw_flat[flat]
        cur_bal[:] += np.bincount(rows, weights=np.concatenate((-bwa, bwa)), minlength=R)
        if w_flat is None:
            wa = None
            cur_cnt[:] += np.bincount(row_to, minlength=R) - np.bincount(row_from, minlength=R)
        else:
            wa = w_flat[flat]
            cur_cnt[:] += np.bincount(rows, weights=np.concatenate((-wa, wa)), minlength=R)
        state_f[flat] = s_to
        idxk_f[flat] = row_to * K
        # NPA age: a new spell starts Sub-standard at this month end (the
        # matrix can no longer produce any other entry — see the fold above);
        # anything that is not Sub-standard now has no promotion pending.
        entering = (s_to == NPA_SUB) & (s_from != NPA_SUB) & (s_from != NPA_DOUBTFUL)
        promote_f[flat] = _NEVER
        if entering.any():
            fe = flat[entering]
            entry_f[fe] = t + 1
            promote_f[fe] = t + 1 + DOUBTFUL_AFTER_MONTHS

        ev = _EVENTS.take(s_from * np.int8(_N_OUT) + outcome)
        e = np.flatnonzero(ev)
        if not e.size:
            return
        code, s_old = ev[e], s_from[e]
        key = row_from[e] >> 3                              # path_local * S + segment
        bw_e = bwa[e]
        w_e = np.ones(e.size) if wa is None else wa[e]
        cash = np.zeros(e.size)
        c, r, st, wo = code == _EV_CURE, code == _EV_RESOLVE, code == _EV_SETTLE, code == _EV_WRITE_OFF
        if c.any():
            cash[c] = bw_e[c] * g_rec.beta(ctx.cure_a[s_old[c]], ctx.cure_b[s_old[c]])
        if r.any():
            cash[r] = bw_e[r] * g_rec.beta(ctx.res_a[s_old[r]], ctx.res_b[s_old[r]])
        if st.any():
            cash[st] = bw_e[st] * ctx.settle_recovery
        sl = np.s_[p0:p1, t]
        out.cure_cash[sl] += sum_by(key[c], cash[c])
        out.resolve_cash[sl] += sum_by(key[r], cash[r])
        out.settle_cash[sl] += sum_by(key[st], cash[st])
        out.cure_accounts[sl] += sum_by(key[c], w_e[c])
        out.resolve_accounts[sl] += sum_by(key[r], w_e[r])
        out.settle_accounts[sl] += sum_by(key[st], w_e[st])
        out.written_off_balance[sl] += sum_by(key[wo], bw_e[wo])
        out.written_off_accounts[sl] += sum_by(key[wo], w_e[wo])
        out.commission[sl] += sum_by(key, cash * ctx.commission_frac[s_old])

    for t in range(T):
        p9 = _tilt(base, shift[:, t, :], ctx.better, ctx.settle_h_rows).reshape(R, _N_OUT)
        thr9, guide = _tables(p9, K, ti)
        g_trans.random(out=buf.u, dtype=np.float32)
        outcome = _draw(idxk, thr9, guide, K, buf)
        np.not_equal(outcome, state, out=buf.mask)  # SETTLED (8) never equals a state
        mv = np.flatnonzero(buf.mask)
        if mv.size:
            move(mv, outcome.reshape(-1)[mv], t)
        # The age rule: Sub-standard for 12 months -> Doubtful, deterministically.
        np.equal(promote, t + 1, out=buf.mask)
        aged = np.flatnonzero(buf.mask)
        if aged.size:
            move(aged, np.full(aged.size, NPA_DOUBTFUL, dtype=np.int8), t)
        if ctx.writeoff_months is not None:
            in_npa = (state == NPA_SUB) | (state == NPA_DOUBTFUL)
            due = np.flatnonzero(in_npa & (entry <= t + 1 - ctx.writeoff_months))
            if due.size:
                move(due, np.full(due.size, WRITTEN_OFF, dtype=np.int8), t)
        out.seg_state_balance[p0:p1, t + 1] = cur_bal.reshape(pc, S, N_STATES)
        out.seg_state_count[p0:p1, t + 1] = cur_cnt.reshape(pc, S, N_STATES)


def _resolve_segment_attr(values: np.ndarray | None, segment: np.ndarray, S: int) -> tuple | None:
    """One value per segment when every account in it agrees, else None there."""
    if values is None:
        return None
    out: list = [None] * S
    for s in range(S):
        v = values[segment == s]
        if v.size:
            u = set(v.tolist())
            out[s] = next(iter(u)) if len(u) == 1 else None
    return tuple(out)


# ── The entry point ──────────────────────────────────────────────────────────


def simulate(
    portfolio: Portfolio,
    matrices: SegmentMatrices,
    *,
    scenario: MacroScenario = BASELINE,
    levers: Levers | None = None,
    recovery: RecoveryParams | None = None,
    ifrs9: Ifrs9Params | None = None,
    n_paths: int = 1000,
    horizon_months: int = 12,
    seed: int = 0,
    config: EngineConfig | None = None,
    max_accounts: int | None = None,
    progress: Callable[[float], None] | None = None,
    calibration=None,
) -> SimulationResult:
    """Run the Monte Carlo. Reproducible from (seed, inputs, config.chunk_paths)
    under one numpy version — numpy does not promise its Generator
    distributions are stable across versions, so the version is recorded in
    `diagnostics["numpy_version"]` and belongs beside the seed (task E03).

    `max_accounts` opts into a stratified subsample (Portfolio.subsample) for
    very large books; the full book is simulated by default. `progress` is
    called with the completed fraction after each chunk (for the Celery job,
    task E03).

    `calibration` is a backtest.BacktestReport for THIS book's history. Only
    a passing one, on non-synthetic history, run by this engine version,
    sets `calibrated_by_backtest` and clears `synthetic_warning`; anything
    else leaves the run marked uncalibrated (see _calibration_status).

    Memory: the per-path outputs are two (n_paths, horizon+1, segments, 8)
    float64 arrays — 1,000 paths x 13 months x 24 segments is ~40 MB, and
    10,000 paths x 100 segments ~1.7 GB. Working memory per thread is
    ~15 bytes per account x chunk_paths.
    """
    t_start = time.perf_counter()
    config = config or EngineConfig()
    levers = levers or config.baseline
    recovery = recovery or RecoveryParams.default()
    ifrs9 = ifrs9 or Ifrs9Params()
    config.validate()
    levers.validate()
    if n_paths < 2:
        raise ValueError("n_paths must be >= 2 (percentiles and standard errors need a spread)")
    if horizon_months < 1:
        raise ValueError("horizon_months must be >= 1")
    if seed < 0:
        raise ValueError("seed must be >= 0")
    S = matrices.n_segments
    if portfolio.segment.max() >= S:
        raise ValueError("portfolio.segment refers to a segment the matrices do not have")

    n_original = portfolio.n
    book = portfolio
    if max_accounts is not None and portfolio.n > max_accounts:
        book = portfolio.subsample(max_accounts, seed=seed)

    # Segment attributes: product decides the macro sensitivity.
    seg_lt = _resolve_segment_attr(book.loan_type, book.segment, S)
    seg_region = _resolve_segment_attr(book.region, book.segment, S)
    loan_types: list[str | None] = []
    for s in range(S):
        lt = matrices.loan_types[s] if matrices.loan_types is not None else None
        if lt is None and seg_lt is not None:
            lt = seg_lt[s]
        loan_types.append(lt)
    mu = np.array([macro_shift(scenario, lt, config.sensitivity_scale) for lt in loan_types])

    # NPA age at the start, reconciled with the 12-month rule. Unknown ages
    # take the state's lower bound (DATA-MODEL-V2 §9.5): Sub 0, Doubtful 12.
    # A known age that contradicts the state is resolved in favour of the
    # rule and counted, never silently: Sub-standard at 12+ months is
    # Doubtful; Doubtful under 12 months is read as exactly 12.
    state0 = book.state.copy()
    raw = np.full(book.n, -1.0) if book.npa_age_months is None else np.nan_to_num(book.npa_age_months, nan=-1.0)
    known = raw >= 0
    sub_aged = (state0 == NPA_SUB) & known & (raw >= DOUBTFUL_AFTER_MONTHS)
    doubtful_young = (state0 == NPA_DOUBTFUL) & known & (raw < DOUBTFUL_AFTER_MONTHS)
    state0[sub_aged] = NPA_DOUBTFUL
    lower = np.where(state0 == NPA_DOUBTFUL, DOUBTFUL_AFTER_MONTHS, 0)
    age0 = np.where(known, np.maximum(raw, lower), lower)
    age0 = np.where(np.isin(state0, NPA), age0, 0).astype(np.int16)

    alpha, no_evidence = matrices.dirichlet_alpha(config)
    alpha_mean = alpha / alpha.sum(axis=2, keepdims=True)
    held = np.zeros((S, N_STATES), dtype=bool)
    np.logical_or.at(held, (book.segment, state0), True)
    rows_held_in_place = [(matrices.keys[s], STATES[i]) for s, i in zip(*np.nonzero(no_evidence & held))]

    settle_h = levers.settlement_hazard()
    settle_rows = np.zeros(N_STATES)
    settle_rows[list(NPA)] = settle_h
    ca, cb, ra, rb = recovery.arrays()
    w = book.weights
    ctx = _Context(
        n=book.n, S=S, T=horizon_months, seg=book.segment, state0=state0, age0=age0,
        bw=book.balance * w, w=w, unit_weights=book.weight is None,
        alpha=alpha, alpha_mean=alpha_mean, identity_rows=no_evidence | _absorbing_mask(S),
        mu=mu, better=lever_log_odds(levers, config), settle_h_rows=settle_rows,
        settle_recovery=1.0 - levers.settlement_discount, writeoff_months=levers.writeoff_policy_months,
        cure_a=ca, cure_b=cb, res_a=ra, res_b=rb,
        commission_frac=levers.placement_rate * levers.commission_by_state() / 100.0,
        pd_h1=ifrs9.pd_horizon_stage1_months, pd_h2=ifrs9.pd_horizon_stage2_months, config=config,
    )

    P, T = n_paths, horizon_months
    stock = (P, T + 1, S, N_STATES)
    flow = (P, T, S)
    out = PathOutputs(
        seg_state_count=np.zeros(stock), seg_state_balance=np.zeros(stock),
        cure_cash=np.zeros(flow), resolve_cash=np.zeros(flow), settle_cash=np.zeros(flow),
        cure_accounts=np.zeros(flow), resolve_accounts=np.zeros(flow), settle_accounts=np.zeros(flow),
        written_off_balance=np.zeros(flow), written_off_accounts=np.zeros(flow),
        commission=np.zeros(flow), field_cost=np.zeros(flow), pd_by_state=np.zeros((P, S, N_STATES)),
        ecl_stage=np.zeros((P, T + 1, 3)), ead_stage=np.zeros((P, T + 1, 3)),
    )

    cp = config.chunk_paths
    bounds = [(a, min(a + cp, P)) for a in range(0, P, cp)]
    seeds = np.random.SeedSequence(seed).spawn(len(bounds))
    workers = config.n_workers or min(8, os.cpu_count() or 1)
    workers = max(1, min(workers, len(bounds)))
    done = 0
    if workers == 1:
        for (a, b), ss in zip(bounds, seeds):
            _run_chunk(ctx, a, b, ss, out)
            done += 1
            if progress:
                progress(done / len(bounds))
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_run_chunk, ctx, a, b, ss, out) for (a, b), ss in zip(bounds, seeds)]
            for f in as_completed(futures):
                f.result()
                done += 1
                if progress:
                    progress(done / len(bounds))

    # Field cost from the delinquent count at the START of each month.
    visits_per_delinquent = (config.visits_per_placed_account * config.baseline.placement_rate
                             * levers.agency_capacity / config.baseline.agency_capacity)
    delinquent = out.seg_state_count[:, :T][..., list(DELINQUENT)].sum(axis=-1)
    out.field_cost[:] = delinquent * visits_per_delinquent * config.field_visit_cost_inr

    # ECL = PD x LGD x EAD, per path, per stage (einsum: no book-sized temporary).
    lgd = ifrs9.lgd_by_state()
    stage = np.asarray(IFRS9_STAGE)
    for k in (1, 2, 3):
        m = stage == k
        out.ead_stage[..., k - 1] = out.seg_state_balance[..., m].sum(axis=(-1, -2))
        out.ecl_stage[..., k - 1] = np.einsum("ptsk,psk,k->pt", out.seg_state_balance[..., m],
                                              out.pd_by_state[..., m], lgd[m])

    per_path = _per_path_metrics(out)
    summary = {m: Band.of(v) for m, v in per_path.items()}
    lgd_stage = {1: ifrs9.lgd_stage1, 2: ifrs9.lgd_stage2, 3: ifrs9.lgd_stage3}
    staging = {}
    for k in (1, 2, 3):
        ead, ecl = out.ead_stage[..., k - 1], out.ecl_stage[..., k - 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            cov = np.where(ead > 0, ecl / np.where(ead > 0, ead, 1.0), np.nan)
        staging[f"stage{k}"] = {
            "EAD": Band.of(ead), "ECL": Band.of(ecl), "COVERAGE": Band.of(cov),
            "PD": Band.of(cov / lgd_stage[k]) if lgd_stage[k] > 0 else Band.of(np.full_like(cov, np.nan)),
        }

    synthetic_inputs = bool(portfolio.synthetic or matrices.synthetic)
    calibrated, calibration_ref = _calibration_status(calibration)
    if synthetic_inputs:
        warning = SYNTHETIC_WARNING
    elif not calibrated:
        warning = UNCALIBRATED_WARNING
    else:
        warning = None
    elapsed = time.perf_counter() - t_start
    return SimulationResult(
        engine_version=ENGINE_VERSION, seed=seed, n_paths=P, horizon_months=T,
        n_accounts=n_original, n_simulated_accounts=book.n, subsampled=book is not portfolio,
        segment_keys=matrices.keys, segment_loan_type=tuple(loan_types), segment_region=seg_region,
        scenario=scenario, levers=levers, config=config, paths=out, summary=summary, ifrs9=staging,
        elapsed_seconds=elapsed, synthetic_inputs=synthetic_inputs,
        calibrated_by_backtest=calibrated, synthetic_warning=warning, calibration=calibration_ref,
        assumptions=ASSUMPTIONS,
        diagnostics={
            "recovery_source": recovery.source,
            "numpy_version": np.__version__,
            "npa_sub_reclassified_doubtful_at_start": int(sub_aged.sum()),
            "npa_doubtful_age_raised_to_12_at_start": int(doubtful_young.sum()),
            "rows_held_in_place": rows_held_in_place,
            "segments_without_loan_type": [matrices.keys[s] for s, lt in enumerate(loan_types) if lt is None],
            "macro_shift_by_segment": dict(zip(matrices.keys, mu.round(6).tolist())),
            "lever_log_odds_by_state": dict(zip(STATES, ctx.better.round(6).tolist())),
            "settlement_hazard": settle_h,
            "chunks": len(bounds), "chunk_paths": cp, "workers": workers,
            "account_steps": int(book.n) * P * T,
        },
    )


def _calibration_status(report) -> tuple[bool, dict | None]:
    """(calibrated_by_backtest, reference) for a backtest.BacktestReport.

    Calibrated means: a PASSING backtest, on NON-synthetic history, run by
    THIS engine version. The source of the transition counts plays no part —
    real counts under unfitted elasticities are still uncalibrated.
    """
    if report is None:
        return False, None
    ref = {"engine_version": report.engine_version, "origin": report.origin,
           "horizon_months": report.horizon_months, "nominal": report.nominal,
           "coverage": report.coverage, "passes": bool(report.passes()),
           "synthetic": bool(report.synthetic)}
    ok = ref["passes"] and not ref["synthetic"] and report.engine_version == ENGINE_VERSION
    return bool(ok), ref


def _absorbing_mask(S: int) -> np.ndarray:
    m = np.zeros((S, N_STATES), dtype=bool)
    m[:, list(ABSORBING)] = True
    return m


# ── Tornado, sensitivity grid, scenario comparison ───────────────────────────

_MACRO_FIELDS = {"gdp", "cpi", "repo_bps", "unemployment", "sector"}
_LEVER_BOUNDS = {
    "placement_rate": (0.0, 1.0), "agency_capacity": (0.05, None),
    "settlement_discount": (0.0, 0.95), "legal_threshold_days": (1.0, None),
    "settlement_accept_max": (0.0, 1.0),
}
# One-factor-at-a-time swings: +/- this much around the current value.
DEFAULT_TORNADO_STEPS: dict[str, float] = {
    "gdp": 1.0, "cpi": 1.0, "repo_bps": 100.0, "unemployment": 1.0, "sector": 5.0,
    "placement_rate": 0.10, "agency_capacity": 0.20, "settlement_discount": 0.10,
    "legal_threshold_days": 30.0,
}


def with_factor(scenario: MacroScenario, levers: Levers, name: str, value: float) -> tuple[MacroScenario, Levers]:
    """Set one macro field or one scalar lever; the other object is unchanged."""
    if name in _MACRO_FIELDS:
        return replace(scenario, **{name: float(value)}), levers
    if name in _LEVER_BOUNDS:
        lo, hi = _LEVER_BOUNDS[name]
        v = max(value, lo) if lo is not None else value
        v = min(v, hi) if hi is not None else v
        return scenario, replace(levers, **{name: float(v)})
    raise ValueError(f"unknown factor {name!r}")


def factor_value(scenario: MacroScenario, levers: Levers, name: str) -> float:
    return float(getattr(scenario if name in _MACRO_FIELDS else levers, name))


def _stat(result: SimulationResult, metric: str, stat: str, period: int = -1) -> float:
    return float(np.asarray(getattr(result.summary[metric], stat))[period])


@dataclass(frozen=True)
class TornadoBar:
    factor: str
    low_value: float
    high_value: float
    metric_low: float
    metric_high: float
    base: float

    @property
    def swing(self) -> float:
        return abs(self.metric_high - self.metric_low)


def tornado(portfolio: Portfolio, matrices: SegmentMatrices, *, scenario: MacroScenario = BASELINE,
            levers: Levers | None = None, steps: Mapping[str, float] | None = None,
            metric: str = "GNPA_PCT", stat: str = "p50", n_paths: int = 200, horizon_months: int = 12,
            seed: int = 0, max_accounts: int | None = 20_000, **kwargs) -> list[TornadoBar]:
    """One factor at a time, low and high, everything else held. Sorted by swing.

    Every run shares the seed, so the transition uniforms and parameter draws
    are COMMON random numbers: the bars measure the factor, not Monte Carlo
    noise. Defaults to a 20k-account stratified subsample for speed.
    """
    config = kwargs.pop("config", None) or EngineConfig()
    levers = levers or config.baseline
    steps = dict(steps or DEFAULT_TORNADO_STEPS)

    def run(sc, lv):
        return simulate(portfolio, matrices, scenario=sc, levers=lv, n_paths=n_paths,
                        horizon_months=horizon_months, seed=seed, config=config,
                        max_accounts=max_accounts, **kwargs)

    base = _stat(run(scenario, levers), metric, stat)
    bars = []
    for name, step in steps.items():
        v = factor_value(scenario, levers, name)
        s_lo, l_lo = with_factor(scenario, levers, name, v - step)
        s_hi, l_hi = with_factor(scenario, levers, name, v + step)
        bars.append(TornadoBar(name, factor_value(s_lo, l_lo, name), factor_value(s_hi, l_hi, name),
                               _stat(run(s_lo, l_lo), metric, stat), _stat(run(s_hi, l_hi), metric, stat), base))
    return sorted(bars, key=lambda b: b.swing, reverse=True)


def sensitivity_grid(portfolio: Portfolio, matrices: SegmentMatrices, *, x_factor: str,
                     x_values: Sequence[float], y_factor: str, y_values: Sequence[float],
                     scenario: MacroScenario = BASELINE, levers: Levers | None = None,
                     metric: str = "GNPA_PCT", stat: str = "p50", n_paths: int = 100,
                     horizon_months: int = 12, seed: int = 0, max_accounts: int | None = 20_000,
                     **kwargs) -> dict:
    """Two factors swept on a grid, the rest held; `z[y][x]` at the horizon."""
    if x_factor == y_factor:
        raise ValueError("x_factor and y_factor must differ")
    config = kwargs.pop("config", None) or EngineConfig()
    levers = levers or config.baseline
    z = np.empty((len(y_values), len(x_values)))
    for yi, yv in enumerate(y_values):
        for xi, xv in enumerate(x_values):
            sc, lv = with_factor(scenario, levers, y_factor, yv)
            sc, lv = with_factor(sc, lv, x_factor, xv)
            res = simulate(portfolio, matrices, scenario=sc, levers=lv, n_paths=n_paths,
                           horizon_months=horizon_months, seed=seed, config=config,
                           max_accounts=max_accounts, **kwargs)
            z[yi, xi] = _stat(res, metric, stat)
    return {"x_factor": x_factor, "y_factor": y_factor, "x_values": list(map(float, x_values)),
            "y_values": list(map(float, y_values)), "metric": metric, "stat": stat, "z": z.tolist(),
            "z_min": float(np.nanmin(z)), "z_max": float(np.nanmax(z))}


HEADLINE_METRICS = ("GNPA_PCT", "RECOVERED_CASH", "ECL", "WRITE_OFFS", "COST", "NET_RECOVERY",
                    "WRITE_OFF_ACCOUNTS", "RECOVERED_ACCOUNTS")


def compare_scenarios(portfolio: Portfolio, matrices: SegmentMatrices,
                      scenarios: Mapping[str, MacroScenario | tuple[MacroScenario, Levers]], *,
                      n_paths: int = 500, horizon_months: int = 12, seed: int = 0,
                      metrics: Sequence[str] = HEADLINE_METRICS, **kwargs) -> dict:
    """Run named scenarios on common random numbers; compare against the first.

    The delta's standard error is PAIRED (sd of per-path differences / sqrt P):
    with a shared seed the same path meets every scenario, so a difference is
    far better resolved than either level. That is the reason to compare here
    rather than subtract two separately-run headlines.
    """
    if not scenarios:
        raise ValueError("at least one scenario is required")
    results: dict[str, SimulationResult] = {}
    for name, spec in scenarios.items():
        sc, lv = spec if isinstance(spec, tuple) else (spec, None)
        results[name] = simulate(portfolio, matrices, scenario=sc, levers=lv, n_paths=n_paths,
                                 horizon_months=horizon_months, seed=seed, **kwargs)
    names = list(results)
    ref = results[names[0]]
    table = []
    for name in names:
        res = results[name]
        for m in metrics:
            d = res.per_path(m)[:, -1] - ref.per_path(m)[:, -1]
            table.append({
                "scenario": name, "metric": m, "unit": METRIC_UNITS[m], **res.summary[m].at(-1),
                "delta_mean_vs_first": float(d.mean()),
                "delta_sem_paired": float(d.std(ddof=1) / math.sqrt(d.size)),
            })
    return {"reference": names[0], "results": results, "table": table}


# ── Fitting the capacity elasticity ──────────────────────────────────────────


def fit_cure_elasticity(log_intensity: Sequence[float] | np.ndarray, cured: Sequence[float] | np.ndarray,
                        at_risk: Sequence[float] | np.ndarray, max_iter: int = 50) -> tuple[float, float]:
    """Binomial logit of cures on log visit intensity: returns (slope, SE).

    One row per (segment, bucket, month) cell with `at_risk` delinquent
    accounts, `cured` of them improving, and `log_intensity` = ln(visits per
    placed account / status-quo visits). The slope is `capacity_elasticity`.
    Observational data confound effort with difficulty (agencies visit the
    accounts that need it), so read the slope as an upper-bounded association
    until an allocation experiment (the epsilon-greedy slice) supplies it.
    """
    x = np.asarray(log_intensity, dtype=float)
    y = np.asarray(cured, dtype=float)
    n = np.asarray(at_risk, dtype=float)
    if not (x.shape == y.shape == n.shape) or x.ndim != 1:
        raise ValueError("inputs must be 1-D and the same length")
    keep = n > 0
    x, y, n = x[keep], y[keep], n[keep]
    if x.size < 3 or np.ptp(x) == 0:
        raise ValueError("need >= 3 cells with varying intensity")
    X = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    for _ in range(max_iter):
        eta = X @ beta
        p = 1.0 / (1.0 + np.exp(-eta))
        wgt = n * p * (1 - p)
        grad = X.T @ (y - n * p)
        hess = X.T @ (X * wgt[:, None])
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < 1e-10:
            break
    p = 1.0 / (1.0 + np.exp(-(X @ beta)))
    cov = np.linalg.inv(X.T @ (X * (n * p * (1 - p))[:, None]))
    return float(beta[1]), float(math.sqrt(cov[1, 1]))


# ── Benchmark ────────────────────────────────────────────────────────────────


def benchmark(n_accounts: int = 50_000, n_paths: int = 1_000, horizon_months: int = 12,
              seed: int = 0, n_workers: int | None = None) -> dict:
    """Time a full run on a SYNTHETIC book (app.strategy.synthetic)."""
    from app.strategy.synthetic import synthetic_book
    portfolio, matrices = synthetic_book(n_accounts=n_accounts, seed=seed)
    cfg = EngineConfig(n_workers=n_workers)
    load_before = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    t, c = time.perf_counter(), time.process_time()
    res = simulate(portfolio, matrices, scenario=PRESETS["adverse"], n_paths=n_paths,
                   horizon_months=horizon_months, seed=seed, config=cfg)
    wall, cpu = time.perf_counter() - t, time.process_time() - c
    steps = n_accounts * n_paths * horizon_months
    # Wall time depends on what else the machine is doing; CPU time is the
    # work. Report both, with the load average, so a number can be read.
    return {"n_accounts": n_accounts, "n_paths": n_paths, "horizon_months": horizon_months,
            "segments": matrices.n_segments, "workers": res.diagnostics["workers"],
            "wall_seconds": round(wall, 2), "cpu_seconds": round(cpu, 2),
            "wall_ns_per_account_step": round(wall / steps * 1e9, 1),
            "cpu_ns_per_account_step": round(cpu / steps * 1e9, 1),
            "loadavg_1m_before": load_before, "cpu_count": os.cpu_count(),
            "gnpa_pct_p50_horizon": round(float(res.summary["GNPA_PCT"].p50[-1]), 3)}


if __name__ == "__main__":  # python -m app.strategy.monte_carlo
    import json
    import sys
    args = [int(a) for a in sys.argv[1:]]
    print(json.dumps(benchmark(*args), indent=2))
