"""The bank portal's API (plan §5). Every route is gated by a capability and
scoped to the caller's own bank (RequestContext.bank_id, read from the user row
on every request, never from the token)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.dependencies import DbSession
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.tenancy import Bank
from app.models.user import User
from app.services.bank.kpi_catalog import ROWS, compute_overview

router = APIRouter(prefix="/bank", tags=["bank"])


class KpiOut(BaseModel):
    id: str
    label: str
    value: str
    sub: str
    trend: str
    trendUp: Optional[bool]
    good: Optional[bool]
    basis: str
    drill: str
    available: bool
    reason: Optional[str] = None


class KpiRowOut(BaseModel):
    id: str
    caption: str
    kpis: list[str]


class TotalOut(BaseModel):
    label: str
    value: str
    basis: str


class NarrativeOut(BaseModel):
    sentences: list[str]
    #: How the text was produced. "rules": fixed sentences over the numbers above.
    #: The page must not present it as AI.
    generated_by: str = "rules"


class OverviewOut(BaseModel):
    bank_name: str
    as_of: Optional[date]
    kpis: list[KpiOut]
    rows: list[KpiRowOut]
    totals: list[TotalOut]
    narrative: NarrativeOut
    notes: list[str]


@router.get("/overview", response_model=OverviewOut, summary="Command Center overview: the twelve header KPIs")
def overview(ctx: CurrentContext, db: DbSession,
             as_of: Optional[date] = Query(None, description="Reading date; defaults to the latest reading"),
             _user: User = require_perm("cc.read")):
    if not ctx.bank_id:
        # cc.read is held only by bank roles, which always carry a bank; refuse rather than guess one.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    bank = db.get(Bank, ctx.bank_id)
    ov = compute_overview(db, ctx.bank_id, as_of)
    return OverviewOut(
        bank_name=bank.display_name if bank else "",
        as_of=ov.as_of,
        kpis=[KpiOut(**{k: v for k, v in kpi.items() if k in KpiOut.model_fields}) for kpi in ov.kpis],
        rows=[KpiRowOut(**r) for r in ROWS],
        totals=[TotalOut(**t) for t in ov.totals],
        narrative=NarrativeOut(sentences=ov.narrative),
        notes=ov.notes,
    )
