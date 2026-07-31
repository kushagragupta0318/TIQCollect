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
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
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
from app.models.call_log import CallOutcome


class CheckInRequest(BaseModel):
    latitude: float
    longitude: float
    selfie_key: Optional[str] = None


class RecordVisitRequest(BaseModel):
    check_in_latitude: float
    check_in_longitude: float
    customer_met: bool
    outcome: VisitOutcome
    # Who was spoken to (required when customer_met=True)
    person_met: Optional[PersonMet] = None
    # Why the customer is defaulting (for payment/RTP/dispute outcomes)
    default_reason: Optional[DefaultReason] = None
    not_met_reason: Optional[NotMetReason] = None
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
    # Device that took all photos on this visit
    device_id: Optional[str] = None
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
    visit_id: Optional[str] = None           # link payment to the visit that triggered it
    upi_reference: Optional[str] = None
    cheque_number: Optional[str] = None
    cheque_date: Optional[str] = None        # ISO date, for post-dated cheques
    cheque_bank: Optional[str] = None
    bank_reference: Optional[str] = None     # UTR / NEFT reference
    receipt_photo_key: Optional[str] = None


class SetPTPRequest(BaseModel):
    committed_amount: float = Field(gt=0)
    committed_date: date
    customer_reason: Optional[str] = None
    agent_notes: Optional[str] = None
    follow_up_date: Optional[date] = None


class SOSRequest(BaseModel):
    latitude: float
    longitude: float


class HandoverRequest(BaseModel):
    notes: str
    return_to_pool: bool = True


class CustomerFlagRequest(BaseModel):
    is_hostile: Optional[bool] = None
    do_not_contact: Optional[bool] = None


class LogCallRequest(BaseModel):
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
    last_payment_date: Optional[str] = None
    last_payment_amount: float
    next_due_date: Optional[str] = None
    legal_status: str
    settlement_status: str


class CaseSummaryResponse(BaseModel):
    id: str
    case_number: str
    status: CaseStatus
    priority: CasePriority
    target_amount: float
    collected_amount: float
    allocation_date: Optional[str] = None
    visit_count: int
    is_escalated: bool
    agent_id: Optional[str] = None
    max_visits_allowed: int
    handover_notes: Optional[str] = None
    customer: CustomerSummary
    loan: LoanSummary
    collection_stage: str
    bank_ptp_date: Optional[str] = None
    bank_ptp_amount: Optional[float] = None
    bank_ptp_status: Optional[str] = None
    bank_agent_remarks: Optional[str] = None


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


class CaseDetailResponse(CaseSummaryResponse):
    visits: list[CaseVisitHistoryItem]
    photos: list[CasePhotoItem]
    payments: list[CaseDetailPaymentItem]
    ptps: list[CaseDetailPTPItem]


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
    cases_visited_today: int
    visited_today_ids: list[str]
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
    date_of_birth: Optional[str] = None
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


class AvailabilityDay(BaseModel):
    date: str
    day_of_week: str
    status: str            # "ON_DUTY" | "OFF_DUTY" — literal string built in the service, not an enum
    beat_status: Optional[str] = None
    cases: int


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
