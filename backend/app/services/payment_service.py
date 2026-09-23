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
#
# 2026-07-30 — collect_payment now accepts an optional verification_id (a
# verified borrower OTP, held ephemerally in Redis — no DB table). When present
# it is consumed via OtpService.consume_for_payment and the Payment is written
# straight as VERIFIED (verified_at set, PAYMENT_VERIFIED audit row) — filling
# the long-standing gap where nothing ever promoted a Payment out of the default
# PENDING_VERIFICATION. When absent, behaviour is unchanged (offline/deferred
# path: row stays PENDING_VERIFICATION for later borrower verification). See
# otp_service.py, prototype_to_product/30.07.md, and /changelog.md.
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta


from app.core.config import settings
from app.core.errors import AppException, ErrorCode
# Agent is read in _get_accessible_case to check whether a colleague's case
# belongs to the same manager. It was used there and never imported, so that
# branch raised NameError instead of authorising the visit — latent because no
# test exercises a same-manager colleague's case. Fixed 2026-09-06.
from app.models.agent import Agent
from app.models.audit_log import AuditLog, AuditAction
from app.core.audit import write_audit
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.services.ptp_lifecycle_service import verified_paid_against
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

    def _get_accessible_case(self, agent, case_id: str) -> Case:
        case = self.db.query(Case).filter(Case.id == case_id).first()
        if not case:
            raise AppException(404, ErrorCode.CASE_NOT_FOUND, "Case not found")

        authorized = (case.agent_id == agent.id)
        if not authorized:
            from app.models.beat import Beat
            beats = self.db.query(Beat).filter(Beat.agent_id == agent.id).all()
            if any(case_id in (b.ordered_case_ids or []) for b in beats):
                authorized = True
            elif agent.manager_user_id and case.agent_id:
                curr_ag = self.db.query(Agent).filter(Agent.id == case.agent_id).first()
                if curr_ag and curr_ag.manager_user_id == agent.manager_user_id:
                    authorized = True
            elif case.agent_id is None:
                authorized = True

        if not authorized:
            raise AppException(403, ErrorCode.FORBIDDEN, "Case not assigned to you")

        if case.agent_id != agent.id:
            case.agent_id = agent.id

        return case

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/payment
    # -----------------------------------------------------------------
    def collect_payment(self, agent, case_id: str, req) -> dict:
        case = self._get_accessible_case(agent, case_id)

        existing = self._find_recent_duplicate(case.id, agent.id, req)
        if existing:
            return self._payment_response(existing, case)

        # Borrower OTP gate: if a verification_id is supplied, confirm it is a
        # verified (Redis-held, ephemeral) OTP authorising THIS exact amount/mode
        # and not already spent — the Payment is then trusted (VERIFIED). No
        # verification_id = the offline/deferred path, where the row stays
        # PENDING_VERIFICATION until the borrower confirms later. Validate BEFORE
        # writing anything.
        verified = False
        verification_id = getattr(req, "verification_id", None)
        if verification_id:
            from app.services.otp_service import OtpService
            verified = OtpService(self.db).consume_for_payment(
                verification_id, case.id, req.amount,
            )

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
            status=PaymentStatus.VERIFIED if verified else PaymentStatus.PENDING_VERIFICATION,
            verified_at=now_utc if verified else None,
        )
        self.db.add(payment)
        self.db.flush()   # assign payment.id for the audit row below

        if verified:
            self.db.add(AuditLog(
                id=str(uuid.uuid4()),
                created_at=now_utc,
                user_id=agent.user_id,
                action=AuditAction.PAYMENT_VERIFIED,
                entity_type="Payment",
                entity_id=payment.id,
                details={"amount": req.amount, "mode": str(req.mode), "channel": "OTP", "deferred": False},
                success=True,
            ))
            self._honor_active_ptps_paid_by_due_date(case.id, agent.id, now_utc)

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

        # 2026-09-11 — PAYMENT_SUBMITTED, declared and never written. The
        # VERIFIED transition is already audited (PAYMENT_VERIFIED, on the OTP
        # path); this is the submission itself, whatever status it lands in.
        write_audit(
            self.db, action=AuditAction.PAYMENT_SUBMITTED, user_id=agent.user_id,
            entity_type="Payment", entity_id=payment.id,
            details={"case_id": case.id, "case_number": case.case_number,
                     "agent_id": agent.id, "amount": payment.amount,
                     "mode": str(getattr(payment.mode, "value", payment.mode)),
                     "status": str(getattr(payment.status, "value", payment.status)),
                     "receipt_number": payment.receipt_number},
        )

        receipt_sent = self._notify_payment_received(agent, case, payment, req)
        return self._payment_response(payment, case, receipt_sent=receipt_sent)

    def _honor_active_ptps_paid_by_due_date(self, case_id: str, agent_id: str, paid_at: datetime) -> None:
        """Keep PTP status in sync when an OTP-verified payment satisfies it."""
        paid_date = paid_at.date()
        active_ptps = (
            self.db.query(PTP)
            .filter(
                PTP.case_id == case_id,
                PTP.agent_id == agent_id,
                PTP.status == PTPStatus.ACTIVE,
                PTP.committed_date >= paid_date,
            )
            .all()
        )
        for ptp in active_ptps:
            # 2026-09-17 — the sum moved to ptp_lifecycle_service so the
            # nightly expiry job and this path read ONE definition of "money
            # against this promise". Same query, same cut-off (the committed
            # date); the lifecycle job passes the grace day instead.
            total_paid = verified_paid_against(self.db, ptp, through=ptp.committed_date)
            if float(total_paid) >= float(ptp.committed_amount):
                previous = str(ptp.status)
                ptp.status = PTPStatus.HONORED
                ptp.actual_paid_amount = float(total_paid)
                # PTP_UPDATED has been declared since the AuditLog model was
                # written and never once emitted — one of the eighteen action
                # types in that enum with no call site. This is the case it was
                # meant for: a commitment's status changing on its own, from a
                # side effect of another action, with no human deciding it.
                # A manager asking "who marked this honoured?" would otherwise
                # find nothing, and the honest answer is "the payment did".
                self.db.add(AuditLog(
                    id=str(uuid.uuid4()),
                    created_at=paid_at,
                    user_id=None,   # nobody did this; a verified payment did
                    action=AuditAction.PTP_UPDATED,
                    entity_type="PTP",
                    entity_id=ptp.id,
                    details={
                        "from": previous,
                        "to": str(PTPStatus.HONORED),
                        "reason": "VERIFIED_PAYMENT_BY_COMMITTED_DATE",
                        "committed_amount": float(ptp.committed_amount),
                        "committed_date": ptp.committed_date.isoformat(),
                        "verified_paid_by_due_date": float(total_paid),
                        "actor": "SYSTEM",
                    },
                    success=True,
                ))

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

    def _notify_payment_received(self, agent, case, payment, req) -> bool:
        """Returns whether a receipt was handed to the transport. False when
        the borrower has no phone, Twilio is unconfigured, or the send failed —
        the failure is logged at ERROR inside send_twilio; this only reports
        it, so the payment response can say so instead of implying delivery."""
        customer = self.db.query(Customer).filter(Customer.id == case.customer_id).first()
        loan = self.db.query(Loan).filter(Loan.id == case.loan_id).first()
        if not customer or not customer.phone_primary:
            return False
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
            f"Agent *{agent.user.full_name}* visited on {pay_date}.\n"
            f"Rs.{req.amount:,.0f} received via *{mode_label}*\n"
            f"Loan Account: {masked_acct}\n"
            f"Receipt No: {payment.receipt_number}\n"
            f"Mobile: {masked_phone}\n\n"
            f"Thank you for your payment.\n– ABC Bank"
        )
        return NotificationService.send_twilio(e164, sms_body, wa_body)

    @staticmethod
    def _payment_response(payment: Payment, case: Case, *, receipt_sent: bool = False) -> dict:
        return {
            "id": payment.id,
            "receipt_number": payment.receipt_number,
            "amount": payment.amount,
            "mode": payment.mode,
            "status": payment.status,
            "payment_date": payment.payment_date.isoformat(),
            "case_status": case.status,
            "total_collected": case.collected_amount,
            # Whether the receipt reached the transport. Response-level only —
            # no column, no migration. Until 2026-09-11 a failed receipt was
            # logged and nobody in the field was told; now the agent's screen can be.
            "receipt_sent": bool(receipt_sent),
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
        case = self._get_accessible_case(agent, case_id)

        existing = self._find_recent_duplicate_ptp(case.id, agent.id, req)
        if existing:
            return self._ptp_response(existing)

        remaining_target = max(0.0, case.target_amount - case.collected_amount)
        amt = req.committed_amount
        if remaining_target > 0 and amt > remaining_target:
            amt = remaining_target

        ptp = PTP(
            case_id=case.id,
            agent_id=agent.id,
            committed_amount=amt,
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

        # 2026-09-11 — PTP_SET, declared and never written. PTP_UPDATED (the
        # payment honouring a promise) has been audited for some time; the
        # promise being TAKEN had not. Same post-commit, own-commit contract.
        write_audit(
            self.db, action=AuditAction.PTP_SET, user_id=agent.user_id,
            entity_type="PTP", entity_id=ptp.id,
            details={"case_id": case.id, "case_number": case.case_number,
                     "agent_id": agent.id, "committed_amount": ptp.committed_amount,
                     "committed_date": ptp.committed_date.isoformat() if ptp.committed_date else None},
        )

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
