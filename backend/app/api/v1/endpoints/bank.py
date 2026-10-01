"""The bank portal's API (plan §5). Every route is gated by a capability and
scoped to the caller's own bank (RequestContext.bank_id, read from the user row
on every request, never from the token)."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.dependencies import AnalyticsDb, DbSession
from app.core.errors import AppException, ErrorCode
from app.core.ids import UUIDQuery
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.loan import DPDBucket, LoanType
from app.models.tenancy import Agency, Bank, Region
from app.models.user import User
from app.services.bank.analytics_catalog import TABS as ANALYTICS_TABS
from app.services.bank.analytics_catalog import compute_tab
from app.services.bank.kpi_catalog import ROWS, compute_overview
from app.services.bank.kpi_filter import PERIODS, FilterError, KpiFilter

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


def _bank_of(ctx) -> str:
    if not ctx.bank_id:
        # cc.read is held only by bank roles, which always carry a bank; refuse rather than guess one.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    return ctx.bank_id


def _own(db, model, row_id: Optional[str], bank_id: str) -> None:
    """A region or agency id from another bank reads exactly like one that does not exist."""
    if row_id is not None:
        row = db.get(model, row_id)
        if row is None or str(row.bank_id) != str(bank_id):
            raise AppException(404, ErrorCode.NOT_FOUND, "Not found")


@router.get("/overview", response_model=OverviewOut, summary="Command Center overview: the twelve header KPIs")
def overview(ctx: CurrentContext, db: DbSession, adb: AnalyticsDb,
             period: Literal["mtd", "l30", "qtd", "fytd", "custom"] = "mtd",
             start: Optional[date] = None, end: Optional[date] = None,
             geo: UUIDQuery = None, agency: UUIDQuery = None,
             product: Optional[LoanType] = None, bucket: Optional[DPDBucket] = None,
             security: Optional[Literal["SECURED", "UNSECURED"]] = None,
             _user: User = require_perm("cc.read")):
    bank_id = _bank_of(ctx)
    _own(db, Region, geo, bank_id)
    _own(db, Agency, agency, bank_id)
    try:
        f = KpiFilter(period=period, start=start, end=end, geo=geo, agency=agency,
                      product=product.value if product else None, bucket=bucket.value if bucket else None,
                      security=security)
    except FilterError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    bank = db.get(Bank, bank_id)
    # The KPIs read the analytics session bound to the caller's tenant (43, B13b):
    # the scoped views return only this bank's rows, and nothing if unbound.
    ov = compute_overview(adb, bank_id, f)
    return OverviewOut(
        bank_name=bank.display_name if bank else "",
        as_of=ov.as_of,
        kpis=[KpiOut(**{k: v for k, v in kpi.items() if k in KpiOut.model_fields}) for kpi in ov.kpis],
        rows=[KpiRowOut(**r) for r in ROWS],
        totals=[TotalOut(**t) for t in ov.totals],
        narrative=NarrativeOut(sentences=ov.narrative),
        notes=ov.notes,
    )


class AnalyticsTabOut(BaseModel):
    tab: str
    available: bool
    reason: Optional[str] = None
    #: Each tab's own chart-ready shape (plan §5.4) — structurally different
    #: per tab (a funnel is not a transition matrix), so unlike KpiOut this
    #: is not forced into one row shape. Every number in it is a real read
    #: from the scoped views, never invented (same rule as the KPIs).
    panels: dict


@router.get("/analytics/{tab}", response_model=AnalyticsTabOut, summary="Analytics tab (plan §5.4)")
def analytics_tab(tab: Literal["exposure", "migration", "agencies", "compliance"],
                  ctx: CurrentContext, db: DbSession, adb: AnalyticsDb,
                  period: Literal["mtd", "l30", "qtd", "fytd", "custom"] = "mtd",
                  start: Optional[date] = None, end: Optional[date] = None,
                  geo: UUIDQuery = None, agency: UUIDQuery = None,
                  product: Optional[LoanType] = None, bucket: Optional[DPDBucket] = None,
                  security: Optional[Literal["SECURED", "UNSECURED"]] = None,
                  _user: User = require_perm("cc.read")):
    bank_id = _bank_of(ctx)
    _own(db, Region, geo, bank_id)
    _own(db, Agency, agency, bank_id)
    try:
        f = KpiFilter(period=period, start=start, end=end, geo=geo, agency=agency,
                      product=product.value if product else None, bucket=bucket.value if bucket else None,
                      security=security)
    except FilterError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    assert tab in ANALYTICS_TABS, tab   # Literal above already refused anything else
    out = compute_tab(db, adb, tab, bank_id, f)
    return AnalyticsTabOut(tab=tab, available=out["available"], reason=out["reason"], panels=out["panels"])


class Option(BaseModel):
    value: str
    label: str
    level: Optional[str] = None
    parent: Optional[str] = None


class FiltersOut(BaseModel):
    periods: list[Option]
    geography: list[Option]
    agencies: list[Option]
    products: list[Option]
    buckets: list[Option]
    security: list[Option]


_PERIOD_LABELS = {"mtd": "Month to date", "l30": "Last 30 days", "qtd": "Quarter to date",
                  "fytd": "Financial year to date", "custom": "Custom range"}
_LEVEL_ORDER = {"ZONE": 0, "REGION": 1, "STATE": 2, "CITY": 3}


@router.get("/filters", response_model=FiltersOut, summary="The global filter bar's options for this bank")
def filters(ctx: CurrentContext, db: DbSession, _user: User = require_perm("cc.read")):
    bank_id = _bank_of(ctx)
    regions = db.query(Region).filter(Region.bank_id == bank_id, Region.is_active.is_(True)).all()
    agencies = db.query(Agency).filter(Agency.bank_id == bank_id).order_by(Agency.legal_name).all()
    return FiltersOut(
        periods=[Option(value=p, label=_PERIOD_LABELS[p]) for p in PERIODS],
        geography=[Option(value=str(r.id), label=r.name, level=r.level,
                          parent=str(r.parent_id) if r.parent_id else None)
                   for r in sorted(regions, key=lambda r: (_LEVEL_ORDER.get(r.level, 9), r.name))],
        agencies=[Option(value=str(a.id), label=a.trade_name or a.legal_name, level=a.status) for a in agencies],
        products=[Option(value=t.value, label=t.value.replace("_", " ").title()) for t in LoanType],
        buckets=[Option(value=b.value, label=b.value.replace("_", " ").title()) for b in DPDBucket],
        security=[Option(value="SECURED", label="Secured"), Option(value="UNSECURED", label="Unsecured")],
    )
