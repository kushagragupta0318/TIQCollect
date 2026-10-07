"""GET /bank/audit — the bank's own and its agencies' recorded actions.

Its own module for the reason router.py records against bank_placements and
bank_models: one bank surface per file keeps two lanes building two pages out
of each other's merge conflicts.

The query, the scope and the honesty block live in
services/bank/audit_read.py, so the list and the CSV cannot disagree about who
may read what.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.audit import write_audit
from app.core.dependencies import DbSession
from app.core.ids import UUIDQuery
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.audit_log import AuditAction
from app.models.user import User
from app.services.bank import audit_read as ar

router = APIRouter(prefix="/bank", tags=["bank-audit"])

#: An export is a convenience. Unbounded, on a table with no retention sweep,
#: it is a way to take the API down from a browser.
MAX_EXPORT_ROWS = 10_000


def _bank_of(ctx) -> str:
    if not ctx.bank_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No bank on this account")
    return ctx.bank_id


class AuditRowOut(BaseModel):
    id: str
    created_at: Optional[str]
    action: str
    actor_name: Optional[str]
    actor_id: Optional[str]
    agency_id: Optional[str]
    entity_type: Optional[str]
    entity_id: Optional[str]
    success: bool
    failure_reason: Optional[str]
    ip_address: Optional[str]


class CoverageOut(BaseModel):
    declared_action_types: int
    sensitive_actions: list[str]
    pending_attribution: int
    note: str


class AuditPageOut(BaseModel):
    since: str
    total: int
    limit: int
    offset: int
    entries: list[AuditRowOut]
    counts_by_action: dict[str, int]
    coverage: CoverageOut


def _filters(action: Optional[str], actor_id: Optional[str],
             since: Optional[datetime], until: Optional[datetime]) -> ar.Filters:
    if since and until and until < since:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`until` is before `since`")
    return ar.Filters(action=action, actor_id=actor_id, since=since, until=until)


@router.get("/audit", response_model=AuditPageOut, summary="The bank's audit trail")
def bank_audit(
    ctx: CurrentContext, db: DbSession,
    action: Optional[str] = None,
    actor_id: UUIDQuery = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(ar.PAGE_SIZE, ge=1, le=ar.MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    _user: User = require_perm("bank.audit.read"),
):
    """This bank's actions and its agencies', newest first.

    Paginated deliberately: audit_logs has no retention sweep anywhere in the
    codebase, so it grows for the life of the deployment and an unbounded read
    here would get slower forever.
    """
    bank_id = _bank_of(ctx)
    f = _filters(action, actor_id, since, until)
    base = ar.scoped_query(db, bank_id, f)
    total = base.count()
    rows = base.offset(offset).limit(limit).all()
    names = ar.actor_names(db, rows)
    return AuditPageOut(
        since=f.window_start().isoformat(),
        total=total, limit=limit, offset=offset,
        entries=[AuditRowOut(**ar.row_out(r, names)) for r in rows],
        counts_by_action=ar.counts_by_action(db, bank_id, f),
        coverage=CoverageOut(**ar.coverage(db, f)),
    )


@router.get("/audit/export", summary="The same window as GET /bank/audit, as CSV")
def bank_audit_export(
    ctx: CurrentContext, db: DbSession,
    action: Optional[str] = None,
    actor_id: UUIDQuery = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    _user: User = require_perm("bank.audit.read"),
):
    """Reuses scoped_query, so the tenant scope cannot be present on one path
    and missing on the other.

    Capped at MAX_EXPORT_ROWS: an export is a convenience, and an unbounded one
    on a table with no retention sweep is a way to take the API down from a
    browser. The header row says when it was truncated, rather than handing
    over a short file that looks complete.
    """
    bank_id = _bank_of(ctx)
    f = _filters(action, actor_id, since, until)
    rows = ar.scoped_query(db, bank_id, f).limit(MAX_EXPORT_ROWS + 1).all()
    truncated = len(rows) > MAX_EXPORT_ROWS
    rows = rows[:MAX_EXPORT_ROWS]
    names = ar.actor_names(db, rows)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "action", "actor", "agency_id", "entity_type", "entity_id",
                "success", "failure_reason", "ip_address"])
    for r in rows:
        d = ar.row_out(r, names)
        w.writerow([d["created_at"], d["action"], d["actor_name"] or "(system)", d["agency_id"] or "",
                    d["entity_type"] or "", d["entity_id"] or "", d["success"],
                    d["failure_reason"] or "", d["ip_address"] or ""])
    if truncated:
        w.writerow([f"# truncated at {MAX_EXPORT_ROWS} rows; narrow the window or filter by action"])

    # An export of the audit trail that is itself unaudited is the defect
    # manager.py's export was fixed for on 2026-09-10. The row records the
    # window and the count, never the content: the export is reproducible from
    # those, and copying the payload in would duplicate the very data being
    # logged. bank_id is passed explicitly so the row is readable by the bank
    # whose trail was taken -- the listener would fill it from the actor too,
    # but this row is exactly the kind that must never go unattributed.
    write_audit(
        db, action=AuditAction.DATA_EXPORT, user_id=_user.id,
        bank_id=bank_id, agency_id=None,
        entity_type="AuditLog", entity_id=None,
        details={"format": "csv", "rows": len(rows), "truncated": truncated,
                 "since": f.window_start().isoformat(),
                 "until": f.until.isoformat() if f.until else None,
                 "action_filter": f.action, "actor_filter": f.actor_id,
                 "endpoint": "/bank/audit/export"},
    )
    db.commit()

    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.csv"'},
    )
