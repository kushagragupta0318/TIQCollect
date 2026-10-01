"""The bank's borrower page (C08): one customer, their cases, one case's history.

Read-only. Bank from the caller's own row (RequestContext), region limit applied
through the loan; missing, another tenant's and outside-the-region are the same
404. The case view is `security_invoker`, so it is read on the tenant-bound
analytics session — that is what makes RLS apply to the caller.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import AnalyticsDb, DbSession
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.schemas.bank_customer_360 import CaseTimeline, Customer360
from app.services.bank import customer_360 as svc
from app.services.scope import region_limit_path

# `placement.read` is the capability the entry point (Placements) already
# requires, and the borrower page is the placed book seen one borrower at a
# time. There is no bank-side `cases.read` — that one is the agency's — and a
# new capability is a permissions-registry change another lane owns. An agency
# user holding placement.read still fails the BANK scope check in the service.
_BANK_READ = "placement.read"

router = APIRouter(prefix="/bank", tags=["bank-customers"])


@router.get("/customers/{customer_id}/360", response_model=Customer360)
def customer_360(customer_id: UUIDPath, ctx: CurrentContext, db: DbSession, adb: AnalyticsDb,
                 _user: User = require_perm(_BANK_READ)):
    return svc.customer_360(db, adb, ctx, customer_id, region_limit=region_limit_path(db, _user))


@router.get("/cases/{case_id}/timeline", response_model=CaseTimeline)
def case_timeline(case_id: UUIDPath, ctx: CurrentContext, db: DbSession,
                  _user: User = require_perm(_BANK_READ)):
    return svc.case_timeline(db, ctx, case_id, region_limit=region_limit_path(db, _user))
