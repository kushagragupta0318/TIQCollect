"""The bank's placement API (plan §6.3, STANDALONE-TASKS D08): pick loans,
place them with an agency within its capacity and coverage, recall one
placement by hand, and list placements.

Every route is gated by a capability and scoped by RequestContext (bank from
the user row, never the token or the body), and by the caller's region limit
(users.scope_region_id) where one is set, on every route alike. A foreign or
out-of-region id answers exactly as a missing one does (404). The work is in services/; this file only maps HTTP.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.dependencies import AnalyticsDb, DbSession
from app.core.errors import AppException, ErrorCode
from app.core.ids import UUIDPath, UUIDQuery, UUIDStr
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext, RequestContext
from app.models.loan import DPDBucket, LoanType
from app.models.placement import PLACEMENT_STATUSES
from app.models.user import User
from app.schemas.placements import (
    AgencyDecisionsOut, AgencyRunsOut, BankDecisionsOut, BankRunsOut, DecisionsOut, PlacementsPageOut, RunsOut,
)
from app.services.bank import placement_engine as engine
from app.services.manual_placement_service import MAX_BATCH, MAX_RECALL_NOTE, BatchResult, ManualPlacementService
from app.services.placement_read_service import MAX_PAGE_SIZE, LoanFilter, PlacementReadService
from app.services.scope import access_day, region_limit_path

router = APIRouter(prefix="/bank/placements", tags=["bank-placements"])


def _bank_id(ctx: RequestContext) -> str:
    # Fail closed: a bank-scoped route needs a bank on the caller's own row.
    if ctx.scope != "BANK" or not ctx.bank_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "A bank user is required")
    return ctx.bank_id


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class BatchIn(BaseModel):
    agency_id: UUIDStr
    loan_ids: list[UUIDStr] = Field(min_length=1, max_length=MAX_BATCH)


class RecallIn(BaseModel):
    reason: str = Field(min_length=1, max_length=MAX_RECALL_NOTE)


def _batch_out(out: BatchResult) -> dict:
    return {
        "run_id": out.run_id, "agency_id": out.agency_id, "on": out.on.isoformat(),
        "headroom_before": out.headroom_before,
        "counts": {k: out.count(k) for k in ("PLACED", "KEPT", "BLOCKED")},
        "verdicts": [v.__dict__ for v in out.verdicts],
    }


@router.get("/loans")
def list_loans(
    ctx: CurrentContext, db: DbSession,
    _user: User = require_perm("placement.manual"),
    region_id: UUIDQuery = None,
    branch_code: Optional[str] = Query(None, max_length=20),
    loan_type: Optional[LoanType] = None,
    dpd_bucket: Optional[DPDBucket] = None,
    dpd_min: Optional[int] = Query(None, ge=0, le=10_000),
    dpd_max: Optional[int] = Query(None, ge=0, le=10_000),
    outstanding_min: Optional[float] = Query(None, ge=0),
    outstanding_max: Optional[float] = Query(None, ge=0),
    placed: Literal["no", "yes", "any"] = "no",
    search: Optional[str] = Query(None, max_length=30),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
):
    f = LoanFilter(region_id=region_id, branch_code=branch_code, loan_type=loan_type, dpd_bucket=dpd_bucket,
                   dpd_min=dpd_min, dpd_max=dpd_max, outstanding_min=outstanding_min,
                   outstanding_max=outstanding_max, placed={"no": False, "yes": True, "any": None}[placed],
                   search=search)
    bank_id = _bank_id(ctx)
    return PlacementReadService(db).loans(bank_id, f, region_limit=region_limit_path(db, _user),
                                          page=page, page_size=page_size)


@router.get("/agencies")
def list_agencies(ctx: CurrentContext, db: DbSession, _user: User = require_perm("placement.manual")):
    """The bank's agencies with their contract in force, room and coverage today."""
    day = access_day()
    return {"on": day.isoformat(), "items": PlacementReadService(db).agencies(_bank_id(ctx), day)}


@router.post("/preview")
def preview(body: BatchIn, ctx: CurrentContext, db: DbSession,
            _user: User = require_perm("placement.manual")):
    bank_id = _bank_id(ctx)
    out = ManualPlacementService(db).preview(bank_id=bank_id, agency_id=body.agency_id,
                                             loan_ids=list(body.loan_ids), on=access_day(),
                                             region_limit=region_limit_path(db, _user))
    return _batch_out(out)


@router.post("")
def place(body: BatchIn, request: Request, ctx: CurrentContext, db: DbSession,
          _user: User = require_perm("placement.manual")):
    bank_id = _bank_id(ctx)
    out = ManualPlacementService(db).apply(bank_id=bank_id, actor_id=ctx.user_id, agency_id=body.agency_id,
                                           loan_ids=list(body.loan_ids), on=access_day(), ip_address=_ip(request),
                                           region_limit=region_limit_path(db, _user))
    return _batch_out(out)


@router.post("/{placement_id}/recall")
def recall(placement_id: UUIDPath, body: RecallIn, request: Request, ctx: CurrentContext, db: DbSession,
           _user: User = require_perm("placement.recall")):
    bank_id = _bank_id(ctx)
    placement, closed = ManualPlacementService(db).recall(
        bank_id=bank_id, actor_id=ctx.user_id, placement_id=placement_id, note=body.reason,
        on=access_day(), ip_address=_ip(request), region_limit=region_limit_path(db, _user))
    return {"placement_id": placement.id, "status": placement.status, "ended_on": placement.ended_on.isoformat(),
            "end_reason": placement.end_reason, "cases_closed": [c.id for c in closed]}


@router.get("", response_model=PlacementsPageOut)
def list_placements(
    ctx: CurrentContext, db: DbSession,
    _user: User = require_perm("placement.read"),
    status: Optional[Literal[PLACEMENT_STATUSES]] = None,   # type: ignore[valid-type]
    agency_id: UUIDQuery = None,
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
):
    """Placements the bank made, or (agency roles) the ones their agency received."""
    if not ctx.bank_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "A bank or agency user is required")
    if ctx.scope == "BANK":
        own_agency = None
        # A bank user with scope_region_id set sees only that region's subtree.
        region_limit = region_limit_path(db, _user)
    elif ctx.scope == "AGENCY" and ctx.agency_id:
        own_agency = ctx.agency_id
        region_limit = None
    else:
        raise AppException(403, ErrorCode.FORBIDDEN, "A bank or agency user is required")
    return PlacementReadService(db).placements(bank_id=ctx.bank_id, agency_id=own_agency, status=status,
                                               filter_agency_id=agency_id, region_limit=region_limit,
                                               page=page, page_size=page_size)


# ── The placement engine (D09, ADR 0010) ─────────────────────────────────────

class RunIn(BaseModel):
    mode: Literal["simulate", "plan"]
    #: ADR 0010: off unless asked for; never above 0.20.
    exploration_rate: Optional[float] = Field(None, ge=0.0, le=0.20)


def _engine_bank_id(ctx: RequestContext, db, user: User) -> str:
    # The engine works on the whole book; a region-limited user may not run
    # or apply it (fail closed rather than plan outside their region).
    bank_id = _bank_id(ctx)
    if region_limit_path(db, user) is not None:
        raise AppException(403, ErrorCode.FORBIDDEN, "The placement engine needs a user without a region limit")
    return bank_id


@router.post("/runs")
def plan_run(body: RunIn, ctx: CurrentContext, db: DbSession, adb: AnalyticsDb,
             _user: User = require_perm("placement.run")):
    bank_id = _engine_bank_id(ctx, db, _user)
    rate = settings.PLACEMENT_EXPLORATION_RATE if body.exploration_rate is None else body.exploration_rate
    try:
        run = engine.plan_run(db, adb, bank_id=bank_id, actor_id=ctx.user_id, today=access_day(),
                              simulate=body.mode == "simulate", exploration_rate=rate)
    except engine.EngineTooSlow as slow:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, str(slow))
    return engine.run_out(run)


def _read_scope(ctx: RequestContext) -> tuple[str, str | None]:
    """(bank_id, own agency or None): bank users read the bank's runs, agency
    users only what their agency was given (the list_placements split)."""
    if ctx.bank_id and ctx.scope == "BANK":
        return ctx.bank_id, None
    if ctx.bank_id and ctx.scope == "AGENCY" and ctx.agency_id:
        return ctx.bank_id, ctx.agency_id
    raise AppException(403, ErrorCode.FORBIDDEN, "A bank or agency user is required")


@router.get("/runs", response_model=RunsOut)
def list_runs(ctx: CurrentContext, db: DbSession, _user: User = require_perm("placement.read"),
              limit: int = Query(30, ge=1, le=100)):
    bank_id, agency_id = _read_scope(ctx)
    items = engine.list_runs(db, bank_id, agency_id=agency_id, limit=limit)
    return AgencyRunsOut(items=items) if agency_id is not None else BankRunsOut(items=items)


@router.get("/runs/{run_id}/decisions", response_model=DecisionsOut)
def run_decisions(run_id: UUIDPath, ctx: CurrentContext, db: DbSession,
                  _user: User = require_perm("placement.read"),
                  outcome: Optional[Literal["PLACED", "DEFERRED", "BLOCKED", "RECALLED"]] = None,
                  page: int = Query(1, ge=1, le=100_000), page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE)):
    bank_id, agency_id = _read_scope(ctx)
    out = engine.run_decisions(db, bank_id=bank_id, run_id=run_id, outcome=outcome, page=page,
                               page_size=page_size, agency_id=agency_id)
    return AgencyDecisionsOut(**out) if agency_id is not None else BankDecisionsOut(**out)


@router.post("/runs/{run_id}/apply")
def apply_run(run_id: UUIDPath, request: Request, ctx: CurrentContext, db: DbSession,
              _user: User = require_perm("placement.run")):
    bank_id = _engine_bank_id(ctx, db, _user)
    run = engine.apply_run(db, bank_id=bank_id, run_id=run_id, actor_id=ctx.user_id, today=access_day(),
                           ip_address=_ip(request))
    return engine.run_out(run)
