"""Orchestration for a strategy simulation: load → run → stamp.

The endpoint stays thin by calling run_simulation here; this is the seam E03's
Celery/persistence wraps later (it is pure orchestration, no HTTP, no response
shaping). It composes transitions.py (counts), history.py (the starting book),
monte_carlo.simulate (the engine) and honesty.py (the stamp) — no new rules.

Every book this runs on today is the generated demo book, so inputs are stamped
synthetic and the figure never travels without naming what it rests on (ADR 0014).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.services.bank.placement_engine import TIME_LIMIT_S
from app.strategy.backtest import npa_age_at
from app.strategy.history import read_panel
from app.strategy.honesty import HonestyStamp, stamp_for_simulation
from app.strategy.monte_carlo import (
    BASELINE, PRESETS, Levers, MacroScenario, Portfolio, SimulationResult, simulate,
)
from app.strategy.transitions import read_transitions

# The response is synchronous, so the run is bounded. n_paths is capped (plan §7);
# a very large book is subsampled (monte_carlo stratified subsample) so the solve
# stays inside the placement engine's shared wall-clock budget rather than being
# restated here.
N_PATHS_CAP = 1000
MAX_ACCOUNTS = 50_000
TIME_BUDGET_S = TIME_LIMIT_S


@dataclass(frozen=True)
class SimulationInputs:
    preset: str = "baseline"                      # a PRESETS key, or "custom"
    scenario: dict | None = None                  # custom macro changes when preset == "custom"
    levers: dict = field(default_factory=dict)    # Levers field overrides
    horizon_months: int = 12
    n_paths: int = 500
    seed: int = 0

    def resolve_scenario(self) -> MacroScenario:
        if self.preset == "custom":
            fields = {k: v for k, v in (self.scenario or {}).items()
                      if k in MacroScenario.__dataclass_fields__}
            return MacroScenario(name="Custom", **fields)
        if self.preset not in PRESETS:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               f"unknown preset {self.preset!r}; one of {sorted(PRESETS)} or 'custom'")
        return PRESETS[self.preset]

    def resolve_levers(self) -> Levers:
        bad = [k for k in self.levers if k not in Levers.__dataclass_fields__]
        if bad:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, f"unknown lever(s): {sorted(bad)}")
        return Levers(**self.levers)


def _portfolio_from_panel(reading, synthetic: bool) -> Portfolio:
    """The starting book: every loan's state at the latest month end. Mirrors how
    backtest builds its origin cohort — observed accounts only, npa age from the
    panel's own history (npa_age_at), never invented."""
    panel = reading.panel
    origin = panel.n_months - 1
    observed = np.flatnonzero(panel.state[:, origin] >= 0)
    if observed.size == 0:
        raise AppException(422, ErrorCode.INSUFFICIENT_HISTORY,
                           "No loan is observed at the latest month end; nothing to simulate forward.")
    age, _ = npa_age_at(panel, origin)
    return Portfolio(
        state=panel.state[observed, origin],
        balance=panel.balance_at(origin)[observed],
        segment=panel.segment[observed],
        npa_age_months=np.where(age[observed] >= 0, age[observed], np.nan),
        synthetic=synthetic,
    )


@dataclass(frozen=True)
class SimulationRun:
    result: SimulationResult
    stamp: HonestyStamp
    segments: int
    month_ends: tuple[date, ...]

    def headline(self) -> dict:
        """The numbers a memo may quote, with the stamp beside them — never without."""
        return {**self.result.headline(), **self.stamp.as_fields()}


def run_simulation(db: Session, adb: Session, bank_id: str, inputs: SimulationInputs,
                   *, as_of: date | None = None, synthetic: bool = True, progress=None) -> SimulationRun:
    """Load the bank's book and transition counts, run the engine forward, stamp it.

    Raises AppException(INSUFFICIENT_HISTORY) — recorded as an ABSTAINED run — when
    the book is too short for transition counts or has no current snapshot. `db` is
    reserved for E03's run record; the reads are all on the tenant analytics session.
    """
    n_paths = max(1, min(inputs.n_paths, N_PATHS_CAP))
    scenario, levers = inputs.resolve_scenario(), inputs.resolve_levers()

    transitions = read_transitions(adb, bank_id, as_of=as_of, synthetic=synthetic)
    reading = read_panel(adb, bank_id, as_of=as_of, synthetic=synthetic)
    portfolio = _portfolio_from_panel(reading, synthetic)

    result = simulate(
        portfolio, transitions.matrices, scenario=scenario, levers=levers,
        n_paths=n_paths, horizon_months=inputs.horizon_months, seed=inputs.seed,
        max_accounts=MAX_ACCOUNTS, progress=progress,
    )
    data_version = reading.month_ends[-1].isoformat() if reading.month_ends else "unknown"
    basis = (f"{portfolio.state.shape[0]} {'synthetic ' if synthetic else ''}loans at "
             f"{data_version}, transitions from {transitions.months} month-ends "
             f"({transitions.matrices.n_segments} segments), {n_paths} paths over "
             f"{inputs.horizon_months} months under '{scenario.name}'")
    stamp = stamp_for_simulation(result, data_version=data_version, basis=basis)
    return SimulationRun(result=result, stamp=stamp, segments=transitions.matrices.n_segments,
                         month_ends=reading.month_ends)
