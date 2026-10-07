"""GET /bank/usage — the bank's own LLM calls, tokens and cost (F11, Tech Ops
→ Usage & Cost).

Its own module for the reason bank_audit.py and bank_models.py each are: one
bank surface per file keeps two lanes building two pages out of each other's
merge conflicts.

The query, the scope and the honesty block live in
services/bank/usage_read.py, so this endpoint cannot disagree with a later
export about who may read what cost figures.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.core.dependencies import DbSession
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.schemas.bank_usage import (
    UsageByDayOut, UsageByFeatureOut, UsageCoverageOut, UsagePageOut, UsageTotalsOut,
)
from app.services.bank import usage_read as ur

router = APIRouter(prefix="/bank", tags=["bank-usage"])


def _bank_of(ctx) -> str:
    if not ctx.bank_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    return ctx.bank_id


def _filters(feature: Optional[str], since: Optional[datetime], until: Optional[datetime]) -> ur.Filters:
    if since and until and until < since:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`until` is before `since`")
    return ur.Filters(feature=feature, since=since, until=until)


@router.get("/usage", response_model=UsagePageOut, summary="This bank's LLM usage and cost")
def bank_usage(
    ctx: CurrentContext, db: DbSession,
    feature: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    _user: User = require_perm("llm_usage.read"),
):
    """Tokens, calls and estimated cost for this bank's own tenancy, over the
    last 30 days by default (`since`/`until` widen or narrow it), broken down
    by feature and by day.
    """
    bank_id = _bank_of(ctx)
    f = _filters(feature, since, until)
    return UsagePageOut(
        since=f.window_start().isoformat(),
        until=f.until.isoformat() if f.until else None,
        totals=UsageTotalsOut(**ur.totals(db, bank_id, f)),
        by_feature=[UsageByFeatureOut(**row) for row in ur.by_feature(db, bank_id, f)],
        by_day=[UsageByDayOut(**row) for row in ur.by_day(db, bank_id, f)],
        coverage=UsageCoverageOut(**ur.coverage(db, f)),
    )
