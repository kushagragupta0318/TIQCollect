# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — POST /beat/reoptimize (line 524) now response_model=
#   ReoptimizeBeatResponse; body replaced with a delegate call to
#   CaseService(db).reoptimize_beat() (line 536) — inline logic moved to
#   services/case_service.py. POST .../call-log (line 1291) now
#   response_model=LogCallResponse and persists available_from/until
#   (line 1312).
# 2026-07-14 — POST .../visit (line 886) now response_model=VisitResponse;
#   body replaced with a delegate call to VisitService(db).record_visit()
#   (line 893) — inline logic moved to services/visit_service.py.
#   POST .../visits/{visit_id}/transcribe (line 1445) changed from a
#   synchronous OpenAI-Whisper-only call to response_model=
#   TranscribeQueuedResponse (202) — enqueues transcribe_visit_recording_task
#   (line 1472) instead of blocking the request; transcription itself moved
#   to core/transcription.py (provider seam: hosted OpenAI Whisper vs.
#   self-hosted faster-whisper, picked via settings.TRANSCRIPTION_PROVIDER).
#   Added POST /transcribe-audio (line 1483) — synchronous, ad-hoc (no
#   Case/Visit needed), backing the frontend's voice-note dictation widgets
#   now that they route through Whisper instead of browser SpeechRecognition.
# 2026-07-14 (API fix pass) — Live-audited the record_visit contract end to
#   end (RecordVisitRequest -> VisitService -> Visit model -> case-detail
#   response) and found three fields that were accepted but silently
#   discarded: `notes`, `consent_given`, `signature_key` (fixes live in
#   visit_service.py/models/visit.py — see their headers). While here:
#   `_generate_visit_report`'s context now includes visit.notes; the
#   /photo-upload-url `subject` set gained "signature" (PNG, not JPEG) so
#   the frontend's already-built SignaturePad can actually upload what it
#   captures instead of discarding it; get_case_detail's per-visit dict
#   had `ai_visit_note`/`agent_recording_transcript` listed twice (harmless
#   but dead) — deduped and added `notes`/`consent_given` in their place.
# 2026-07-14 (module completion pass) — Three more extractions out of this
#   file, same router->service recipe: `_generate_receipt` stays, but
#   `_generate_visit_report` -> services/ai_report_service.py,
#   `_normalize_phone`/`_send_twilio` -> services/notification_service.py
#   (also updated collect_payment/notify_visit/notify_case, which use these
#   too — shared infra, not visit-only), and photo-upload-url/photos/
#   recording-upload-url/recording-urls/transcribe/transcribe-audio ->
#   services/media_service.py. `storage`/`is_within_contact_hours`/
#   `within_geo_fence` imports removed here — no longer used directly in
#   this file, only inside the services now. Full detail + why for all:
#   /changelog.md
# 2026-07-15 — trigger_sos() now looks up the agent's manager
#   (Agent.manager_user_id) and sends a real SMS/WhatsApp via
#   NotificationService — previously set agent.sos_active=True and returned
#   "Your manager has been alerted" with no notification actually sent.
#   Added `from app.models.user import User` for the manager lookup.
# 2026-07-21 — collect_payment/create_payment_link/set_ptp bodies moved to
#   the new services/payment_service.py (PaymentService), same
#   router->service recipe as visit_service.py — endpoints here are now
#   thin delegates with response_model=PaymentResponse/PaymentLinkResponse/
#   PTPResponse. `_generate_receipt()` moved with collect_payment (only
#   caller) to PaymentService._generate_receipt(). Nothing about these
#   three endpoints' request/response JSON shape changed — pure
#   extraction, plus a new idempotency guard inside PaymentService (see
#   its own header). Full detail: /changelog.md
# 2026-07-22 — list_cases/get_ranked_cases/get_case_detail/handover_case/
#   flag_customer bodies moved to services/case_service.py (CaseService)
#   — completes that file's §7.1 scope except visit-strategy (deliberately
#   left inline, tracked separately). handover_case/flag_customer now
#   declare response_model=HandoverResponse/FlagCustomerResponse;
#   list_cases/get_ranked_cases/get_case_detail stay dict-shaped (large
#   nested case/customer/loan structure — typing them is a separate,
#   larger pass, not bundled in here). `_PRIORITY_ORDER` module constant
#   moved to case_service.py (its only remaining user) — NOT deleted, just
#   relocated; see case_service.py's header. `CasePriority` import on this
#   file's import line is now unused here as a result (kept, not removed,
#   per instruction not to delete anything without being asked). Full
#   detail: /changelog.md
# 2026-07-22 (continued) — home_summary/checkin/get_beat/get_profile/
#   trigger_sos/cancel_sos/get_availability_calendar bodies moved to the
#   new services/agent_service.py (AgentService) — completes
#   final_changes.md §7.1 item #2. checkin/trigger_sos/cancel_sos now
#   declare response_model=CheckInResponse/SOSResponse/SOSCancelResponse;
#   home_summary/get_beat/get_profile/get_availability_calendar stay
#   dict-shaped, same scope decision as case_service's list/ranked/detail
#   (large nested payloads — typing them is separate future work). Every
#   moved function's logic is unchanged, byte-for-byte, in
#   agent_service.py — nothing here was deleted, only relocated and
#   logged, per instruction. Full detail: /changelog.md
# 2026-07-22 (typed-response follow-up, same day) — SUPERSEDES the "stays
#   dict-shaped" notes above for list_cases/get_ranked_cases/
#   get_case_detail/home_summary/get_beat/get_profile/
#   get_availability_calendar: all 7 now declare a real response_model
#   (schemas added to schemas/agent.py, cross-checked field-by-field
#   against the actual SQLAlchemy models, not guessed). `get_beat` uses
#   `Optional[BeatResponse]` since it can legitimately return `None` (no
#   active beat). New `from typing import Optional` import added to this
#   file for that. Nothing about any of these endpoints' JSON output
#   changed — same keys, same values, same nesting, just documented and
#   validated now instead of implicit. Full detail: /changelog.md
# 2026-07-30 — Added two borrower-OTP endpoints next to the payment routes:
#   POST .../payment/otp/send (OtpSendResponse) and .../payment/otp/verify
#   (OtpVerifyResponse), both thin delegates to the new OtpService. These gate
#   payment verification — the borrower confirms the amount from their
#   registered phone before a Payment is trusted. collect_payment's contract
#   also gained an optional verification_id (handled entirely in
#   PaymentService). See otp_service.py, prototype_to_product/30.07.md,
#   /changelog.md.
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import uuid
from datetime import datetime, date, time, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Form, File, UploadFile
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import structlog

from app.core.dependencies import DbSession, AgentOnly
from app.core import llm
from app.core.config import settings
from app.models.agent import Agent, AgentStatus
from app.models.beat import Beat
from app.models.case import Case, CaseStatus, CasePriority
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.payment import Payment
from app.models.ptp import PTP, PTPStatus
from app.models.visit import Visit, VisitOutcome
from app.models.case import EscalationReason
from app.models.user import User
from app.services.notification_service import NotificationService
from app.services.media_service import MediaService
from app.services.payment_service import PaymentService
from app.services.otp_service import OtpService
from app.schemas.agent import (
    AvailabilityCalendarResponse,
    BeatResponse,
    CaseDetailResponse,
    CaseListItemResponse,
    CheckInRequest,
    CheckInResponse,
    CollectPaymentRequest,
    CustomerFlagRequest,
    FlagCustomerResponse,
    HandoverRequest,
    HandoverResponse,
    HomeSummaryResponse,
    LogCallRequest,
    LogCallResponse,
    OtpSendRequest,
    OtpSendResponse,
    OtpVerifyRequest,
    OtpVerifyResponse,
    PaymentLinkResponse,
    PaymentResponse,
    ProfileResponse,
    PTPResponse,
    RankedCaseResponse,
    RecordVisitRequest,
    ReoptimizeBeatResponse,
    SetPTPRequest,
    LocationBatchRequest,
    LocationBatchResponse,
    SOSCancelResponse,
    SOSRequest,
    SOSResponse,
    TranscribeAudioResponse,
    TranscribeQueuedResponse,
    VisitResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/agent", tags=["agent"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _effective_day(agent_id: str, db) -> date:
    """Return the most recent active beat date on or before today for this agent.

    Falls back to today only when no beat exists at all (brand-new install).
    This lets the demo run without daily re-seeding: if the last seed was
    yesterday, the app still shows yesterday's data as "today".
    """
    row = (
        db.query(Beat.beat_date)
        .filter(Beat.agent_id == agent_id, Beat.beat_date <= date.today())
        .order_by(Beat.beat_date.desc())
        .first()
    )
    return row[0] if row else date.today()


def _get_agent_or_404(current_user, db) -> Agent:
    agent = db.query(Agent).options(joinedload(Agent.user)).filter(Agent.user_id == current_user.id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    return agent


def _format_case(case: Case) -> dict:
    c = case.customer
    l = case.loan
    return {
        "id": case.id,
        "case_number": case.case_number,
        "status": case.status,
        "priority": case.priority,
        "target_amount": case.target_amount,
        "collected_amount": case.collected_amount,
        "allocation_date": case.allocation_date,
        "visit_count": case.visit_count,
        "is_escalated": case.is_escalated,
        "agent_id": case.agent_id,
        "max_visits_allowed": case.max_visits_allowed,
        "handover_notes": case.handover_notes,
        "customer": {
            "id": c.id,
            "customer_ref": c.customer_ref,
            "full_name": c.full_name,
            # Masked identifiers only — full PAN/Aadhaar never stored, bank account not in schema
            "pan_masked": c.pan_masked,
            "aadhaar_masked": c.aadhaar_masked,
            # Phone for calling via platform — alternate shown for contact, not for sharing
            "phone_primary": c.phone_primary,
            "phone_alternate": c.phone_alternate,
            "address_line1": c.address_line1,
            "address_line2": c.address_line2,
            "city": c.city,
            "state": c.state,
            "pincode": c.pincode,
            "latitude": c.latitude,
            "longitude": c.longitude,
            # Operational flags — agent needs these for safety and compliance
            "is_hostile": c.is_hostile,
            "do_not_contact": c.do_not_contact,
            "requires_female_agent": c.requires_female_agent,
            "customer_segment": c.customer_segment,
            "language_preference": c.language_preference,
            # Internal scores — sent but NOT rendered in agent UI (used only by manager/ML layer)
            # Kept in payload so manager-facing detail view (if ever proxied) can use them
            "_risk_category": c.risk_category,
            "_risk_score": c.risk_score,
            "_fraud_flag": c.fraud_flag,
        },
        "loan": {
            "id": l.id,
            # Loan account masked to last 4 digits for non-borrower display
            "loan_account_number": l.loan_account_number,
            "loan_account_masked": "XXXX" + l.loan_account_number[-4:] if l.loan_account_number else None,
            "loan_type": l.loan_type,
            "bank_name": l.bank_name,
            "dpd": l.dpd,
            "dpd_bucket": l.dpd_bucket,
            "status": l.status,
            "npa_flag": l.npa_flag,
            # Financial details — sent but frontend gates on borrower_verified flag
            "sanctioned_amount": l.sanctioned_amount,
            "outstanding_principal": l.outstanding_principal,
            "outstanding_interest": l.outstanding_interest,
            "penal_charges": l.penal_charges,
            "total_outstanding": l.total_outstanding,
            "overdue_amount": l.overdue_amount,
            "emi_amount": l.emi_amount,
            "tenure_months": l.tenure_months,
            "interest_rate": l.interest_rate,
            "last_payment_date": l.last_payment_date,
            "last_payment_amount": l.last_payment_amount,
            "next_due_date": l.next_due_date,
            # Legal/settlement — gated on borrower_verified in frontend
            "legal_status": l.legal_status,
            "settlement_status": l.settlement_status,
        },
        "collection_stage": case.collection_stage,
        "bank_ptp_date": case.bank_ptp_date,
        "bank_ptp_amount": case.bank_ptp_amount,
        "bank_ptp_status": case.bank_ptp_status,
        "bank_agent_remarks": case.bank_agent_remarks,
    }


def _visited_today(agent_id: str, eff_day: date, db) -> set[str]:
    """Return the set of case_ids that this agent has visited on eff_day or today."""
    from sqlalchemy import or_
    days = {eff_day, date.today()}
    conditions = []
    for d in days:
        start = datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = datetime.combine(d, time.max).replace(tzinfo=timezone.utc)
        conditions.append((Visit.check_in_time >= start) & (Visit.check_in_time <= end))

    return {
        row[0] for row in
        db.query(Visit.case_id)
        .filter(Visit.agent_id == agent_id, or_(*conditions))
        .distinct().all()
    }


# ---------------------------------------------------------------------------
# GET /agent/home-summary
# ---------------------------------------------------------------------------

@router.get("/home-summary", response_model=HomeSummaryResponse)
def home_summary(current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    agent = _get_agent_or_404(current_user, db)
    return AgentService(db).home_summary(agent)


# ---------------------------------------------------------------------------
# POST /agent/checkin
# ---------------------------------------------------------------------------

@router.post("/checkin", response_model=CheckInResponse)
def checkin(req: CheckInRequest, current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    agent = _get_agent_or_404(current_user, db)
    return AgentService(db).checkin(agent, req)


# ---------------------------------------------------------------------------
# POST /agent/checkout
# ---------------------------------------------------------------------------

@router.post("/checkout", response_model=CheckInResponse)
def checkout(current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    agent = _get_agent_or_404(current_user, db)
    return AgentService(db).checkout(agent)


# ---------------------------------------------------------------------------
# GET /agent/beat
# ---------------------------------------------------------------------------

@router.get("/beat", response_model=Optional[BeatResponse])
def get_beat(current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    agent = _get_agent_or_404(current_user, db)
    return AgentService(db).get_beat(agent)


# ---------------------------------------------------------------------------
# POST /agent/beat/reoptimize
# ---------------------------------------------------------------------------

@router.post("/beat/reoptimize", response_model=ReoptimizeBeatResponse)
def reoptimize_beat(
    current_user: AgentOnly,
    db: DbSession,
    lat: float,
    lon: float,
):
    """Re-optimize the active beat from the agent's current GPS position.

    See CaseService.reoptimize_beat for the actual logic (blocked-case
    filtering, time-window/urgent-override handling, OSRM+OR-Tools solve).
    """
    from app.services.case_service import CaseService

    agent = _get_agent_or_404(current_user, db)
    return CaseService(db).reoptimize_beat(agent, lat, lon)


# ---------------------------------------------------------------------------
# GET /agent/cases
# ---------------------------------------------------------------------------

LIVE_STATUSES = {
    CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS, CaseStatus.PTP_SET,
    CaseStatus.PARTIALLY_PAID, CaseStatus.ESCALATED,
}


_DONE_STATUSES = {CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF}


@router.get("/cases", response_model=list[CaseListItemResponse])
def list_cases(current_user: AgentOnly, db: DbSession):
    """Return today's beat cases — pending first (by priority), done last."""
    from app.services.case_service import CaseService
    agent = _get_agent_or_404(current_user, db)
    return CaseService(db).list_cases(agent)


# ---------------------------------------------------------------------------
# GET /agent/cases/ranked  — must be registered BEFORE /{case_id} route
# ---------------------------------------------------------------------------

@router.get("/cases/ranked", response_model=list[RankedCaseResponse])
def get_ranked_cases(current_user: AgentOnly, db: DbSession):
    """Return today's beat cases ranked by AI priority signals.

    Scoring: call log intel + last visit outcome + PTP + customer flags.
    One GPT-4o-mini call at the end generates a one-line reason per top-8 case.
    """
    from app.services.case_service import CaseService
    agent = _get_agent_or_404(current_user, db)
    return CaseService(db).ranked_cases(agent)


# ---------------------------------------------------------------------------
# GET /agent/cases/{case_id}
# ---------------------------------------------------------------------------

@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case_detail(case_id: str, current_user: AgentOnly, db: DbSession):
    from app.services.case_service import CaseService
    agent = _get_agent_or_404(current_user, db)
    return CaseService(db).case_detail(agent, case_id)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/visit
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/visit", response_model=VisitResponse)
def record_visit(case_id: str, req: RecordVisitRequest, current_user: AgentOnly, db: DbSession):
    """Record a field visit outcome.

    See VisitService.record_visit for the actual logic (geo/contact-hours
    checks, case status transition, AI audit note, customer notification).
    """
    from app.services.visit_service import VisitService

    agent = _get_agent_or_404(current_user, db)
    return VisitService(db).record_visit(agent, case_id, req)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/payment
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/payment", response_model=PaymentResponse)
def collect_payment(case_id: str, req: CollectPaymentRequest, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    return PaymentService(db).collect_payment(agent, case_id, req)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/payment/otp/send    (borrower OTP verification)
# POST /agent/cases/{case_id}/payment/otp/verify
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/payment/otp/send", response_model=OtpSendResponse)
def send_payment_otp(case_id: str, req: OtpSendRequest, current_user: AgentOnly, db: DbSession):
    """Send a 4-digit OTP to the borrower's REGISTERED phone to confirm a
    collection amount. `payment_id` present = re-verify an existing pending
    (offline) payment; absent = verify before collecting. See OtpService."""
    agent = _get_agent_or_404(current_user, db)
    return OtpService(db).generate_and_send(agent, case_id, req.amount, req.mode, req.payment_id)


@router.post("/cases/{case_id}/payment/otp/verify", response_model=OtpVerifyResponse)
def verify_payment_otp(case_id: str, req: OtpVerifyRequest, current_user: AgentOnly, db: DbSession):
    """Verify the borrower's OTP. For a deferred (payment-bound) OTP this
    promotes the pending Payment to VERIFIED and sends the e-receipt; for the
    pre-collection flow it marks the OTP used so collect_payment can consume it."""
    agent = _get_agent_or_404(current_user, db)
    return OtpService(db).verify(agent, case_id, req.otp_id, req.code)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/payment-link  (Razorpay UPI QR)
# ---------------------------------------------------------------------------

class PaymentLinkRequest(BaseModel):
    amount: float

@router.post("/cases/{case_id}/payment-link", response_model=PaymentLinkResponse)
def create_payment_link(case_id: str, req: PaymentLinkRequest, current_user: AgentOnly, db: DbSession):
    return PaymentService(db).create_payment_link(case_id, req.amount)


def _get_accessible_case_or_404(db: DbSession, agent: Agent, case_id: str) -> Case:
    case = (
        db.query(Case)
        .options(joinedload(Case.customer), joinedload(Case.loan))
        .filter(Case.id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if case.agent_id == agent.id:
        return case

    beats = db.query(Beat).filter(Beat.agent_id == agent.id).all()
    if any(case_id in (b.ordered_case_ids or []) for b in beats):
        return case

    if agent.manager_user_id and case.agent_id:
        curr_ag = db.query(Agent).filter(Agent.id == case.agent_id).first()
        if curr_ag and curr_ag.manager_user_id == agent.manager_user_id:
            return case
    elif case.agent_id is None:
        return case

    raise HTTPException(status_code=403, detail="Case not found or not assigned to you")


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/notify-visit  (pre-visit WhatsApp + SMS)
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/notify-visit")
def notify_visit(case_id: str, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    case = _get_accessible_case_or_404(db, agent, case_id)

    customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
    if not customer or not customer.phone_primary:
        raise HTTPException(status_code=400, detail="Customer has no phone number")

    loan = db.query(Loan).filter(Loan.id == case.loan_id).first()
    masked_acct = "XXXX" + loan.loan_account_number[-4:] if loan else "XXXXXXXX"
    dpd = loan.dpd if loan else 0

    e164 = "+" + NotificationService.normalize_phone(customer.phone_primary)
    visit_date = datetime.now(timezone.utc).strftime("%d %b %Y")
    sms_body = (
        f"Dear {customer.full_name}, ABC Bank's field agent {agent.user.full_name} "
        f"will visit you on {visit_date} regarding loan {masked_acct} (DPD: {dpd} days). "
        f"Target: Rs.{case.target_amount:,.0f}. Please be available. - ABC Bank"
    )
    wa_body = (
        f"*Visit Notice – ABC Bank*\n\n"
        f"Dear {customer.full_name},\n\n"
        f"\U0001f3e0 Our field agent *{agent.user.full_name}* will be visiting you shortly.\n"
        f"\U0001f4c5 Date: {visit_date}\n"
        f"\U0001f4b0 Target Amount: Rs.{case.target_amount:,.0f}\n"
        f"\U0001f4b3 Loan Account: {masked_acct}\n"
        f"⏳ DPD: {dpd} days overdue\n\n"
        f"Please be available and keep documents ready.\n– ABC Bank"
    )
    NotificationService.send_twilio(e164, sms_body, wa_body)
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/notify  (reminder / ptp / receipt via Twilio)
# ---------------------------------------------------------------------------

class NotifyCaseRequest(BaseModel):
    type: str  # "reminder" | "ptp" | "receipt"


@router.post("/cases/{case_id}/notify")
def notify_case(case_id: str, req: NotifyCaseRequest, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    case = _get_accessible_case_or_404(db, agent, case_id)

    customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
    if not customer or not customer.phone_primary:
        raise HTTPException(status_code=400, detail="Customer has no phone number")

    loan = db.query(Loan).filter(Loan.id == case.loan_id).first()
    masked_acct = "XXXX" + loan.loan_account_number[-4:] if loan else "XXXXXXXX"
    bank_name = loan.bank_name if loan else "ABC Bank"
    dpd = loan.dpd if loan else 0

    e164 = "+" + NotificationService.normalize_phone(customer.phone_primary)

    visit_date = datetime.now(timezone.utc).strftime("%d %b %Y")
    if req.type == "reminder":
        sms_body = (
            f"Dear {customer.full_name}, ABC Bank's field agent {agent.user.full_name} "
            f"will visit you on {visit_date} regarding loan {masked_acct} (DPD: {dpd} days). "
            f"Target: Rs.{case.target_amount:,.0f}. Please be available. - ABC Bank"
        )
        wa_body = (
            f"*Visit Notice – ABC Bank*\n\n"
            f"Dear {customer.full_name},\n\n"
            f"\U0001f3e0 Our field agent *{agent.user.full_name}* will be visiting you shortly.\n"
            f"\U0001f4c5 Date: {visit_date}\n"
            f"\U0001f4b0 Target Amount: Rs.{case.target_amount:,.0f}\n"
            f"\U0001f4b3 Loan Account: {masked_acct}\n"
            f"⏳ DPD: {dpd} days overdue\n\n"
            f"Please be available and keep documents ready.\n– ABC Bank"
        )
    elif req.type == "ptp":
        active_ptp = (
            db.query(PTP)
            .filter(PTP.case_id == case.id, PTP.status == PTPStatus.ACTIVE)
            .order_by(PTP.committed_date.desc())
            .first()
        )
        if not active_ptp:
            raise HTTPException(status_code=400, detail="No active PTP for this case")
        ptp_date = active_ptp.committed_date.strftime("%d %b %Y") if hasattr(active_ptp.committed_date, "strftime") else str(active_ptp.committed_date)
        sms_body = (
            f"Dear {customer.full_name}, this is a reminder for your commitment of "
            f"Rs.{active_ptp.committed_amount:,.0f} due on {ptp_date} against loan {masked_acct}. "
            f"Please ensure timely payment. - ABC Bank"
        )
        wa_body = (
            f"*PTP Reminder – ABC Bank*\n\n"
            f"Dear {customer.full_name},\n\n"
            f"\U0001f4b0 Committed Amount: Rs.{active_ptp.committed_amount:,.0f}\n"
            f"\U0001f4c5 Due Date: {ptp_date}\n"
            f"\U0001f4b3 Loan Account: {masked_acct}\n\n"
            f"Please ensure timely payment on the committed date.\n– ABC Bank"
        )
    elif req.type == "receipt":
        last_payment = (
            db.query(Payment)
            .filter(Payment.case_id == case.id)
            .order_by(Payment.payment_date.desc())
            .first()
        )
        if not last_payment:
            raise HTTPException(status_code=400, detail="No payment found for this case")
        pay_date = last_payment.payment_date.strftime("%d %b %Y") if hasattr(last_payment.payment_date, "strftime") else str(last_payment.payment_date)
        sms_body = (
            f"Dear {customer.full_name}, your payment of Rs.{last_payment.amount:,.0f} "
            f"against loan {masked_acct} (Receipt: {last_payment.receipt_number}) has been received on {pay_date}. "
            f"Thank you. - ABC Bank"
        )
        wa_body = (
            f"*Payment Receipt – ABC Bank*\n\n"
            f"Dear {customer.full_name},\n\n"
            f"✅ Amount Received: Rs.{last_payment.amount:,.0f}\n"
            f"\U0001f4b3 Loan Account: {masked_acct}\n"
            f"\U0001f9fe Receipt No: {last_payment.receipt_number}\n"
            f"\U0001f4c5 Date: {pay_date}\n\n"
            f"Thank you for your payment.\n– ABC Bank"
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid notification type")

    NotificationService.send_twilio(e164, sms_body, wa_body)
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/ptp
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/ptp", response_model=PTPResponse)
def set_ptp(case_id: str, req: SetPTPRequest, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    return PaymentService(db).set_ptp(agent, case_id, req)


# ---------------------------------------------------------------------------
# GET /agent/profile
# ---------------------------------------------------------------------------

@router.get("/profile", response_model=ProfileResponse)
def get_profile(current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    return AgentService(db).get_profile(current_user)


# ---------------------------------------------------------------------------
# POST /agent/location
# ---------------------------------------------------------------------------
# Batch ingest for the on-duty location trail. Accepts a queue rather than a
# single fix because the client buffers while out of signal — see
# services/location_service.py. Deliberately cheap: no geo-fence check, no
# contact-hours guard, nothing that could reject a safety-relevant position.

@router.post("/location", response_model=LocationBatchResponse)
def record_location(req: LocationBatchRequest, current_user: AgentOnly, db: DbSession):
    from app.services.location_service import LocationService
    agent = _get_agent_or_404(current_user, db)
    return LocationService(db).record_batch(agent, req.pings)


# ---------------------------------------------------------------------------
# POST /agent/sos
# ---------------------------------------------------------------------------

@router.post("/sos", response_model=SOSResponse)
def trigger_sos(req: SOSRequest, current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    agent = _get_agent_or_404(current_user, db)
    return AgentService(db).trigger_sos(agent, req)


# ---------------------------------------------------------------------------
# POST /agent/sos/cancel
# ---------------------------------------------------------------------------

@router.post("/sos/cancel", response_model=SOSCancelResponse)
def cancel_sos(current_user: AgentOnly, db: DbSession):
    from app.services.agent_service import AgentService
    agent = _get_agent_or_404(current_user, db)
    return AgentService(db).cancel_sos(agent)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/handover
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/handover", response_model=HandoverResponse)
def handover_case(case_id: str, req: HandoverRequest, current_user: AgentOnly, db: DbSession):
    from app.services.case_service import CaseService
    agent = _get_agent_or_404(current_user, db)
    return CaseService(db).handover_case(agent, case_id, req)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/call-log
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/call-log", status_code=201, response_model=LogCallResponse)
def log_call(case_id: str, req: LogCallRequest, current_user: AgentOnly, db: DbSession):
    """Record a phone call attempt and any scheduling/payment intel gathered."""
    from app.models.call_log import CallLog

    agent = _get_agent_or_404(current_user, db)
    case = _get_accessible_case_or_404(db, agent, case_id)

    log = CallLog(
        case_id=case_id,
        agent_id=agent.id,
        customer_id=case.customer_id,
        called_at=datetime.now(timezone.utc),
        duration_seconds=req.duration_seconds,
        outcome=req.outcome,
        phone_used=req.phone_used,
        customer_response_notes=req.customer_response_notes,
        visit_feasible_today=req.visit_feasible_today,
        best_time_to_visit=req.best_time_to_visit,
        available_from=req.available_from,
        available_until=req.available_until,
        blocked_until_date=req.blocked_until_date,
        alternate_location_hint=req.alternate_location_hint,
        payment_intent_signalled=req.payment_intent_signalled,
        verbal_payment_date=req.verbal_payment_date,
        ai_intel_summary=req.ai_intel_summary,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {
        "id": log.id,
        "called_at": log.called_at.isoformat(),
        "outcome": log.outcome,
    }


# ---------------------------------------------------------------------------
# PATCH /agent/customers/{customer_id}/flag
# ---------------------------------------------------------------------------

@router.patch("/customers/{customer_id}/flag", response_model=FlagCustomerResponse)
def flag_customer(customer_id: str, req: CustomerFlagRequest, current_user: AgentOnly, db: DbSession):
    from app.services.case_service import CaseService
    agent = _get_agent_or_404(current_user, db)
    return CaseService(db).flag_customer(agent, customer_id, req)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/photo-upload-url
# Returns a pre-signed MinIO PUT URL so the app can upload a geo-tagged photo.
# subject: "agent" | "borrower" | "object" | "signature"
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/photo-upload-url")
def get_photo_upload_url(case_id: str, subject: str, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    return MediaService(db).get_photo_upload_url(agent, case_id, subject)


@router.get("/cases/{case_id}/photos")
def get_case_photos(case_id: str, current_user: AgentOnly, db: DbSession):
    """Return latest geo-tagged photo per type for this case, extracted from visits."""
    agent = _get_agent_or_404(current_user, db)
    return MediaService(db).get_case_photos(agent, case_id)


# ---------------------------------------------------------------------------
# POST /agent/cases/{case_id}/recording-upload-url
# Returns a pre-signed MinIO PUT URL so the app can upload an .mp4 directly.
# recorder: "agent" | "borrower"
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/recording-upload-url")
def get_recording_upload_url(case_id: str, recorder: str, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    return MediaService(db).get_recording_upload_url(agent, case_id, recorder)


# ---------------------------------------------------------------------------
# GET /agent/visits/{visit_id}/recording-urls
# Returns pre-signed playback URLs for both recordings on a visit.
# ---------------------------------------------------------------------------

@router.get("/visits/{visit_id}/recording-urls")
def get_recording_playback_urls(visit_id: str, current_user: AgentOnly, db: DbSession):
    agent = _get_agent_or_404(current_user, db)
    return MediaService(db).get_recording_playback_urls(agent, visit_id)


# ---------------------------------------------------------------------------
# POST /agent/visits/{visit_id}/transcribe
# Queues a background Celery task that downloads the recording(s) from
# MinIO and transcribes+translates them via core/transcription.py
# (TRANSCRIPTION_PROVIDER: hosted OpenAI Whisper, or self-hosted
# faster-whisper). recorder: "agent" | "borrower" | "both"
# ---------------------------------------------------------------------------

@router.post("/visits/{visit_id}/transcribe", status_code=202, response_model=TranscribeQueuedResponse)
def transcribe_visit_recording(visit_id: str, current_user: AgentOnly, db: DbSession, recorder: str = "both"):
    """Enqueue transcription; returns immediately with a task id.

    See MediaService.queue_visit_transcription for the actual logic (why
    this runs via Celery rather than inline).
    """
    agent = _get_agent_or_404(current_user, db)
    return MediaService(db).queue_visit_transcription(agent, visit_id, recorder)


# ---------------------------------------------------------------------------
# POST /agent/transcribe-audio
# Ad-hoc, not tied to a Case/Visit — for the voice-note dictation widgets in
# RecordVisitPage, which run *before* a Visit row exists to attach to.
# ---------------------------------------------------------------------------

@router.post("/transcribe-audio", response_model=TranscribeAudioResponse)
def transcribe_audio(current_user: AgentOnly, db: DbSession, audio: UploadFile = File(...)):
    """Transcribe (and translate to English) a short voice-note clip, synchronously.

    See MediaService.transcribe_audio_adhoc for why this runs inline rather
    than via Celery (unlike /visits/{visit_id}/transcribe).
    """
    if settings.TRANSCRIPTION_PROVIDER == "openai" and not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Transcription service not configured (missing OPENAI_API_KEY)")

    _get_agent_or_404(current_user, db)   # auth check only — not tied to any specific case

    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    try:
        text = MediaService.transcribe_audio_adhoc(audio_bytes, audio.filename or "note.webm")
    except Exception as exc:
        logger.warning("transcribe_audio.failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Transcription failed") from exc

    return {"text": text}


# ---------------------------------------------------------------------------
# AI Visit Strategy Brief
# ---------------------------------------------------------------------------

@router.get("/cases/{case_id}/visit-strategy")
def get_visit_strategy(case_id: str, current_user: AgentOnly, db: DbSession):
    """Generate an LLM-powered customer approach strategy for this case.

    Reads the last 3 visits (transcripts + ai_visit_note) and last 5 call logs,
    builds a prompt, and returns a structured strategy brief via GPT-4o-mini.
    Falls back to a rule-based brief if the OpenAI key is missing.
    """
    from app.models.call_log import CallLog
    import json as _json
    from datetime import datetime as _dt

    agent = _get_agent_or_404(current_user, db)
    case = _get_accessible_case_or_404(db, agent, case_id)

    customer = case.customer
    loan = case.loan

    # Fetch last 3 visits (most recent first)
    visits = (
        db.query(Visit)
        .filter(Visit.case_id == case_id)
        .order_by(Visit.check_in_time.desc())
        .limit(3)
        .all()
    )

    # Fetch last 5 call logs (most recent first)
    call_logs = (
        db.query(CallLog)
        .filter(CallLog.case_id == case_id)
        .order_by(CallLog.called_at.desc())
        .limit(5)
        .all()
    )

    # ── Build context string for prompt ──────────────────────────────────────
    visit_lines = []
    for v in visits:
        line = (
            f"  [{v.check_in_time.strftime('%d %b %Y %I:%M %p')}] Visit #{v.visit_number}"
            f" — Outcome: {v.outcome.value}"
            f" | Customer met: {'Yes' if v.customer_met else 'No'}"
        )
        if v.person_met:
            line += f" | Person met: {v.person_met.value}"
        if v.default_reason:
            line += f" | Default reason: {v.default_reason.value.replace('_', ' ')}"
        if v.not_met_reason:
            line += f" | Not met reason: {v.not_met_reason.value.replace('_', ' ')}"
        if v.agent_recording_transcript:
            line += f"\n     Agent note: \"{v.agent_recording_transcript[:300]}\""
        if v.borrower_recording_transcript:
            line += f"\n     Customer said: \"{v.borrower_recording_transcript[:200]}\""
        if v.ai_visit_note:
            line += f"\n     AI summary: \"{v.ai_visit_note[:250]}\""
        visit_lines.append(line)

    call_lines = []
    for cl in call_logs:
        line = (
            f"  [{cl.called_at.strftime('%d %b %Y %I:%M %p')}]"
            f" {cl.outcome.value}"
            f" ({cl.duration_seconds or 0}s)"
        )
        if cl.customer_response_notes:
            line += f"\n     Agent call note: \"{cl.customer_response_notes[:200]}\""
        if cl.best_time_to_visit:
            line += f"\n     Best time to visit: {cl.best_time_to_visit}"
        if cl.blocked_until_date:
            line += f"\n     BLOCKED until: {cl.blocked_until_date.strftime('%d %b %Y')}"
        if cl.payment_intent_signalled:
            line += "\n     Payment intent signalled: YES"
        if cl.verbal_payment_date:
            line += f"\n     Verbal payment date: {cl.verbal_payment_date.strftime('%d %b %Y')}"
        if cl.ai_intel_summary:
            line += f"\n     AI call intel: {cl.ai_intel_summary[:150]}"
        call_lines.append(line)

    flags = []
    if customer.is_hostile:
        flags.append("HOSTILE customer — approach with caution, avoid confrontation")
    if customer.do_not_contact:
        flags.append("DO NOT CONTACT flag — legal complaint filed, no field visit allowed")
    if customer.requires_female_agent:
        flags.append("FEMALE AGENT REQUIRED — culturally sensitive household")
    if loan.legal_status and loan.legal_status != "NONE":
        flags.append(f"Legal status: {loan.legal_status.replace('_', ' ')}")
    if case.is_escalated:
        flags.append("Case ESCALATED — manager review pending")

    prompt = f"""You are a collections intelligence assistant at a financial recovery agency in India.
Generate a structured visit strategy brief for a field agent about to visit this NPA customer.

--- CASE CONTEXT ---
Customer: {customer.full_name} ({customer.customer_segment or 'Unknown'}, {customer.city})
Language: {customer.language_preference}
Loan: {loan.loan_type.replace('_', ' ')} with {loan.bank_name}
DPD: {loan.dpd} days overdue | Bucket: {loan.dpd_bucket.value}
Total outstanding: ₹{loan.total_outstanding:,.0f} | Overdue: ₹{loan.overdue_amount:,.0f}
Case target: ₹{case.target_amount:,.0f} | Collected so far: ₹{case.collected_amount:,.0f}
Collection stage: {case.collection_stage or 'FIELD'} | Visit {case.visit_count + 1} of {case.max_visits_allowed} allowed

--- VISIT HISTORY (last 3 visits, most recent first) ---
{chr(10).join(visit_lines) if visit_lines else '  No prior visits — first approach.'}

--- CALL HISTORY (last 5 calls) ---
{chr(10).join(call_lines) if call_lines else '  No call logs — no pre-visit calls recorded.'}

--- RISK FLAGS ---
{chr(10).join('  • ' + f for f in flags) if flags else '  None'}

--- BANK REMARKS ---
{case.bank_agent_remarks or 'None on file.'}

Based on all the above, generate a visit strategy brief. Respond ONLY with a valid JSON object — no markdown, no explanation — using exactly these fields:
{{
  "best_time_to_visit": "specific time window based on call intel or visit history, or 'No timing intel — try morning 9–11 AM'",
  "customer_situation": "2-3 sentence summary of the customer's financial situation and behaviour pattern",
  "recommended_approach": "2-3 sentences on tone, opening, what to offer or avoid",
  "payment_readiness": "LOW or MEDIUM or HIGH",
  "risk_flags": ["list of 1–4 short risk strings"],
  "key_leverage_points": ["list of 1–3 actionable intel points the agent should use"],
  "opening_line": "a natural opening sentence the agent should say in Hindi or Hinglish to open the conversation warmly"
}}"""

    # ── Ask the model ─────────────────────────────────────────────────────────
    # 2026-08-19 — routed through core/llm.py. The rule-based fallback below is
    # unchanged; what is new is that the agent is TOLD which one they are
    # reading. Someone standing at a door deciding how to open a conversation
    # should know whether their brief came from a model or from a rule.
    strategy: dict = {}
    _llm = llm.complete(
        prompt, purpose="visit_strategy", json_mode=True,
        temperature=0.4, max_tokens=1500,
    )
    if _llm.ai_generated:
        strategy = _llm.data

    # ── Rule-based fallback ───────────────────────────────────────────────────
    if not strategy:
        # Derive best_time from most recent call log with timing intel
        best_time = "No timing intel — try morning 9–11 AM"
        for cl in call_logs:
            if cl.best_time_to_visit:
                best_time = cl.best_time_to_visit
                break

        payment_rdy = "LOW"
        if loan.dpd <= 60:
            payment_rdy = "MEDIUM"
        for cl in call_logs:
            if cl.payment_intent_signalled:
                payment_rdy = "HIGH"
                break

        strategy = {
            "best_time_to_visit": best_time,
            "customer_situation": f"{customer.full_name} has {loan.dpd} days overdue on a {loan.loan_type.replace('_',' ')} loan. {case.visit_count} previous visit(s) recorded.",
            "recommended_approach": "Greet the customer by name. Acknowledge their situation with empathy. Present a clear repayment path and avoid confrontation.",
            "payment_readiness": payment_rdy,
            "risk_flags": flags[:4] if flags else ["No flags — standard approach"],
            "key_leverage_points": ["Build rapport before discussing numbers", "Reference previous commitment if any PTP exists"],
            "opening_line": f"Namaste {customer.full_name} ji, main TIQ Collections se bol raha hoon. Aapka loan account ke baare mein baat karni thi.",
        }

    strategy["generated_at"] = _dt.now(timezone.utc).isoformat()
    strategy["case_id"] = case_id
    # Which of the two the agent is actually looking at.
    strategy["ai_generated"] = _llm.ai_generated
    strategy["ai_status"] = _llm.status
    strategy["ai_failure_reason"] = _llm.failure_reason
    return strategy


# ---------------------------------------------------------------------------
# Availability Calendar  (no new table — derived from Beat records)
# ---------------------------------------------------------------------------

@router.get("/availability/calendar", response_model=AvailabilityCalendarResponse)
def get_availability_calendar(current_user: AgentOnly, db: DbSession):
    """Return a 6-month day-by-day duty calendar derived from Beat records.
    Days with a beat = ON_DUTY; days without = OFF_DUTY (effectively off/leave).
    Sundays are excluded (RBI mandate: no field collection on Sundays).
    """
    from app.services.agent_service import AgentService
    return AgentService(db).availability_calendar(current_user)


# ---------------------------------------------------------------------------
# GET /agent/voice/token  — Twilio Voice access token for browser calling
# ---------------------------------------------------------------------------

@router.get("/voice/token")
def get_voice_token(current_user: AgentOnly, db: DbSession):
    if not all([settings.TWILIO_ACCOUNT_SID, settings.TWILIO_API_KEY_SID,
                settings.TWILIO_API_KEY_SECRET, settings.TWILIO_TWIML_APP_SID]):
        raise HTTPException(status_code=503, detail="Twilio Voice not configured")
    try:
        from twilio.jwt.access_token import AccessToken
        from twilio.jwt.access_token.grants import VoiceGrant
        token = AccessToken(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_API_KEY_SID,
            settings.TWILIO_API_KEY_SECRET,
            identity=str(current_user.id),
            ttl=3600,
        )
        token.add_grant(VoiceGrant(
            outgoing_application_sid=settings.TWILIO_TWIML_APP_SID,
            incoming_allow=False,
        ))
        return {"token": token.to_jwt()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /agent/voice/outbound  — TwiML for outbound calls (called by Twilio)
# ---------------------------------------------------------------------------

@router.post("/voice/outbound")
def voice_outbound(PhoneTo: str = Form(default="")):
    # The frontend Voice SDK sends the destination as the custom param `PhoneTo`
    # (Twilio's own `To` param is the client identity, not the dialed number).
    try:
        from twilio.twiml.voice_response import VoiceResponse, Dial
    except ImportError:
        return FastAPIResponse(content="<Response><Say>Service unavailable</Say></Response>",
                               media_type="application/xml")
    resp = VoiceResponse()
    if PhoneTo:
        dial = Dial(caller_id=settings.TWILIO_PHONE_NUMBER)
        dial.number(PhoneTo)
        resp.append(dial)
    else:
        resp.say("No destination number provided.")
    return FastAPIResponse(content=str(resp), media_type="application/xml")
