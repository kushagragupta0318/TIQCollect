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


def _bank_of(ctx) -> str:
    if not ctx.bank_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    return ctx.bank_id


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
    return run.headline()
