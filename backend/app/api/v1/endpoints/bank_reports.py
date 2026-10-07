# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — New file. E10: the Board Reports page's API. GET /types lists
#   the packs this bank can generate; POST /generate renders one (E09's
#   app/reports engine) and returns a presigned download link. Gated by
#   `reports.generate` / `reports.download` (core/permissions.py, already
#   declared, unused until now) and scoped to the caller's own bank exactly
#   like bank.py's /overview.
# ────────────────────────────────────────────────────────────────────────────
"""Board Reports: list the available packs, and render one to PDF/PPTX/XLSX.

Thin by design: this module resolves the caller's bank (and agency, for the
agency_review template), builds the typed payload via
services/bank/report_templates.py, and hands it to app/reports/service.export,
which owns render → upload → presign → audit. No figure is computed here.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import AnalyticsDb, DbSession
from app.core.errors import AppException, ErrorCode
from app.core.ids import UUIDStr
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.tenancy import Agency, Bank
from app.models.user import User
from app.reports import service as report_service
from app.services.bank import report_templates
from app.services.bank.kpi_filter import FilterError, KpiFilter

router = APIRouter(prefix="/bank/reports", tags=["bank-reports"])


def _bank_of(ctx) -> str:
    if not ctx.bank_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    return ctx.bank_id


class ReportTypeOut(BaseModel):
    template: str
    name: str
    description: str
    requires_agency: bool


#: The two packs this lane builds (E09/E10's "one or two done well" scope).
#: Risk, Audit and Monthly MIS packs are templates E09's payload shape
#: already supports — adding one is a new builder in report_templates.py,
#: not a change to this route or the renderers.
REPORT_TYPES: tuple[ReportTypeOut, ...] = (
    ReportTypeOut(template="board", name="Board Pack",
                 description="Portfolio KPIs for the period, the trained model's live-equivalent "
                             "performance, and an AI-written summary — for the board.",
                 requires_agency=False),
    ReportTypeOut(template="agency_review", name="Agency Review",
                 description="One agency's scorecard for the latest available month: collections, "
                             "PTPs, SLA, cost and compliance.",
                 requires_agency=True),
)


@router.get("/types", response_model=list[ReportTypeOut], summary="Report packs this bank can generate")
def list_types(_user: User = require_perm("reports.generate")):
    return list(REPORT_TYPES)


class GenerateReportIn(BaseModel):
    template: Literal["board", "agency_review"]
    format: Literal["pdf", "pptx", "xlsx"]
    period: Literal["mtd", "l30", "qtd", "fytd", "custom"] = "mtd"
    start: Optional[date] = None
    end: Optional[date] = None
    #: Required for agency_review; ignored for board (the board pack is bank-wide).
    #: UUIDStr: a malformed id is a 422 here, never a Postgres DataError 500
    #: (db.get(Agency, ...) against a uuid column) the way plain str let through.
    agency_id: Optional[UUIDStr] = None


class GeneratedReportOut(BaseModel):
    report_id: str
    format: str
    url: str
    expires_minutes: int
    size_bytes: int
    sha256: str


@router.post("/generate", response_model=GeneratedReportOut,
            summary="Render a report pack and return a time-limited download link")
def generate(body: GenerateReportIn, ctx: CurrentContext, db: DbSession, adb: AnalyticsDb,
            _user: User = require_perm("reports.download")):
    bank_id = _bank_of(ctx)
    bank = db.get(Bank, bank_id)
    if bank is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Bank not found")

    if body.template == "agency_review":
        if not body.agency_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "agency_id is required for an agency review pack")
        agency = db.get(Agency, body.agency_id)
        if agency is None or str(agency.bank_id) != str(bank_id):
            raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
        payload = report_templates.build_agency_review_payload(adb, bank, agency)
    else:
        try:
            f = KpiFilter(period=body.period, start=body.start, end=body.end)
        except FilterError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        payload = report_templates.build_board_payload(adb, bank, f)

    try:
        exported = report_service.export(
            db, payload, body.format, user_id=ctx.user_id, bank_id=bank_id,
            endpoint="/bank/reports/generate",
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return GeneratedReportOut(report_id=exported.report_id, format=exported.fmt, url=exported.url,
                              expires_minutes=exported.expires_minutes, size_bytes=exported.size_bytes,
                              sha256=exported.sha256)
