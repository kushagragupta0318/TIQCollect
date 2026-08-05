# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 — Added notes/consent_given/signature_key (bottom of Visit).
#   RecordVisitRequest already accepted all three over the wire but nothing
#   persisted them: notes had no schema field at all (Pydantic silently drops
#   unknown fields), and consent_given/signature_key had a schema field but
#   no column and were never read in visit_service.py. Full detail + why:
#   /changelog.md
# ───────────────────────────────────────────────────────────────────────────
import enum
from datetime import datetime
from sqlalchemy import String, Float, Boolean, Enum as SAEnum, ForeignKey, Index, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class VisitOutcome(str, enum.Enum):
    # Payment outcomes
    PAID_FULL       = "PAID_FULL"         # Full target amount collected
    PART_PAID       = "PART_PAID"         # Partial amount collected, no PTP for rest
    PTP             = "PTP"               # Promise to Pay — no money today
    PART_PAID_PTP   = "PART_PAID_PTP"    # Partial payment + PTP for remainder
    BROKEN_PTP      = "BROKEN_PTP"       # Customer had a PTP but didn't honour it
    # Non-payment outcomes (customer met)
    RTP             = "RTP"               # Refuse to Pay — met but refuses
    DISPUTE         = "DISPUTE"           # Customer disputes loan existence / amount
    # Customer not available outcomes
    NOT_AVAILABLE   = "NOT_AVAILABLE"     # Customer not present at address
    ADDRESS_ISSUE   = "ADDRESS_ISSUE"     # Wrong address / customer has shifted
    DECEASED        = "DECEASED"          # Customer deceased
    # Admin
    REVISIT         = "REVISIT"           # Incomplete visit, revisit needed


class PersonMet(str, enum.Enum):
    BORROWER     = "BORROWER"
    CO_BORROWER  = "CO_BORROWER"
    SPOUSE       = "SPOUSE"
    PARENT       = "PARENT"
    SIBLING      = "SIBLING"
    CHILD        = "CHILD"
    RELATIVE     = "RELATIVE"
    EMPLOYER     = "EMPLOYER"
    NEIGHBOR     = "NEIGHBOR"
    SECURITY     = "SECURITY"
    OTHER        = "OTHER"


class DefaultReason(str, enum.Enum):
    JOB_LOSS        = "JOB_LOSS"
    SALARY_CUT      = "SALARY_CUT"
    BUSINESS_FAILURE = "BUSINESS_FAILURE"
    MEDICAL         = "MEDICAL"
    DEATH_IN_FAMILY = "DEATH_IN_FAMILY"
    MARITAL_DISPUTE = "MARITAL_DISPUTE"
    ALREADY_PAID    = "ALREADY_PAID"      # Claims they already paid bank directly
    AMOUNT_DISPUTED = "AMOUNT_DISPUTED"   # Disputes the outstanding figure
    FRAUD_CLAIM     = "FRAUD_CLAIM"       # Denies ever taking the loan
    OVER_LEVERAGED  = "OVER_LEVERAGED"    # Too many loans
    OTHER           = "OTHER"


class NotMetReason(str, enum.Enum):
    PREMISES_LOCKED    = "PREMISES_LOCKED"
    CUSTOMER_AWAY      = "CUSTOMER_AWAY"
    WRONG_ADDRESS      = "WRONG_ADDRESS"
    CUSTOMER_ABSCONDED = "CUSTOMER_ABSCONDED"
    NEIGHBOR_MET       = "NEIGHBOR_MET"
    OTHER              = "OTHER"


class Visit(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "visits"

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)

    # Geo-verification — visit must be within 100m of customer address
    check_in_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    check_in_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    check_in_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    distance_from_customer_metres: Mapped[float] = mapped_column(Float, nullable=False)
    geo_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Contact compliance
    within_contact_hours: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Outcome
    customer_met: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outcome: Mapped[VisitOutcome] = mapped_column(SAEnum(VisitOutcome, name="visit_outcome_enum"), nullable=False)
    person_met: Mapped[PersonMet | None] = mapped_column(SAEnum(PersonMet, name="person_met_enum"), nullable=True)
    default_reason: Mapped[DefaultReason | None] = mapped_column(SAEnum(DefaultReason, name="default_reason_enum"), nullable=True)
    not_met_reason: Mapped[NotMetReason | None] = mapped_column(SAEnum(NotMetReason, name="not_met_reason_enum"), nullable=True)
    visit_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Selfie proof (agent check-in selfie — legacy, kept for backward compat)
    selfie_photo_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Geo-tagged photos captured at the visit location
    agent_photo_key: Mapped[str | None] = mapped_column(String(500), nullable=True)      # agent selfie at premises
    borrower_photo_key: Mapped[str | None] = mapped_column(String(500), nullable=True)   # borrower / person met
    object_photo_key: Mapped[str | None] = mapped_column(String(500), nullable=True)     # vehicle, property, asset (optional)
    # Full GPS metadata at the moment each photo was taken (device-reported)
    agent_photo_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_photo_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_photo_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_photo_altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_photo_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    borrower_photo_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrower_photo_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrower_photo_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrower_photo_altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrower_photo_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_photo_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    object_photo_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    object_photo_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    object_photo_altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    object_photo_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SHA-256 of raw JPEG bytes — used for tamper detection / integrity verification
    agent_photo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    borrower_photo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_photo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Device that captured the photos this visit (Android/iOS device ID)
    device_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Call / visit recordings stored in object storage (S3/GCS key or URL)
    agent_recording_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    borrower_recording_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Whisper transcripts (Hindi → English or English → English)
    agent_recording_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    borrower_recording_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Field investigation (Phase 1D)
    property_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    occupancy_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    vehicle_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    business_running: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # AI-generated visit summary (100–150 words, generated post-visit for manager/command-centre)
    ai_visit_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Agent's free-text field observations (was accepted by RecordVisitRequest
    # but had no column and was silently dropped — see changelog.md 2026-07-14)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Customer consent for the visit/data collection, and their signature image key
    consent_given: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    signature_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    case: Mapped["Case"] = relationship("Case", back_populates="visits")  # type: ignore[name-defined]  # noqa: F821
    agent: Mapped["Agent"] = relationship("Agent", back_populates="visits")  # type: ignore[name-defined]  # noqa: F821
    payment: Mapped["Payment | None"] = relationship("Payment", back_populates="visit", uselist=False, lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    ptp: Mapped["PTP | None"] = relationship("PTP", back_populates="visit", uselist=False, lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_visit_agent_date", "agent_id", "check_in_time"),
        Index("ix_visit_case", "case_id", "check_in_time"),
        Index("ix_visit_outcome", "outcome"),
    )
