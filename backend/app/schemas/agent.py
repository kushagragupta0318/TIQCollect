# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — LogCallRequest gained available_from/available_until
#   (line 114-115); added LogCallResponse (line 123) and ReoptimizeBeatResponse
#   (line 141) as the first typed response_model schemas outside auth.py.
# 2026-07-14 — Added VisitResponse (line 129) for POST .../visit,
#   matching the router→service extraction in visit_service.py. Added
#   TranscribeQueuedResponse (line 155) for the now-async POST
#   .../visits/{visit_id}/transcribe. Added TranscribeAudioResponse
#   (line 161) for the new ad-hoc POST .../transcribe-audio.
# 2026-07-14 (API fix pass) — RecordVisitRequest gained `notes` — it was
#   being sent by the frontend but had no field here, so Pydantic silently
#   dropped it before it ever reached the service layer.
# 2026-07-21 — Added PaymentResponse/PaymentLinkResponse/PTPResponse (end
#   of file) for the payment_service.py extraction — each mirrors the
#   exact dict shape the old inline endpoints already returned, field for
#   field, so this is a typing-only change with no response-shape change.
# 2026-07-22 — Added CustomerSummary/LoanSummary/CaseSummaryResponse and
#   everything built on them (CaseListItemResponse/RankedCaseResponse/
#   CaseDetailResponse/BeatCaseItemResponse/BeatResponse/HomeSummaryResponse/
#   ProfileResponse/AvailabilityCalendarResponse and their nested items) —
#   typed contracts for the endpoints that previously returned untyped
#   dicts (list_cases/ranked_cases/case_detail/home_summary/get_beat/
#   get_profile/availability_calendar). Every field was cross-checked
#   against the real SQLAlchemy model column it comes from (see
#   changelog.md for the full field-by-field trace) before being typed —
#   getting a type wrong here would mean FastAPI's response_model either
#   silently drops that field from the response or raises a validation
#   error at request time, both of which are real breaks, not typos.
#   `_risk_category`/`_risk_score`/`_fraud_flag` (leading-underscore keys
#   in the existing dict output) use Field(alias=...) + populate_by_name,
#   since Pydantic v2 treats a literal leading-underscore attribute name
#   as a private attribute, not a model field. Full detail + why:
#   /changelog.md
# 2026-07-30 — Borrower OTP verification: CollectPaymentRequest gained an
#   optional `verification_id` (the used OTP that authorises writing the
#   Payment straight as VERIFIED; absent = offline/pending path, unchanged).
#   Added OtpSendRequest/OtpSendResponse and OtpVerifyRequest/OtpVerifyResponse
#   (end of file) for the two new OTP endpoints. See
#   prototype_to_product/30.07.md and /changelog.md.
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from app.core.ids import UUIDStr
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from datetime import date, datetime
from typing import Optional
from app.models.visit import VisitOutcome, PersonMet, DefaultReason, NotMetReason
from app.models.payment import PaymentMode, PaymentStatus
from app.models.ptp import PTPStatus
from app.models.case import CaseStatus, CasePriority, EscalationReason
from app.models.customer import RiskCategory
from app.models.loan import LoanType, LoanStatus, DPDBucket
from app.models.beat import BeatStatus
from app.models.agent import AgentStatus, AgentTier, AgentSpecialization
from app.models.call_log import BorrowerDisposition, CallOutcome


class CheckInRequest(BaseModel):
    latitude: float
    longitude: float
    selfie_key: Optional[str] = None


class OfflineCapture(BaseModel):
    """I02 offline outbox (docs/adr/0011-offline-outbox.md). All absent = a live
    submit, judged exactly as before. services/capture_time.py bounds them."""
    # Client-generated at capture; a repeat returns the row already stored.
    client_submission_id: Optional[UUIDStr] = None
    # The phone's clock when the agent pressed Submit.
    captured_at: Optional[AwareDatetime] = None
    # Strictly increasing per device across every outbox item.
    device_seq: Optional[int] = Field(default=None, ge=1, le=2**62)
    # The device the item was captured on (for a visit: also the photos' device).
    device_id: Optional[str] = Field(default=None, max_length=200)


class RecordVisitRequest(OfflineCapture):
    check_in_latitude: float
    check_in_longitude: float
    customer_met: bool
    outcome: VisitOutcome
    # Who was spoken to (required when customer_met=True)
    person_met: Optional[PersonMet] = None
    # Why the customer is defaulting (for payment/RTP/dispute outcomes)
    default_reason: Optional[DefaultReason] = None
    not_met_reason: Optional[NotMetReason] = None
    # ML-1 (2026-09-24): the borrower's stance, the agent's read of it. Only
    # when the borrower was met (services/borrower_stance.py). Never defaulted.
    borrower_disposition: Optional[BorrowerDisposition] = None
    notes: Optional[str] = None
    selfie_photo_key: Optional[str] = None
    # Geo-tagged photos — full metadata per photo type (keys from /photo-upload-url)
    agent_photo_key: Optional[str] = None
    agent_photo_lat: Optional[float] = None
    agent_photo_lon: Optional[float] = None
    agent_photo_accuracy: Optional[float] = None
    agent_photo_altitude: Optional[float] = None
    agent_photo_captured_at: Optional[str] = None   # ISO-8601 datetime
    agent_photo_sha256: Optional[str] = None
    borrower_photo_key: Optional[str] = None
    borrower_photo_lat: Optional[float] = None
    borrower_photo_lon: Optional[float] = None
    borrower_photo_accuracy: Optional[float] = None
    borrower_photo_altitude: Optional[float] = None
    borrower_photo_captured_at: Optional[str] = None
    borrower_photo_sha256: Optional[str] = None
    object_photo_key: Optional[str] = None
    object_photo_lat: Optional[float] = None
    object_photo_lon: Optional[float] = None
    object_photo_accuracy: Optional[float] = None
    object_photo_altitude: Optional[float] = None
    object_photo_captured_at: Optional[str] = None
    object_photo_sha256: Optional[str] = None
    # Visit recordings (keys returned by /agent/cases/{id}/recording-upload-url)
    agent_recording_key: Optional[str] = None
    borrower_recording_key: Optional[str] = None
    # Field investigation (Phase 1D)
    property_type: Optional[str] = None
    occupancy_status: Optional[str] = None
    vehicle_present: Optional[bool] = None
    business_running: Optional[bool] = None
    # Consent
    consent_given: Optional[bool] = None
    signature_key: Optional[str] = None


class CollectPaymentRequest(BaseModel):
    amount: float = Field(gt=0)
    mode: PaymentMode
    visit_id: Optional[UUIDStr] = None       # link payment to the visit that triggered it
    upi_reference: Optional[str] = None
    cheque_number: Optional[str] = None
    cheque_date: Optional[str] = None        # ISO date, for post-dated cheques
    cheque_bank: Optional[str] = None
    bank_reference: Optional[str] = None     # UTR / NEFT reference
    receipt_photo_key: Optional[str] = None
    # Borrower OTP that authorises this collection. Present → Payment is written
    # as VERIFIED. Absent → Payment stays PENDING_VERIFICATION (offline/deferred
    # path; borrower verifies later once they have signal).
    verification_id: Optional[str] = None


class SetPTPRequest(OfflineCapture):
    committed_amount: float = Field(gt=0)
    committed_date: date
    customer_reason: Optional[str] = None
    agent_notes: Optional[str] = None
    follow_up_date: Optional[date] = None


class SOSRequest(BaseModel):
    # Optional since 2026-08-18. SOSButton.tsx used to guarantee these by
    # substituting hardcoded Gurugram coordinates whenever the browser did
    # not answer in 2.5s, which made every alert carry a plausible-looking
    # position regardless of whether one was known. The client now sends
    # nothing rather than something false, and the server falls back to the
    # last tracked fix — labelled as such.
    latitude: float | None = None
    longitude: float | None = None
    accuracy_metres: float | None = None
    battery_pct: int | None = None


class HandoverRequest(BaseModel):
    notes: str
    return_to_pool: bool = True


class CustomerFlagRequest(BaseModel):
    is_hostile: Optional[bool] = None
    do_not_contact: Optional[bool] = None


class LogCallRequest(OfflineCapture):
    outcome: CallOutcome
    duration_seconds: Optional[int] = None
    phone_used: Optional[str] = None          # "PRIMARY" | "ALTERNATE"
    customer_response_notes: Optional[str] = None
    visit_feasible_today: Optional[bool] = None
    best_time_to_visit: Optional[str] = None
    available_from: Optional[datetime] = None
    available_until: Optional[datetime] = None
    blocked_until_date: Optional[date] = None
    alternate_location_hint: Optional[str] = None
    payment_intent_signalled: Optional[bool] = None
    verbal_payment_date: Optional[date] = None
    ai_intel_summary: Optional[str] = None
    # ML-1 (2026-09-24): what the borrower said about paying, on an ANSWERED
    # call only (services/borrower_stance.py). Never defaulted.
    borrower_disposition: Optional[BorrowerDisposition] = None


class LogCallResponse(BaseModel):
    id: str
    called_at: str   # already .isoformat()'d before being returned
    outcome: CallOutcome


class VisitResponse(BaseModel):
    id: str
    case_id: str
    outcome: VisitOutcome
    geo_verified: bool
    distance_from_customer_metres: float
    within_contact_hours: bool
    visit_number: int
    check_in_time: str   # already .isoformat()'d before being returned
    ai_visit_note: Optional[str] = None


class ReoptimizeBeatResponse(BaseModel):
    ordered_case_ids: list[str]
    optimized: bool
    # Only present on the "found pending cases, ran the solver" path
    pending_count: Optional[int] = None
    done_count: Optional[int] = None
    blocked_count: Optional[int] = None
    urgent_case_id: Optional[str] = None
    # Only present on the "nothing to route" early-return path
    message: Optional[str] = None


class TranscribeQueuedResponse(BaseModel):
    status: str
    task_id: str


class TranscribeAudioResponse(BaseModel):
    text: str


class PaymentResponse(BaseModel):
    id: str
    receipt_number: str
    amount: float
    mode: PaymentMode
    status: PaymentStatus
    payment_date: str   # already .isoformat()'d before being returned
    case_status: CaseStatus
    total_collected: float
    # Whether the borrower's receipt reached the SMS/WhatsApp transport. False
    # covers no phone, Twilio unconfigured, and a failed send; the payment is
    # recorded either way. Defaulted so any older construction still validates.
    receipt_sent: bool = False


class PaymentLinkResponse(BaseModel):
    image_url: str
    qr_id: str


class PTPResponse(BaseModel):
    id: str
    case_id: str
    committed_amount: float
    committed_date: str   # already .isoformat()'d before being returned
    follow_up_date: Optional[str] = None
    status: PTPStatus


# ─── Borrower payment-verification OTP (2026-07-30) ───────────────────────
class OtpSendRequest(BaseModel):
    amount: float = Field(gt=0)
    # Optional: the OTP is issued before the payment channel is chosen, so it
    # binds to the amount. Only the deferred flow (existing payment) has a mode.
    mode: Optional[PaymentMode] = None
    # Present → deferred flow: re-verify an already-created PENDING payment.
    # Absent  → pre-collection flow: verify first, then collect.
    payment_id: Optional[UUIDStr] = None


class OtpSendResponse(BaseModel):
    otp_id: str
    masked_phone: str
    expires_at: str            # ISO — already .isoformat()'d
    resend_available_at: str   # ISO — earliest a resend is allowed (throttle)
    demo_otp: Optional[str] = None
    # Whether the code reached the SMS transport. Until 2026-09-11 this
    # endpoint could not say, and answered 200 over a failed send. Defaulted so
    # any older construction still validates.
    sms_sent: bool = False


class OtpVerifyRequest(BaseModel):
    otp_id: str
    code: str


class OtpVerifyResponse(BaseModel):
    verified: bool
    otp_id: str
    payment_id: Optional[str] = None   # set when the OTP verified an existing (deferred) payment


class HandoverResponse(BaseModel):
    success: bool
    returned_to_pool: bool


class FlagCustomerResponse(BaseModel):
    success: bool
    is_hostile: bool
    do_not_contact: bool


class CheckInResponse(BaseModel):
    status: str
    message: str


class SOSResponse(BaseModel):
    sos_triggered: bool
    triggered_at: str
    message: str
    # LIVE | LAST_KNOWN | NONE — the agent is told which, so a
    # failed GPS read is visible to them rather than silently
    # replaced with a fabricated position.
    location_quality: str = "NONE"
    location_age_seconds: int | None = None
    manager_notified: bool = False


class SOSCancelResponse(BaseModel):
    sos_cancelled: bool
    message: str


# ─── Case / beat / profile response contracts (2026-07-22) ────────────────
# Mirrors _format_case() in endpoints/agent.py field for field. If that
# function's output shape ever changes, these must change with it.

class CustomerSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    customer_ref: str
    full_name: str
    pan_masked: str
    aadhaar_masked: str
    phone_primary: str
    phone_alternate: Optional[str] = None
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    pincode: str
    latitude: float
    longitude: float
    is_hostile: bool
    do_not_contact: bool
    requires_female_agent: bool
    customer_segment: str
    language_preference: str
    # Leading-underscore keys in the source dict — Pydantic v2 treats a
    # literal leading-underscore attribute as a private attribute, not a
    # model field, so these are declared under a plain name with the
    # real dict key set as the alias (populate_by_name above lets either
    # name populate; FastAPI's response_model_by_alias=True default means
    # the alias, i.e. the original "_..." key, is what actually goes out
    # on the wire — unchanged from today's output).
    risk_category: RiskCategory = Field(alias="_risk_category")
    risk_score: float = Field(alias="_risk_score")
    fraud_flag: bool = Field(alias="_fraud_flag")


class LoanSummary(BaseModel):
    id: str
    loan_account_number: str
    loan_account_masked: Optional[str] = None
    loan_type: LoanType
    bank_name: str
    dpd: int
    dpd_bucket: DPDBucket
    status: LoanStatus
    npa_flag: bool
    sanctioned_amount: float
    outstanding_principal: float
    outstanding_interest: float
    penal_charges: float
    total_outstanding: float
    overdue_amount: float
    emi_amount: float
    tenure_months: Optional[int] = None
    interest_rate: float
    last_payment_date: Optional[date] = None
    last_payment_amount: float
    next_due_date: Optional[date] = None
    legal_status: str
    settlement_status: str


# ── Visit priority (2026-08-27) ─────────────────────────────────────────────
# The three named components travel TOGETHER in a nested model, not as loose
# sibling fields. The brief's requirement is that a manager can explain why one
# case sits above another; a score whose components can be dropped independently
# by a later "let's slim the payload" pass stops being explainable while still
# looking like a score.
class VisitPriorityComponent(BaseModel):
    code: str
    points: float
    summary: str
    evidence: dict = {}
    # True when the component had no data to work with. Distinguishes "we
    # measured this and it is low" from "we have not measured it".
    abstained: bool = False


class VisitPriority(BaseModel):
    score: float
    # HIGH / MEDIUM / LOW. Sent so a screen can say what a bare "11 / 100"
    # means without asserting a queue position the score does not support.
    band: str
    components: list[VisitPriorityComponent]
    reason: str
    # A hand-weighted scorecard. Sent so no screen can present it as a model.
    is_modelled: bool = False
    model_version: str
    # How old the recovery rate behind the value component is. The score is
    # computed now; its main input is as fresh as the last scoring run, and
    # conflating the two would overstate its currency.
    rate_as_of: Optional[str] = None

    # `model_version` collides with Pydantic v2's protected `model_` namespace,
    # which would otherwise emit a UserWarning on every import.
    model_config = ConfigDict(protected_namespaces=())


class CaseSummaryResponse(BaseModel):
    id: str
    case_number: str
    status: CaseStatus
    priority: CasePriority
    target_amount: float
    collected_amount: float
    allocation_date: Optional[date] = None
    visit_count: int
    is_escalated: bool
    agent_id: Optional[str] = None
    max_visits_allowed: int
    handover_notes: Optional[str] = None
    customer: CustomerSummary
    loan: LoanSummary
    collection_stage: str
    bank_ptp_date: Optional[date] = None
    bank_ptp_amount: Optional[float] = None
    bank_ptp_status: Optional[str] = None
    bank_agent_remarks: Optional[str] = None
    # None on a case whose loan carries no balance at all — the list still
    # renders, the card just shows no score rather than a fabricated zero.
    visit_priority: Optional[VisitPriority] = None


class CaseListItemResponse(CaseSummaryResponse):
    is_visited_today: bool


class RankedCaseResponse(CaseListItemResponse):
    ptp_due_today: bool
    rank_score: int
    rank_badge: str
    rank_badge_color: str
    rank_reason: str = ""
    rank: Optional[int] = None   # set after sorting; absent would be a bug, kept optional defensively


class BeatCaseItemResponse(CaseSummaryResponse):
    ptp_due_today: bool


class CaseVisitHistoryItem(BaseModel):
    id: str
    visit_number: int
    outcome: VisitOutcome
    customer_met: bool
    person_met: Optional[PersonMet] = None
    default_reason: Optional[DefaultReason] = None
    not_met_reason: Optional[NotMetReason] = None
    notes: Optional[str] = None
    consent_given: Optional[bool] = None
    ai_visit_note: Optional[str] = None
    geo_verified: bool
    within_contact_hours: bool
    distance_from_customer_metres: float
    check_in_time: str   # already .isoformat()'d before being returned
    agent_name: str
    property_type: Optional[str] = None
    occupancy_status: Optional[str] = None
    vehicle_present: Optional[bool] = None
    business_running: Optional[bool] = None
    agent_recording_transcript: Optional[str] = None
    borrower_recording_transcript: Optional[str] = None


class CasePhotoItem(BaseModel):
    photo_type: str
    storage_key: str
    view_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_metres: Optional[float] = None
    altitude_metres: Optional[float] = None
    captured_at: Optional[str] = None
    sha256: Optional[str] = None
    device_id: Optional[str] = None
    visit_id: str
    agent_id: str
    case_id: str
    status: str
    visit_date: str   # already .isoformat()'d before being returned
    is_latest: bool


class CaseDetailPaymentItem(BaseModel):
    id: str
    receipt_number: str
    amount: float
    mode: PaymentMode
    status: PaymentStatus
    payment_date: str   # already .isoformat()'d before being returned


class CaseDetailPTPItem(BaseModel):
    id: str
    committed_amount: float
    committed_date: str   # already .isoformat()'d before being returned
    follow_up_date: Optional[str] = None
    status: PTPStatus
    customer_reason: Optional[str] = None


class RepaymentFactorItem(BaseModel):
    """One line of the score's reasoning — or a stated abstention.

    A factor with no evidence carries `abstained` and a `reason` and NO points.
    That distinction has to survive serialisation: rendering an abstention as a
    contribution of zero puts a reason in front of an agent that the scorecard
    never gave.
    """
    code: str
    direction: Optional[str] = None
    points: Optional[float] = None
    summary: Optional[str] = None
    evidence: Optional[dict] = None
    abstained: Optional[bool] = None
    reason: Optional[str] = None


class RepaymentScoreItem(BaseModel):
    likelihood: float
    risk_score: float
    band: str
    risk_category: str
    source: str
    model_version: str
    # False for a hand-weighted scorecard, true only for a trained model. Kept
    # on the wire so the UI cannot present one as the other.
    is_modelled: bool
    # False when too little evidence spoke to justify showing a number.
    is_confident: bool
    evidence_coverage: float
    as_of: str
    factors: list[RepaymentFactorItem]


class CaseDetailResponse(CaseSummaryResponse):
    visits: list[CaseVisitHistoryItem]
    photos: list[CasePhotoItem]
    payments: list[CaseDetailPaymentItem]
    ptps: list[CaseDetailPTPItem]
    # Declared, or FastAPI silently drops it. The service computed this block
    # correctly and response_model filtering discarded it before it reached the
    # wire — no error, no log, just a missing key. Anything case_detail() adds
    # from here on needs a field here too.
    repayment: Optional[RepaymentScoreItem] = None


class HomeSummaryResponse(BaseModel):
    cases_today: int
    total_target_today: float
    visits_done: int
    amount_collected_today: float
    ptps_due_today: int
    check_in_status: AgentStatus
    beat_status: Optional[BeatStatus] = None
    sos_active: bool


class BeatResponse(BaseModel):
    id: str
    beat_date: str   # already .isoformat()'d before being returned
    beat_number: str
    ordered_case_ids: list[str]
    total_cases: int
    estimated_distance_km: float
    estimated_duration_minutes: int
    total_target_amount: float
    status: BeatStatus
    # Road geometry for the beat map. Optional because beats planned before
    # 2026-09-08 have none, and because a Haversine fallback produces no
    # polyline at all — route_source tells the client which it is looking at.
    route_geometry: Optional[str] = None
    route_source: Optional[str] = None
    start_latitude: Optional[float] = None
    start_longitude: Optional[float] = None
    cases_visited_today: int
    visited_today_ids: list[str]
    # Cases on today's route still needing a visit, and the ones that need
    # none because they are already resolved. One rule, computed in
    # services/agent_service.py — see the note there (2026-09-22).
    cases_pending: int
    no_visit_needed_ids: list[str]
    amount_collected_today: float
    cases: list[BeatCaseItemResponse]
    ptps_due_today: int
    check_in_status: AgentStatus
    sos_active: bool


class ProfileResponse(BaseModel):
    id: str
    user_id: str
    employee_code: str
    id_card_number: str
    full_name: str
    email: str
    phone: str
    date_of_birth: Optional[date] = None
    territory: str
    tier: AgentTier
    status: AgentStatus
    specialization: AgentSpecialization
    languages_spoken: list[str]
    ranking_score: float
    lifetime_collection_rate: float
    max_cases_per_day: int
    current_month_visits: int
    current_month_collections: float
    current_month_ptps_set: int
    current_month_ptps_honored: int
    last_known_latitude: Optional[float] = None
    last_known_longitude: Optional[float] = None
    sos_active: bool
    cases_today: int
    agency_name: Optional[str] = None
    agency_rbi_registration_no: Optional[str] = None
    # The signed token for the ID card's QR (G05) — core/security.create_agent_verify_token,
    # validated by the public GET /verify-agent. Minted fresh on every profile fetch.
    verify_token: str


class AvailabilityDay(BaseModel):
    date: str
    day_of_week: str
    status: str            # "ON_DUTY" | "OFF_DUTY" | "ON_LEAVE" (2026-09-21) — literal string built in the service
    beat_status: Optional[str] = None
    cases: int
    leave_type: Optional[str] = None   # set only when status is ON_LEAVE


class AvailabilitySummary(BaseModel):
    total_working_days: int
    on_duty_days: int
    off_duty_days: int
    attendance_rate_pct: float


class AvailabilityMonthlySummary(BaseModel):
    month: str
    on_duty: int
    off_duty: int
    total_cases: int
    attendance_pct: float


class AvailabilityCalendarResponse(BaseModel):
    agent_id: str
    current_status: str   # already .value'd to a plain string before being returned
    calendar: list[AvailabilityDay]
    summary: AvailabilitySummary
    monthly_summary: list[AvailabilityMonthlySummary]

# ─── Location trail (2026-08-18) ──────────────────────────────────────────
# Batch, not single-fix: a field phone loses signal constantly, so the client
# queues fixes locally and flushes the queue on reconnect. See
# services/location_service.py for the ingest rules.

class LocationPing(BaseModel):
    latitude: float
    longitude: float
    # Browser-reported horizontal accuracy in metres. Optional because a
    # device without it should still be able to report a position.
    accuracy_metres: float | None = None
    # Device clock at capture. The server records its own receipt time
    # separately; the gap between them is how long this fix sat queued.
    recorded_at: datetime
    source: str = "HEARTBEAT"
    battery_pct: int | None = None


class LocationBatchRequest(BaseModel):
    pings: list[LocationPing]


class LocationBatchResponse(BaseModel):
    accepted: int
    rejected: int
    last_recorded_at: str | None = None
