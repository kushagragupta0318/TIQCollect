"""Payment reversal: a two-stage agency→bank void of a mistaken collection (#2).

Owner's flow (2026-10-01): the agency raises the reversal and approves its own side
(AGENCY_MANAGER), then it routes to the BANK, whose sign-off is fiduciary and final —
the ledger unwinds only on bank approval. The agency cannot both initiate and bless
a reversal; the bank sign-off is the real second pair of eyes.

Stages:
  request_reversal (AM)        -> PENDING_AGENCY
  agency_approve   (AM/AA)     -> PENDING_BANK          (no ledger change)
  bank_approve     (bank)      -> APPROVED + _unwind    (CROSS-TENANT, via l8 scope)
  reject           (either)    -> REJECTED              (nothing moved)

The bank stage is cross-tenant (a bank user acting on a row the agency raised). It
goes through l8's scoped RequestContext — NOT hand-rolled here. Until l8 merges,
_require_bank_scope is the seam: it refuses rather than guessing an authorization.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.payment import Payment, PaymentStatus
from app.models.payment_reversal import OPEN_REVERSAL_STATUSES, PaymentReversalRequest, ReversalStatus
from app.models.ptp import PTP, PTPStatus
from app.services.ptp_lifecycle_service import verified_paid_against

# The bank stage is cross-tenant and must go through l8's scoped RequestContext
# (f8's sanctioned mechanism), not a hand-rolled bank_id match. l8 is in the merge
# wave; until it lands this stays False and the bank stage refuses (503) rather than
# authorising a cross-tenant write on a money path. When l8 merges: flip to True and
# replace _request_for_bank's bank_id check with l8's RequestContext scope.
_L8_SCOPE_AVAILABLE = False


class PaymentReversalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── stage 1: request (AGENCY_MANAGER) ────────────────────────────────────
    def request_reversal(self, user, payment_id: str, reason: str) -> PaymentReversalRequest:
        if not (reason or "").strip():
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "A reversal needs a reason.")
        payment = self._payment_in_agency(user, payment_id)
        if payment.status == PaymentStatus.REVERSED:
            raise AppException(409, ErrorCode.CONFLICT, "This payment is already reversed.")
        if (self.db.query(PaymentReversalRequest)
                .filter(PaymentReversalRequest.payment_id == payment.id,
                        PaymentReversalRequest.status.in_(OPEN_REVERSAL_STATUSES)).first()) is not None:
            raise AppException(409, ErrorCode.CONFLICT, "A reversal for this payment is already open.")

        req = PaymentReversalRequest(
            bank_id=payment.bank_id, agency_id=payment.agency_id, payment_id=payment.id,
            case_id=payment.case_id, reason=reason.strip(),
            status=ReversalStatus.PENDING_AGENCY, agency_requested_by_id=user.id,
        )
        self.db.add(req)
        self.db.flush()
        self._audit(AuditAction.PAYMENT_REVERSAL_REQUESTED, user, req, payment,
                    extra={"stage": "AGENCY_REQUEST", "reason": req.reason})
        self.db.commit()
        self.db.refresh(req)
        return req

    # ── stage 2: agency approval (AM/AA) — routes to the bank, no ledger change ─
    def agency_approve(self, user, request_id: str) -> PaymentReversalRequest:
        req = self._request_in_agency(user, request_id)
        if req.status != ReversalStatus.PENDING_AGENCY:
            raise AppException(409, ErrorCode.CONFLICT, f"This reversal is already {req.status.value}.")
        now = datetime.now(timezone.utc)
        req.status = ReversalStatus.PENDING_BANK
        req.agency_approved_by_id = user.id
        req.agency_approved_at = now
        self.db.commit()          # routed to the bank; nothing on the ledger yet
        self.db.refresh(req)
        return req

    # ── stage 3: bank final sign-off (bank role, CROSS-TENANT via l8) — unwinds ─
    def bank_approve(self, user, request_id: str, *, scope) -> PaymentReversalRequest:
        """`scope` is l8's RequestContext — the sanctioned cross-tenant mechanism.
        The row was raised by the agency; a bank user may act on it only within that
        scope. Not hand-rolled: _require_bank_scope refuses until l8 provides it."""
        self._require_bank_scope(user, scope)
        req = self._request_for_bank(user, request_id, scope)
        if req.status != ReversalStatus.PENDING_BANK:
            raise AppException(409, ErrorCode.CONFLICT, f"This reversal is not awaiting the bank ({req.status.value}).")
        # Fiduciary separation: the bank sign-off is never one of the agency actors.
        if str(user.id) in {str(req.agency_requested_by_id), str(req.agency_approved_by_id)}:
            raise AppException(403, ErrorCode.FORBIDDEN,
                               "The bank sign-off must be a different person from the agency approver.")
        payment = self.db.get(Payment, req.payment_id)
        if payment is None or payment.status == PaymentStatus.REVERSED:
            raise AppException(409, ErrorCode.CONFLICT, "This payment is already reversed or missing.")

        now = datetime.now(timezone.utc)
        self._unwind(payment, actor=user, now=now)      # the ledger unwinds HERE, on bank sign-off
        req.status = ReversalStatus.APPROVED
        req.bank_approved_by_id = user.id
        req.bank_approved_at = now
        self._audit(AuditAction.PAYMENT_REVERSED, user, req, payment,
                    extra={"stage": "BANK_APPROVE",
                           "agency_requested_by": str(req.agency_requested_by_id),
                           "agency_approved_by": str(req.agency_approved_by_id)})
        self.db.commit()
        self.db.refresh(req)
        return req

    # ── reject (agency at stage 1, bank at stage 2) ──────────────────────────
    def reject(self, user, request_id: str, note: str | None, *, by_bank: bool, scope=None) -> PaymentReversalRequest:
        if by_bank:
            self._require_bank_scope(user, scope)
            req = self._request_for_bank(user, request_id, scope)
            if req.status != ReversalStatus.PENDING_BANK:
                raise AppException(409, ErrorCode.CONFLICT, f"This reversal is not awaiting the bank ({req.status.value}).")
        else:
            req = self._request_in_agency(user, request_id)
            if req.status not in (ReversalStatus.PENDING_AGENCY, ReversalStatus.PENDING_BANK):
                raise AppException(409, ErrorCode.CONFLICT, f"This reversal is already {req.status.value}.")
        req.status = ReversalStatus.REJECTED
        req.decision_note = note
        self.db.commit()          # a rejection touches no ledger; the row is its record
        self.db.refresh(req)
        return req

    # ── the unwind: everything collect_payment did, in reverse, one tx ───────
    def _unwind(self, payment: Payment, *, actor, now: datetime) -> None:
        case = self.db.get(Case, payment.case_id)
        amount = float(payment.amount)
        was_verified = payment.status == PaymentStatus.VERIFIED
        payment.status = PaymentStatus.REVERSED       # first, so verified_paid_against excludes it
        self.db.flush()

        case.collected_amount = round(max(0.0, case.collected_amount - amount), 2)
        # Re-open ONLY a payment-driven close. A case closed for another reason
        # (CLOSED / WRITTEN_OFF / SETTLED / legal) keeps its status and resolved_at —
        # the reversal adjusts the ledger but does not drag a written-off case back
        # into collections. The ledger change surfaces it for human review.
        if case.status in (CaseStatus.PAID, CaseStatus.PARTIALLY_PAID):
            case.status = CaseStatus.IN_PROGRESS if case.collected_amount <= 0 else CaseStatus.PARTIALLY_PAID
            case.resolved_at = None

        if payment.agent_id:
            from app.models.agent import Agent
            agent = self.db.get(Agent, payment.agent_id)
            if agent is not None:
                # current_month_collections is a running counter zeroed monthly
                # (performance_snapshot) and incremented at collection time, so it holds
                # only THIS month. Decrement only when the payment is in the current IST
                # month; reversing an earlier month's payment must not under-report this
                # one (the counter never held it).
                from app.core.geo import IST
                pay_ist, now_ist = payment.payment_date.astimezone(IST), now.astimezone(IST)
                if (pay_ist.year, pay_ist.month) == (now_ist.year, now_ist.month):
                    agent.current_month_collections = round(
                        max(0.0, float(agent.current_month_collections or 0.0) - amount), 2)

        # Un-honor a PTP only if it now falls below its committed amount with this
        # payment excluded; one honoured by other payments stays honoured.
        if was_verified and payment.agent_id:
            for ptp in (self.db.query(PTP)
                        .filter(PTP.case_id == payment.case_id, PTP.agent_id == payment.agent_id,
                                PTP.status == PTPStatus.HONORED).all()):
                still_paid = verified_paid_against(self.db, ptp, through=ptp.committed_date)
                if still_paid < float(ptp.committed_amount):
                    previous = str(ptp.status)
                    ptp.status = PTPStatus.ACTIVE
                    ptp.actual_paid_amount = still_paid
                    self.db.add(AuditLog(
                        created_at=now, user_id=actor.id, action=AuditAction.PTP_UPDATED,
                        entity_type="PTP", entity_id=ptp.id,
                        details={"from": previous, "to": str(PTPStatus.ACTIVE), "reason": "PAYMENT_REVERSED",
                                 "committed_amount": float(ptp.committed_amount),
                                 "verified_paid_after_reversal": still_paid, "reversed_payment_id": payment.id},
                        success=True))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _audit(self, action, user, req, payment, *, extra: dict) -> None:
        self.db.add(AuditLog(
            created_at=datetime.now(timezone.utc), user_id=user.id, action=action,
            entity_type="Payment", entity_id=payment.id,
            details={"reversal_request_id": req.id, "amount": payment.amount,
                     "case_id": payment.case_id, "bank_id": req.bank_id, "agency_id": req.agency_id, **extra},
            success=True))

    def _payment_in_agency(self, user, payment_id: str) -> Payment:
        payment = self.db.get(Payment, payment_id)
        if payment is None or str(payment.agency_id) != str(getattr(user, "agency_id", None)):
            raise AppException(404, ErrorCode.NOT_FOUND, "Payment not found.")
        return payment

    def _request_in_agency(self, user, request_id: str) -> PaymentReversalRequest:
        req = self.db.get(PaymentReversalRequest, request_id)
        if req is None or str(req.agency_id) != str(getattr(user, "agency_id", None)):
            raise AppException(404, ErrorCode.NOT_FOUND, "Reversal request not found.")
        return req

    def _require_bank_scope(self, user, scope) -> None:
        """The cross-tenant seam. l8's RequestContext authorizes a bank user to act
        on a row the agency raised; until it is wired, refuse rather than hand-roll a
        cross-tenant grant on a money path (f8, fc). Drops in when l8 merges."""
        if not _L8_SCOPE_AVAILABLE or scope is None:
            raise AppException(503, ErrorCode.SERVICE_UNAVAILABLE,
                               "Bank reversal sign-off is awaiting the scoped-access release (l8).")

    def _request_for_bank(self, user, request_id: str, scope) -> PaymentReversalRequest:
        """Read a reversal for the bank user, scoped by their bank. Replace the
        bank_id match with l8's RequestContext check when it lands (same result:
        a bank sees only its own agencies' reversals)."""
        req = self.db.get(PaymentReversalRequest, request_id)
        if req is None or str(req.bank_id) != str(getattr(user, "bank_id", None)):
            raise AppException(404, ErrorCode.NOT_FOUND, "Reversal request not found.")
        return req
