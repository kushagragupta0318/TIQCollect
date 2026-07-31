import enum
from sqlalchemy import String, Float, Integer, Boolean, Enum as SAEnum, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class DocumentType(str, enum.Enum):
    PAYMENT_RECEIPT = "PAYMENT_RECEIPT"
    SIGNED_ACKNOWLEDGEMENT = "SIGNED_ACKNOWLEDGEMENT"
    LOAN_CLOSURE_FORM = "LOAN_CLOSURE_FORM"
    CUSTOMER_ID_PROOF = "CUSTOMER_ID_PROOF"
    PREMISES_PHOTO = "PREMISES_PHOTO"
    SELFIE_CHECKIN = "SELFIE_CHECKIN"
    PTP_SIGNED_FORM = "PTP_SIGNED_FORM"
    OTHER = "OTHER"


class Document(Base, UUIDPrimaryKey, TimestampMixin):
    """All field documents captured by agents — geo-stamped and tamper-evident."""
    __tablename__ = "documents"

    visit_id: Mapped[str | None] = mapped_column(ForeignKey("visits.id"), nullable=True, index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)

    doc_type: Mapped[DocumentType] = mapped_column(SAEnum(DocumentType, name="doc_type_enum"), nullable=False)

    # MinIO storage
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Tamper-evidence
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Geo-stamp at capture time
    capture_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_timestamp: Mapped[str] = mapped_column(String(50), nullable=False)

    # Customer consent for photo
    customer_consent_obtained: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    visit: Mapped["Visit | None"] = relationship("Visit", back_populates="documents")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_doc_case_type", "case_id", "doc_type"),
    )
