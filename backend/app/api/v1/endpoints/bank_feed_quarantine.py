# Quarantine review and release for bank feed rows (Phase 6). Routing and
# request shapes only — the rules live in
# services/bank/feed_quarantine_service.py, and placement itself stays in
# services/placement_service.py (one engine, shared with the nightly feed).
#
# AUTHORISATION. Reviewing and releasing a held feed row is feed work, so both
# are gated on the EXISTING `bank_feed.upload` capability (BANK_ADMIN /
# BANK_TECHOPS / SERVICE, already declared sensitive in core/permissions.py).
# No new capability and no new role list: the bank comes from the authenticated
# RequestContext, so a bank id in the path or body is never authorization.
#
# RAW BORROWER PII (Phase 9, RISK-1). `include_raw=true` is a REQUEST, not a
# permission. It is honoured only for a caller holding the separate
# `bank_feed.pii.read` capability (BANK_ADMIN only), and only once the read has
# been recorded as a DATA_EXPORT audit row — if that row cannot be persisted,
# the response carries no raw data (fail closed).
#
# A reviewer WITHOUT that grant keeps the whole workflow — held rows, reasons,
# account numbers, Release — and simply never receives `raw`. That is why these
# routes stay gated on `bank_feed.upload` and the PII check lives in the
# service: refusing the whole listing with a 403 would take quarantine review
# away from the people whose job it is.
#
# No `from __future__ import annotations` (same reason as bank_feed): FastAPI
# must resolve the endpoint annotations at import.
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.errors import AppException, ErrorCode
from app.core.ids import UUIDPath, parse_uuid_or_404
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.services.bank import feed_quarantine_service as svc

router = APIRouter(prefix="/bank/feed", tags=["bank-feed"])


def _client_ip(request: Request) -> str | None:
    """The caller's address for the audit row, or None behind a proxy that did
    not forward one. Never raises: a missing client must not fail the read."""
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


class ReleaseRequest(BaseModel):
    """The rows a reviewer chose to release. Explicit ids only — there is no
    "release everything" flag, because releasing a row is a decision per row
    and a blanket switch invites releasing what was not reviewed."""
    row_ids: List[str] = Field(..., min_length=1, max_length=svc.MAX_RELEASE_ROWS)
    # The placement date. Defaults to today; named so a reviewer clearing a
    # backlog can place on the business date it belongs to.
    on: Optional[date] = None


@router.get("/{batch_id}/quarantine")
async def list_quarantine_route(
    batch_id: UUIDPath,
    request: Request,
    ctx: CurrentContext,
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_raw: bool = Query(
        default=False,
        description="Request the row as received (borrower PII). Honoured only for a caller "
                    "holding bank_feed.pii.read, and audited as DATA_EXPORT."),
    _user: User = require_perm("bank_feed.upload"),
):
    """The quarantined rows of one of the CALLER's own batches, with the reason
    each one is held and whether it can be released from here.

    Counts are the database's own, over the whole batch rather than the page
    returned, so a paged review never under-reports what is held.
    """
    return svc.list_quarantined(db, ctx, batch_id, limit=limit, offset=offset,
                                include_raw=include_raw,
                                ip_address=_client_ip(request),
                                user_agent=request.headers.get("user-agent"))


@router.get("/quarantine/rows/{row_id}")
async def get_quarantine_row_route(
    row_id: UUIDPath,
    request: Request,
    ctx: CurrentContext,
    db: DbSession,
    include_raw: bool = Query(default=False),
    _user: User = require_perm("bank_feed.upload"),
):
    """One held row. Another bank's row id is the SAME 404 a nonexistent id
    gets, so this cannot be used to probe what another bank holds."""
    return svc.get_row(db, ctx, row_id, include_raw=include_raw,
                       ip_address=_client_ip(request),
                       user_agent=request.headers.get("user-agent"))


@router.post("/{batch_id}/quarantine/release")
async def release_quarantine_route(
    batch_id: UUIDPath,
    body: ReleaseRequest,
    request: Request,
    ctx: CurrentContext,
    db: DbSession,
    _user: User = require_perm("bank_feed.upload"),
):
    """Re-try the named held rows through the existing placement rules.

    This is a RE-EVALUATION, not a correction: a row whose refusal reason has
    not actually been fixed is refused again and stays quarantined. The
    response reports a verdict PER ROW — released, still refused, not
    releasable, already released — because a selection of mixed rows is the
    normal case and one overall status could not describe it honestly.
    """
    if not body.row_ids:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Name at least one row to release.")
    # Normalised to canonical UUIDs here, so a malformed id is the same 404 a
    # missing row gets rather than a driver error leaking out of the query.
    row_ids = [parse_uuid_or_404(r) for r in body.row_ids]
    return svc.release_rows(db, ctx, batch_id, row_ids, on=body.on,
                            ip_address=getattr(getattr(request, "client", None), "host", None))
