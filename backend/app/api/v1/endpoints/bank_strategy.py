"""The bank's strategy simulator: POST /bank/strategy/simulate (plan §7, ADR 0014);
the 13-week cash forecast: GET /bank/strategy/cash-forecast (plan §7, task E06).

Thin — it validates the request, resolves the caller's bank, and calls
strategy.service.run_simulation / strategy.cash_forecast.build_cash_forecast,
which own the orchestration. Gated by `strategy.simulate` / `strategy.forecast`
respectively (BANK_ADMIN and BANK_ANALYST; AGENCY_* never — the capability
seed already enforces this). Synchronous for now; the service seam is where E03's
Celery/persistence wraps.

The response carries the honesty stamp (synthetic + basis) beside every figure, so
a number can never be read without what it rests on.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.dependencies import AnalyticsDb, DbSession
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.strategy import cash_forecast as CF
from app.strategy.honesty import stamp_for_cash_forecast
from app.strategy.service import N_PATHS_CAP, SimulationInputs, run_simulation

router = APIRouter(prefix="/bank/strategy", tags=["bank-strategy"])


class SimulateIn(BaseModel):
    preset: str = "baseline"
    scenario: Optional[dict] = None
    levers: dict = Field(default_factory=dict)
    horizon_months: int = Field(12, ge=1, le=60)
    n_paths: int = Field(500, ge=1, le=N_PATHS_CAP)
    seed: int = 0
    # E05 fan chart: the full monthly bands (months 0..horizon_months) instead
    # of just headline()'s horizon value. Opt-in — one array per percentile
    # per metric is a bigger response, and the existing callers (memo, board
    # pack) only ever read the horizon figure.
    series: bool = False


class CashForecastWeekOut(BaseModel):
    week_start: str
    p10: float
    p50: float
    p90: float
    ptp_scheduled: float
    bottom_up: float
    top_down: float


class CashForecastBacktestOut(BaseModel):
    mape: Optional[float]
    n_folds: int
    calibrated: bool
    reason: str


class CashForecastOut(BaseModel):
    """Every field `cash_forecast_endpoint` actually builds, named here once.
    A bare dict (the /simulate route's own style) is an allowlist with no
    list — the next field someone adds to the handler's `out` dict would be
    silently dropped by a response_model written AFTER the fact without
    re-reading what the handler returns; this one is read off the handler
    below, not guessed."""
    weeks: list[CashForecastWeekOut]
    totals: dict[str, float]
    history_weeks: int
    reporting_lag_weeks: int
    ptp_honor_rate: Optional[float]
    ptp_resolved_count: int
    recovery_informed_total: float
    recovery_informed_loans: int
    backtest: CashForecastBacktestOut
    engine_version: str
    # The honesty stamp (strategy/honesty.py.HonestyStamp.as_fields()).
    synthetic: bool
    calibrated: bool
    data_version: str
    basis: str
    text: str


def _bank_of(ctx) -> str:
    if not ctx.bank_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    return ctx.bank_id


def _json_safe(value):
    """NaN -> null, recursively. Found by this endpoint's own first real HTTP
    test, not guessed: Band.of() deliberately leaves an undefined metric NaN
    rather than imputing 0 (no live book at month 0, an IFRS-9 stage with no
    exposure yet — ADR 0005, abstain rather than impute), and standard JSON has
    no NaN, so a short or small run — any run where an early month is
    genuinely undefined for some metric — 500'd the instant headline() tried
    to serialise it. This endpoint had never been called over real HTTP
    before (only the engine itself, service-level, was tested), so nothing
    had caught it. null is the honest "not computable here"; applied to the
    whole response, not just the new `series` field, since headline()'s own
    horizon value can be exactly as undefined as any other month."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@router.post("/simulate", summary="Run a Monte Carlo strategy simulation on the bank's book")
def simulate_endpoint(body: SimulateIn, ctx: CurrentContext, db: DbSession, adb: AnalyticsDb,
                      _user: User = require_perm("strategy.simulate")):
    """Run the engine forward on the caller's book. A bank with too little history
    ABSTAINS (422, INSUFFICIENT_HISTORY) rather than returning a fabricated matrix."""
    bank_id = _bank_of(ctx)
    inputs = SimulationInputs(
        preset=body.preset, scenario=body.scenario, levers=body.levers,
        horizon_months=body.horizon_months, n_paths=body.n_paths, seed=body.seed,
    )
    # AppException (abstain, bad preset/lever) is an HTTPException subclass: FastAPI
    # renders it with its typed code. The reads are on the tenant analytics session.
    run = run_simulation(db, adb, bank_id, inputs)
    out = run.headline()
    if body.series:
        # STATE_SHARE excluded, same as headline(): its Band is indexed
        # (month, state), not a flat per-month series, so its as_dict()
        # would be shaped differently from every other metric's.
        out["series"] = {m: b.as_dict() for m, b in run.result.summary.items() if m != "STATE_SHARE"}
    return _json_safe(out)


@router.get("/cash-forecast", response_model=CashForecastOut,
           summary="13-week forecast of weekly collection inflow, with p10/p50/p90 bands")
def cash_forecast_endpoint(ctx: CurrentContext, adb: AnalyticsDb,
                           as_of: Optional[date] = None,
                           _user: User = require_perm("strategy.forecast")):
    """Project the next 13 weeks of VERIFIED collections. A book with under
    `cash_forecast.MIN_WEEKS_HISTORY` weeks of VERIFIED payments — including
    none at all — ABSTAINS (422, INSUFFICIENT_HISTORY) rather than returning a
    forecast built on invented history."""
    bank_id = _bank_of(ctx)
    run = CF.build_cash_forecast(adb, bank_id, as_of=as_of)
    lag_note = (f", last {run.reporting_lag_weeks} week(s) treated as not-yet-reported"
               if run.reporting_lag_weeks else "")
    basis = (f"{run.history_weeks} week(s) of VERIFIED payments{lag_note}, Holt ETS "
             f"(alpha={run.alpha}, beta={run.beta}), {run.ptp_resolved_count} resolved PTP(s) "
             f"for the honor rate, {run.recovery_informed_loans} recovery_risk-scored loan(s) "
             f"with no active PTP")
    stamp = stamp_for_cash_forecast(run, data_version=(as_of or date.today()).isoformat(), basis=basis)
    out = {
        "weeks": [
            {"week_start": run.week_starts[i].isoformat(), "p10": run.p10[i], "p50": run.p50[i],
             "p90": run.p90[i], "ptp_scheduled": run.ptp_scheduled[i], "bottom_up": run.bottom_up[i],
             "top_down": run.top_down[i]}
            for i in range(len(run.week_starts))
        ],
        "totals": run.totals(),
        "history_weeks": run.history_weeks,
        "reporting_lag_weeks": run.reporting_lag_weeks,
        "ptp_honor_rate": run.ptp_honor_rate,
        "ptp_resolved_count": run.ptp_resolved_count,
        "recovery_informed_total": run.recovery_informed_total,
        "recovery_informed_loans": run.recovery_informed_loans,
        "backtest": run.backtest.as_dict(),
        "engine_version": run.engine_version,
        **stamp.as_fields(),
    }
    return _json_safe(out)
