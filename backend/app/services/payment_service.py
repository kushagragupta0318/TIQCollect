# ─── CHANGELOG (prototype → product) ───
# New file, 2026-07-21. Second router→service extraction of this session
# (after visit_service.py et al.), absorbing collect_payment/
# create_payment_link/set_ptp out of agent.py per final_changes.md §7.1
# item #4. Pulled ahead of the other 5 not-started services specifically
# because this is regulated money-movement — see
# prototype_to_product/21.07.md for the sequencing rationale.
#
# Extraction is otherwise byte-for-byte from the code it replaces (same
# "pure extraction, not a rewrite" discipline used for visit_service.py):
# same query order, same validation order, same status transitions, same
# SMS/WhatsApp copy. The one genuinely new behavior is the idempotency
# guard on collect_payment (below), mirroring the duplicate-submit-window
# pattern already proven in visit_service.py — added because
# final_changes.md §8 and the TRD both call out idempotency as
# non-negotiable specifically for payment writes, and it's cheaper to
# build it in now than retrofit it later the way visit_service.py needed
# to. See changelog.md for full detail, verification, and open gaps
# (payment-link has no equivalent guard yet — noted there, not silently
# skipped).
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.payment import Payment
from app.models.ptp import PTP, PTPStatus
from app.services.notification_service import NotificationService


class PaymentService:
    """Business logic for payment collection, Razorpay QR links, and PTPs.
    No HTTP knowledge — callable from a router, a Celery task, or a
    WebSocket handler without duplicating any of this."""

    # Same window/rationale as visit_service.py's duplicate-submit guard:
    # a flaky field connection retrying the same request must not create
    # two Payment rows (double-counted collection) or two PTP rows.
    _DUPLICATE_SUBMIT_WINDOW_SECONDS = 15

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _generate_receipt() -> str:
        year = datetime.now().year
        token = uuid.uuid4().hex[:8].upper()
        return f"TIQ-{year}-{token}"

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/payment
    # -----------------------------------------------------------------
    def collect_payment(self, agent, case_id: str, req) -> dict:
        case = self.db.query(Case).filter(Case.id == case_id, Case.agent_id == agent.id).first()
        if not case:
            raise AppException(404, ErrorCode.CASE_NOT_FOUND, "Case not found or not assigned to you")

        existing = self._find_recent_duplicate(case.id, agent.id, req)
        if existing:
            return self._payment_response(existing, case)

        now_utc = datetime.now(timezone.utc)
        payment = Payment(
            case_id=case.id,
            visit_id=req.visit_id,
            agent_id=agent.id,
            amount=req.amount,
            mode=req.mode,
            receipt_number=self._generate_receipt(),
            upi_reference=req.upi_reference,
            cheque_number=req.cheque_number,
            bank_reference=req.bank_reference,
            receipt_photo_key=req.receipt_photo_key,
            payment_date=now_utc,
        )
        self.db.add(payment)

        remaining = round(case.target_amount - case.collected_amount, 2)
        if req.amount > remaining:
            raise AppException(
                400, ErrorCode.VALIDATION_ERROR,
                f"Payment ₹{req.amount:,.2f} exceeds remaining balance ₹{remaining:,.2f}",
            )
        case.collected_amount = round(case.collected_amount + req.amount, 2)
        if case.collected_amount >= case.target_amount:
            case.status = CaseStatus.PAID
            case.resolved_at = now_utc
        else:
            case.status = CaseStatus.PARTIALLY_PAID

        agent.current_month_collections += req.amount

        self.db.commit()
        self.db.refresh(payment)

        self._notify_payment_received(agent, case, payment, req)
        return self._payment_response(payment, case)

    def _find_recent_duplicate(self, case_id: str, agent_id: str, req) -> Payment | None:
        window_start = datetime.now(timezone.utc) - timedelta(seconds=self._DUPLICATE_SUBMIT_WINDOW_SECONDS)
        return (
            self.db.query(Payment)
            .filter(
                Payment.case_id == case_id,
                Payment.agent_id == agent_id,
                Payment.amount == req.amount,
                Payment.mode == req.mode,
                Payment.payment_date >= window_start,
            )
            .order_by(Payment.payment_date.desc())
            .first()
        )

    def _notify_payment_received(self, agent, case, payment, req) -> None:
        customer = self.db.query(Customer).filter(Customer.id == case.customer_id).first()
        loan = self.db.query(Loan).filter(Loan.id == case.loan_id).first()
        if not customer or not customer.phone_primary:
            return
        e164 = "+" + NotificationService.normalize_phone(customer.phone_primary)
        masked_phone = "XXXXXX" + e164[-4:]
        masked_acct = "XXXX" + loan.loan_account_number[-4:] if loan else "XXXXXXXX"
        pay_date = payment.payment_date.strftime("%d %b %Y")
        mode_label = {"CASH": "Cash", "UPI": "UPI", "CHEQUE": "Cheque", "NEFT": "NEFT", "RTGS": "RTGS"}.get(str(req.mode), str(req.mode))
        sms_body = (
            f"Dear {customer.full_name}, ABC Bank's agent {agent.user.full_name} visited on {pay_date}. "
            f"Rs.{req.amount:,.0f} received via {mode_label} for loan {masked_acct}. "
            f"Receipt: {payment.receipt_number}. Mobile: {masked_phone}. - ABC Bank"
        )
        wa_body = (
            f"*Visit Completed & Payment Received – ABC Bank*\n\n"
            f"Dear {customer.full_name},\n\n"
            f"\U0001f3e0 Agent *{agent.user.full_name}* visited on {pay_date}.\n"
            f"✅ Rs.{req.amount:,.0f} received via *{mode_label}*\n"
            f"\U0001f4b3 Loan Account: {masked_acct}\n"
            f"\U0001f9fe Receipt No: {payment.receipt_number}\n"
            f"\U0001f4f1 Mobile: {masked_phone}\n\n"
            f"Thank you for your payment.\n– ABC Bank"
        )
        NotificationService.send_twilio(e164, sms_body, wa_body)

    @staticmethod
    def _payment_response(payment: Payment, case: Case) -> dict:
        return {
            "id": payment.id,
            "receipt_number": payment.receipt_number,
            "amount": payment.amount,
            "mode": payment.mode,
            "status": payment.status,
            "payment_date": payment.payment_date.isoformat(),
            "case_status": case.status,
            "total_collected": case.collected_amount,
        }

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/payment-link  (Razorpay UPI QR)
    # -----------------------------------------------------------------
    def create_payment_link(self, case_id: str, amount: float) -> dict:
        import razorpay
        if not settings.RAZORPAY_TEST_API or not settings.RAZORPAY_TEST_KEY_SECRET:
            raise AppException(503, ErrorCode.VALIDATION_ERROR, "Razorpay not configured")

        case = self.db.query(Case).filter(Case.id == case_id).first()
        if not case:
            raise AppException(404, ErrorCode.CASE_NOT_FOUND, "Case not found")

        close_at = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())

        client = razorpay.Client(auth=(settings.RAZORPAY_TEST_API, settings.RAZORPAY_TEST_KEY_SECRET))
        qr = client.qrcode.create({
            "type": "upi_qr",
            "name": "ABC Bank",
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": int(amount * 100),
            "description": f"Loan Recovery – {case.case_number}",
            "close_by": close_at,
        })
        return {"image_url": qr["image_url"], "qr_id": qr["id"]}

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/ptp
    # -----------------------------------------------------------------
    def set_ptp(self, agent, case_id: str, req) -> dict:
        case = self.db.query(Case).filter(Case.id == case_id, Case.agent_id == agent.id).first()
        if not case:
            raise AppException(404, ErrorCode.CASE_NOT_FOUND, "Case not found or not assigned to you")

        existing = self._find_recent_duplicate_ptp(case.id, agent.id, req)
        if existing:
            return self._ptp_response(existing)

        ptp = PTP(
            case_id=case.id,
            agent_id=agent.id,
            committed_amount=req.committed_amount,
            committed_date=req.committed_date,
            customer_reason=req.customer_reason,
            agent_notes=req.agent_notes,
            follow_up_date=req.follow_up_date,
        )
        self.db.add(ptp)

        case.status = CaseStatus.PTP_SET
        agent.current_month_ptps_set += 1

        self.db.commit()
        self.db.refresh(ptp)

        return self._ptp_response(ptp)

    def _find_recent_duplicate_ptp(self, case_id: str, agent_id: str, req) -> PTP | None:
        window_start = datetime.now(timezone.utc) - timedelta(seconds=self._DUPLICATE_SUBMIT_WINDOW_SECONDS)
        return (
            self.db.query(PTP)
            .filter(
                PTP.case_id == case_id,
                PTP.agent_id == agent_id,
                PTP.committed_amount == req.committed_amount,
                PTP.committed_date == req.committed_date,
                PTP.created_at >= window_start,
            )
            .order_by(PTP.created_at.desc())
            .first()
        )

    @staticmethod
    def _ptp_response(ptp: PTP) -> dict:
        return {
            "id": ptp.id,
            "case_id": ptp.case_id,
            "committed_amount": ptp.committed_amount,
            "committed_date": ptp.committed_date.isoformat(),
            "follow_up_date": ptp.follow_up_date.isoformat() if ptp.follow_up_date else None,
            "status": ptp.status,
        }
