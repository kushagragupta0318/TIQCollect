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
#
# 2026-09-24 (hotfix PAY-1) — collect_payment refuses mode=UPI without a
# non-blank upi_reference (AppException 422, UPI_REFERENCE_REQUIRED), before
# anything is read or written. The Record Visit page used to waive the field
# 10 s after showing a static QR — a demo timer in every build — so a UPI
# payment could be recorded with no evidence it happened. There is no
# gateway-verified exception because v1 records no gateway payment:
# create_payment_link mints a Razorpay QR that no page calls and no webhook
# confirms.
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.events import publish_event
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
import re

from app.core.security import explicit_true
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.services.scope import agent_case_or_404, sync_assignee
from app.services.capture_time import judge_capture, moves_case, note_delivered, same_ist_month
from app.services.ptp_lifecycle_service import verified_paid_against
from app.services.brand import brand_for
from app.services.notification_service import NotificationService

# ── The evidence each payment mode must carry (hotfix PAY-1, 2026-09-24) ──────
# The only evidence a non-cash payment happened is its reference. The page
# asked for these; the server never did — 201 of the demo book's 1,036
# payments are NEFT rows with no reference at all. One definition, read by
# collect_payment and by the deferred-OTP promotion (otp_service).
# The prefix the demo auto-confirm writes (frontend upiPayment.ts); compared
# upper-case, so a hand-typed variant is caught too.
DEMO_UPI_REFERENCE_PREFIX = "DEMO-UPI-"
# A UPI transaction's UTR / RRN is 12 digits.
UPI_UTR = re.compile(r"^\d{12}$")
_BANK_REFERENCE_MODES = frozenset({PaymentMode.NEFT, PaymentMode.RTGS, PaymentMode.DD})


def normalised_upi_reference(ref: str | None) -> str | None:
    """The UTR as stored: what the rule checks (spaces removed), so
    "4123 4567 8901" and "412345678901" are one reference, not two. A demo
    reference is kept as typed, trimmed."""
    r = (ref or "").strip()
    if not r:
        return None
    if r.upper().startswith(DEMO_UPI_REFERENCE_PREFIX):
        return r
    return r.replace(" ", "")


def payment_reference_problem(mode, *, upi_reference: str | None, bank_reference: str | None,
                              cheque_number: str | None) -> tuple[ErrorCode, str] | None:
    """None when the payment carries the evidence its mode needs; otherwise
    the error code and the message for the agent."""
    mode = PaymentMode(mode) if not isinstance(mode, PaymentMode) else mode
    if mode == PaymentMode.UPI:
        ref = (upi_reference or "").strip()
        if not ref:
            return (ErrorCode.UPI_REFERENCE_REQUIRED,
                    "A UPI payment needs its transaction reference (UTR) from the payment confirmation.")
        if ref.upper().startswith(DEMO_UPI_REFERENCE_PREFIX):
            # Accepted only where the demo flag production never sets is on —
            # NOT DEMO_MODE, which the live site runs with.
            if explicit_true(settings.DEMO_UPI_ACCEPT):
                return None
            return ErrorCode.UPI_REFERENCE_REQUIRED, "A demo UPI reference is not accepted on this server."
        if not UPI_UTR.match(ref.replace(" ", "")):
            return ErrorCode.UPI_REFERENCE_REQUIRED, "A UPI transaction reference (UTR) is 12 digits."
        return None
    if mode in _BANK_REFERENCE_MODES and not (bank_reference or "").strip():
        return (ErrorCode.PAYMENT_REFERENCE_REQUIRED,
                f"A {mode.value} payment needs its bank reference (UTR) from the transfer confirmation.")
    if mode == PaymentMode.CHEQUE and not (cheque_number or "").strip():
        return ErrorCode.PAYMENT_REFERENCE_REQUIRED, "A cheque payment needs the cheque number."
    return None


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
        """2026-09-24 (A03): the one rule (services/scope). The old copy took
        over any unassigned case in any tenant on the first payment."""
        return agent_case_or_404(self.db, agent, case_id)

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/payment
    # -----------------------------------------------------------------
    def collect_payment(self, agent, case_id: str, req) -> dict:
        # 2026-09-24 (hotfix PAY-1) — a UPI collection must carry its UTR. The
        # page used to waive the field 10 s after showing a static QR (a demo
        # timer, not a payment signal), so a "UPI payment" could be recorded
        # with no evidence it happened. No gateway-verified exception exists
        # because v1 records no gateway payment at all: create_payment_link
        # mints a Razorpay QR that no page calls and no webhook confirms. A
        # future webhook must put the gateway's payment id in upi_reference.
        # Every non-cash mode now carries its evidence (see
        # payment_reference_problem): UPI a 12-digit UTR (or, on a box with
        # DEMO_UPI_ACCEPT, the demo's DEMO-UPI- reference), NEFT/RTGS/DD a bank
        # reference, CHEQUE its number.
        problem = payment_reference_problem(req.mode, upi_reference=req.upi_reference,
                                            bank_reference=req.bank_reference, cheque_number=req.cheque_number)
        if problem:
            raise AppException(422, problem[0], problem[1])
        case = self._get_accessible_case(agent, case_id)

        # Idempotency (v2_0025): a retry after a lost response — the photo uploads
        # routinely push it past the 15-second window below — must return the
        # payment it already made, not a second one. This returns BEFORE the OTP
        # gate and every side effect (a new receipt number, the audit row, the
        # borrower notification), so a retry neither double-counts nor re-spends
        # the single-use OTP token. Older clients send no id and still get the
        # 15-second window.
        csid = getattr(req, "client_submission_id", None)
        if csid:
            stored = self._by_submission(agent, csid)
            if stored is not None:
                return self._payment_repeat(stored, case, req)

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
            client_submission_id=csid,
            agent_id=agent.id,
            amount=req.amount,
            mode=req.mode,
            receipt_number=self._generate_receipt(),
            upi_reference=normalised_upi_reference(req.upi_reference),
            cheque_number=req.cheque_number,
            bank_reference=req.bank_reference,
            receipt_photo_key=req.receipt_photo_key,
            payment_date=now_utc,
            status=PaymentStatus.VERIFIED if verified else PaymentStatus.PENDING_VERIFICATION,
            verified_at=now_utc if verified else None,
        )
        self.db.add(payment)
        try:
            self.db.flush()   # assign payment.id for the audit row below
        except IntegrityError:
            # Two retries of one submission raced and the other's INSERT landed
            # first (the partial unique index on (agent_id, client_submission_id)).
            # Return the row it made; re-raise if it is not there, because then it
            # was a different constraint, not this idempotency key.
            self.db.rollback()
            stored = self._by_submission(agent, csid) if csid else None
            if stored is None:
                raise
            return self._payment_repeat(stored, case, req)

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
        sync_assignee(case, agent)   # at the business commit, never at the read

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
        # 2026-09-24 — live events. An OTP-verified payment is written VERIFIED
        # in the same commit, so it announces both steps; a deferred one
        # announces the verification later, from OtpService.
        status = str(getattr(payment.status, "value", payment.status))
        event_data = {"payment_id": payment.id, "case_id": case.id,
                      "case_number": case.case_number, "amount": payment.amount,
                      "mode": str(getattr(payment.mode, "value", payment.mode)),
                      "status": status, "case_status": str(getattr(case.status, "value", case.status))}
        publish_event("payment.submitted", agent=agent, data=event_data)
        if verified:
            publish_event("payment.verified", agent=agent, data={**event_data, "deferred": False})

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
        bn = brand_for(self.db, case=case).bank_name
        mode_label = {"CASH": "Cash", "UPI": "UPI", "CHEQUE": "Cheque", "NEFT": "NEFT", "RTGS": "RTGS"}.get(str(req.mode), str(req.mode))
        sms_body = (
            f"Dear {customer.full_name}, {bn}'s agent {agent.user.full_name} visited on {pay_date}. "
            f"Rs.{req.amount:,.0f} received via {mode_label} for loan {masked_acct}. "
            f"Receipt: {payment.receipt_number}. Mobile: {masked_phone}. - {bn}"
        )
        wa_body = (
            f"*Visit Completed & Payment Received – {bn}*\n\n"
            f"Dear {customer.full_name},\n\n"
            f"Agent *{agent.user.full_name}* visited on {pay_date}.\n"
            f"Rs.{req.amount:,.0f} received via *{mode_label}*\n"
            f"Loan Account: {masked_acct}\n"
            f"Receipt No: {payment.receipt_number}\n"
            f"Mobile: {masked_phone}\n\n"
            f"Thank you for your payment.\n– {bn}"
        )
        return NotificationService.send_twilio(e164, sms_body, wa_body, db=self.db, case_id=case.id)

    def _by_submission(self, agent, client_submission_id: str) -> Payment | None:
        return (self.db.query(Payment)
                .filter(Payment.agent_id == agent.id,
                        Payment.client_submission_id == client_submission_id)
                .first())

    def _payment_repeat(self, stored: Payment, case: Case, req) -> dict:
        """The response for a submission already recorded. A 409 — not a silent
        return of the first payment — when the id was reused for a DIFFERENT case,
        amount or mode: that is a client bug, and returning the first payment would
        hide a money error."""
        if str(stored.case_id) != str(case.id):
            raise AppException(409, ErrorCode.IDEMPOTENCY_KEY_REUSED,
                               "This submission id was already used for another case.")
        if float(stored.amount) != float(req.amount) or \
                str(getattr(stored.mode, "value", stored.mode)) != str(getattr(req.mode, "value", req.mode)):
            raise AppException(409, ErrorCode.IDEMPOTENCY_KEY_REUSED,
                               "This submission id was already used for a different amount or mode.")
        # receipt_sent defaults False: this call sent nothing (the receipt went
        # with the original payment). Matches the 15-second duplicate path above,
        # which returns the same default — the field is an event about THIS attempt
        # (schemas/agent.py), not the payment's state, so the two dedupe paths
        # report alike.
        return self._payment_response(stored, case)

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
    def create_payment_link(self, agent, case_id: str, amount: float) -> dict:
        # 2026-09-24 (hotfix PL-1) — this had NO access check: any agent could
        # mint a UPI payment QR for any case in the database. Now the case must
        # pass the access rule, checked before anything else, and "not yours"
        # is the same 404 as "no such case".
        # 2026-09-28 (merge into standalone-p1): the rule is services/scope's —
        # the ONE definition (agency AND assigned-or-on-today's-beat) — not the
        # strict `agent_id ==` copy hotfix-1 carried on v1, and the 404 body is
        # scope's uniform "Not found".
        case = agent_case_or_404(self.db, agent, case_id)

        import razorpay
        if not settings.RAZORPAY_TEST_API or not settings.RAZORPAY_TEST_KEY_SECRET:
            raise AppException(503, ErrorCode.VALIDATION_ERROR, "Razorpay not configured")
        # The name on the gateway QR is the payee's, from settings — the same
        # UPI_PAYEE_NAME the static QR uses (PAY-2). It was "ABC Bank",
        # hardcoded, which review of 4dcd9dc found surviving here.
        payee = (settings.UPI_PAYEE_NAME or "").strip()
        if not payee or payee.startswith("${"):
            raise AppException(503, ErrorCode.VALIDATION_ERROR, "UPI payee not configured")

        close_at = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())

        client = razorpay.Client(auth=(settings.RAZORPAY_TEST_API, settings.RAZORPAY_TEST_KEY_SECRET))
        qr = client.qrcode.create({
            "type": "upi_qr",
            "name": payee,
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
    def set_ptp(self, agent, case_id: str, req, *, token_device_id: str | None = None) -> dict:
        # I02: a replayed offline promise returns the row it already made, then
        # is judged at its capture time (ADR 0011), like its visit.
        csid = getattr(req, "client_submission_id", None)
        if csid:
            stored = self._ptp_by_submission(agent, csid)
            if stored is not None:
                return self._ptp_repeat(stored, case_id)
        capture = judge_capture(self.db, agent, captured_at=getattr(req, "captured_at", None),
                                device_seq=getattr(req, "device_seq", None),
                                item_device_id=getattr(req, "device_id", None),
                                token_device_id=token_device_id, now=datetime.now(timezone.utc))
        case = agent_case_or_404(self.db, agent, case_id, on_day=capture.day if capture.late else None)
        # A promise cannot fall due before it was made. For a replay "made" is
        # the capture day, never the sync day (Opus audit of 1a85ffd, MED).
        if req.committed_date < capture.day:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               "The promised date is before the day the promise was made.")

        existing = None if csid else self._find_recent_duplicate_ptp(case.id, agent.id, req)
        if existing:
            return self._ptp_response(existing)

        # 2026-09-24 (coordinator audit): the cap was skipped whenever the case
        # target was already met (remaining 0) — `if remaining > 0` — so a
        # promise of any size, ₹9.99 crore included, was stored against a case
        # with nothing left to collect. A promise is now capped by what the
        # CASE still needs, else by what the LOAN still owes; with neither
        # there is nothing to promise, and saying so beats storing fiction.
        remaining_target = max(0.0, (case.target_amount or 0.0) - (case.collected_amount or 0.0))
        loan = self.db.get(Loan, case.loan_id)
        loan_owed = max(0.0, float(loan.total_outstanding or 0.0)) if loan is not None else 0.0
        ceiling = remaining_target if remaining_target > 0 else loan_owed
        if ceiling <= 0:
            raise AppException(400, ErrorCode.VALIDATION_ERROR, "Nothing is outstanding on this case to promise against")
        amt = min(float(req.committed_amount), ceiling)

        ptp = PTP(
            case_id=case.id,
            agent_id=agent.id,
            committed_amount=amt,
            committed_date=req.committed_date,
            customer_reason=req.customer_reason,
            agent_notes=req.agent_notes,
            follow_up_date=req.follow_up_date,
            client_submission_id=csid,
        )
        self.db.add(ptp)

        if moves_case(self.db, case, capture):
            case.status = CaseStatus.PTP_SET
        if same_ist_month(capture.at, datetime.now(timezone.utc)):
            agent.current_month_ptps_set += 1
        if not capture.late:
            sync_assignee(case, agent)   # at the business commit, never at the read; never on a replay
        note_delivered(capture)

        try:
            self.db.commit()
        except IntegrityError:
            # Two deliveries of one key raced and the other landed first.
            self.db.rollback()
            stored = self._ptp_by_submission(agent, csid) if csid else None
            if stored is None:
                raise
            return self._ptp_repeat(stored, case_id)
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
        publish_event("ptp.set", agent=agent, data={
            "ptp_id": ptp.id, "case_id": case.id, "case_number": case.case_number,
            "committed_amount": ptp.committed_amount,
            "committed_date": ptp.committed_date.isoformat() if ptp.committed_date else None,
        })

        return self._ptp_response(ptp)

    def _ptp_by_submission(self, agent, client_submission_id: str) -> PTP | None:
        return (self.db.query(PTP)
                .filter(PTP.agent_id == agent.id, PTP.client_submission_id == client_submission_id)
                .first())

    def _ptp_repeat(self, stored: PTP, case_id: str) -> dict:
        if str(stored.case_id) != str(case_id):
            raise AppException(409, ErrorCode.IDEMPOTENCY_KEY_REUSED,
                               "This submission id was already used for another case.")
        return self._ptp_response(stored)

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
