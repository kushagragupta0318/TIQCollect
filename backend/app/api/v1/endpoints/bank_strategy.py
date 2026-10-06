"""The bank's strategy simulator: POST /bank/strategy/simulate (plan §7, ADR 0014).

Thin — it validates the request, resolves the caller's bank, and calls
strategy.service.run_simulation, which owns the orchestration. Gated by
`strategy.simulate` (BANK_ADMIN and BANK_ANALYST; AGENCY_* never — the capability
seed already enforces this). Synchronous for now; the service seam is where E03's
Celery/persistence wraps.

The response carries the honesty stamp (synthetic + basis) beside every figure, so
a number can never be read without what it rests on.
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.dependencies import AnalyticsDb, DbSession
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
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
