from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum as SAEnum, Float, ForeignKey, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class PhotoType(str, enum.Enum):
    AGENT_SELFIE  = "AGENT_SELFIE"
    BORROWER      = "BORROWER"
    PREMISES      = "PREMISES"
    VEHICLE_ASSET = "VEHICLE_ASSET"
    ID_PROOF      = "ID_PROOF"
    BANK_STMT     = "BANK_STMT"
    INCOME_PROOF  = "INCOME_PROOF"
    MEDICAL_SUPPORT = "MEDICAL_SUPPORT"
    RECEIPT       = "RECEIPT"
    OTHER         = "OTHER"


class PhotoStatus(str, enum.Enum):
    UPLOADED  = "UPLOADED"
    VERIFIED  = "VERIFIED"
    REJECTED  = "REJECTED"


class CasePhoto(Base):
    __tablename__ = "case_photos"

    id         = Column(String(36), primary_key=True)
    case_id    = Column(String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id   = Column(String(36), ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_id   = Column(String(36), ForeignKey("agents.id"), nullable=False)

    photo_type   = Column(SAEnum(PhotoType), nullable=False)
    storage_key  = Column(String(512), nullable=False, unique=True)

    # GPS metadata captured at photo-take time by the device
    latitude        = Column(Float, nullable=True)
    longitude       = Column(Float, nullable=True)
    accuracy_metres = Column(Float, nullable=True)
    altitude_metres = Column(Float, nullable=True)
    captured_at     = Column(DateTime(timezone=True), nullable=True)

    # Device / integrity
    device_id   = Column(String(255), nullable=True)
    sha256_hash = Column(String(64),  nullable=True)
    status      = Column(SAEnum(PhotoStatus), default=PhotoStatus.UPLOADED, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
