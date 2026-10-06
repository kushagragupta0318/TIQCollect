"""Payment reversal: two-stage agency→bank void of a mistaken collection (#2).

Agency side (/manager, AGENCY_MANAGER/ADMIN): request, agency-approve, agency-reject.
Bank side (/bank, a bank role): final sign-off and reject — CROSS-TENANT, through l8's
scoped RequestContext, so until l8 merges the service refuses the bank stage (503).
Thin: the stage machine, the fiduciary separation and the atomic unwind live in
PaymentReversalService.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.services.payment_reversal_service import PaymentReversalService

# Agency-side routes share manager.py's /manager prefix; bank-side share /bank.
agency_router = APIRouter(prefix="/manager", tags=["payment-reversals"])
bank_router = APIRouter(prefix="/bank", tags=["payment-reversals"])
# Exported for router.py; it includes both.
router = agency_router


class ReversalRequestIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ReversalDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _out(req) -> dict:
    return {"id": req.id, "payment_id": req.payment_id, "case_id": req.case_id,
            "status": req.status.value, "reason": req.reason,
            "agency_requested_by_id": req.agency_requested_by_id,
            "agency_approved_by_id": req.agency_approved_by_id,
            "bank_approved_by_id": req.bank_approved_by_id,
            "agency_approved_at": req.agency_approved_at.isoformat() if req.agency_approved_at else None,
            "bank_approved_at": req.bank_approved_at.isoformat() if req.bank_approved_at else None}


# ── agency stage ─────────────────────────────────────────────────────────────
@agency_router.post("/payments/{payment_id}/reversal", summary="Request a payment reversal (AGENCY_MANAGER)")
def request_reversal(payment_id: UUIDPath, body: ReversalRequestIn, db: DbSession,
                     current_user: User = require_perm("payment.reversal.request")):
    return _out(PaymentReversalService(db).request_reversal(current_user, payment_id, body.reason))


@agency_router.post("/reversals/{request_id}/agency-approve",
                    summary="Agency approves a reversal, routing it to the bank (AGENCY_MANAGER/ADMIN)")
def agency_approve(request_id: UUIDPath, db: DbSession,
                   current_user: User = require_perm("payment.reversal.approve.agency")):
    return _out(PaymentReversalService(db).agency_approve(current_user, request_id))


@agency_router.post("/reversals/{request_id}/agency-reject", summary="Agency rejects a reversal")
def agency_reject(request_id: UUIDPath, body: ReversalDecisionIn, db: DbSession,
                  current_user: User = require_perm("payment.reversal.approve.agency")):
    return _out(PaymentReversalService(db).reject(current_user, request_id, body.note, by_bank=False))


# ── bank stage (cross-tenant, through l8's RequestContext) ───────────────────
@bank_router.post("/reversals/{request_id}/approve",
                  summary="Bank's fiduciary final sign-off; the ledger unwinds here (bank role)")
def bank_approve(request_id: UUIDPath, ctx: CurrentContext, db: DbSession,
                 current_user: User = require_perm("payment.reversal.approve.bank")):
    return _out(PaymentReversalService(db).bank_approve(current_user, request_id, scope=ctx))


@bank_router.post("/reversals/{request_id}/reject", summary="Bank rejects a reversal (bank role)")
def bank_reject(request_id: UUIDPath, body: ReversalDecisionIn, ctx: CurrentContext, db: DbSession,
                current_user: User = require_perm("payment.reversal.approve.bank")):
    return _out(PaymentReversalService(db).reject(current_user, request_id, body.note, by_bank=True, scope=ctx))
