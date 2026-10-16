# Bank loan-feed ingestion: upload a daily CSV, and read this bank's ingestion
# history. Routing and request shapes only — the rules live in
# services/bank/feed_upload_service.py, and ingestion itself stays in
# scripts/ingest_daily (one engine, shared with the CLI).
#
# AUTHORISATION. Every route is gated on the EXISTING `bank_feed.upload`
# capability (BANK_ADMIN / BANK_TECHOPS / SERVICE, declared sensitive in
# core/permissions.py and previously unused) — no new permission. The bank is
# read from the authenticated RequestContext; a bank id in the body or a form
# field is never authorization, so one bank cannot upload into another's scope
# or read another's history.
#
# No `from __future__ import annotations` (same reason as manager_agents_admin):
# FastAPI must resolve the endpoint annotations at import.
from datetime import date
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.core.dependencies import DbSession
from app.core.errors import AppException, ErrorCode
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.services.bank import feed_upload_service as svc

router = APIRouter(prefix="/bank", tags=["bank-feed"])


@router.post("/feed", status_code=201)
async def upload_feed_route(
    request: Request,
    ctx: CurrentContext,
    db: DbSession,
    file: UploadFile = File(...),
    business_date: Optional[str] = Form(default=None),
    _user: User = require_perm("bank_feed.upload"),
):
    """Upload one daily loan feed for the CALLER's bank.

    Re-uploading the same file for the same business date returns the SAME
    batch with `duplicate: true` rather than ingesting twice — the uniqueness
    constraint on (bank_id, feed_type, business_date, file_sha256) is what makes
    that safe under concurrency.
    """
    # Read with a hard ceiling rather than trusting Content-Length: the limit
    # must hold even if the header lies. One extra byte is enough to know the
    # file is over, so an oversized upload is never fully buffered.
    data = await file.read(svc.MAX_UPLOAD_BYTES + 1)
    if len(data) > svc.MAX_UPLOAD_BYTES:
        raise AppException(413, ErrorCode.VALIDATION_ERROR,
                           "File is larger than {} MB.".format(svc.MAX_UPLOAD_BYTES // (1024 * 1024)))

    day = None
    if business_date:
        try:
            day = date.fromisoformat(business_date)
        except ValueError:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               "business_date must be YYYY-MM-DD.")

    return svc.upload_feed(db, ctx, data=data, filename=file.filename, business_day=day)


@router.post("/feed/validate")
async def validate_feed_route(
    request: Request,
    ctx: CurrentContext,
    db: DbSession,
    file: UploadFile = File(...),
    _user: User = require_perm("bank_feed.upload"),
):
    """Dry-run the file checks and report what WOULD happen. Writes nothing —
    so a reviewer can see the blocking errors and warnings before committing to
    an ingestion."""
    data = await file.read(svc.MAX_UPLOAD_BYTES + 1)
    if len(data) > svc.MAX_UPLOAD_BYTES:
        raise AppException(413, ErrorCode.VALIDATION_ERROR,
                           "File is larger than {} MB.".format(svc.MAX_UPLOAD_BYTES // (1024 * 1024)))
    report = svc.validate_csv(data)
    return {"file_name": svc.display_filename(file.filename), **svc._validation_dict(report)}


@router.get("/feed")
def list_feed_batches_route(
    ctx: CurrentContext,
    db: DbSession,
    limit: int = 50,
    offset: int = 0,
    _user: User = require_perm("bank_feed.upload"),
):
    """This bank's ingestion history, newest first."""
    return svc.list_batches(db, ctx, limit=limit, offset=offset)


@router.get("/feed/{batch_id}")
def get_feed_batch_route(
    batch_id: UUIDPath,
    ctx: CurrentContext,
    db: DbSession,
    _user: User = require_perm("bank_feed.upload"),
):
    """One ingestion batch of this bank. Another bank's id is the same 404 a
    nonexistent one gets."""
    return svc.get_batch(db, ctx, batch_id)
