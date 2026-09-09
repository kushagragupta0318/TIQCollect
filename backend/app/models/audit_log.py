import enum
from datetime import datetime
from sqlalchemy import String, Enum as SAEnum, ForeignKey, Index, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AuditAction(str, enum.Enum):
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    TOKEN_REFRESH = "TOKEN_REFRESH"
    CASE_ASSIGNED = "CASE_ASSIGNED"
    CASE_UPDATED = "CASE_UPDATED"
    VISIT_RECORDED = "VISIT_RECORDED"
    PAYMENT_SUBMITTED = "PAYMENT_SUBMITTED"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    PTP_SET = "PTP_SET"
    PTP_UPDATED = "PTP_UPDATED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    SOS_TRIGGERED = "SOS_TRIGGERED"
    SOS_RESOLVED = "SOS_RESOLVED"
    BEAT_GENERATED = "BEAT_GENERATED"
    BEAT_MODIFIED = "BEAT_MODIFIED"
    AGENT_STATUS_CHANGED = "AGENT_STATUS_CHANGED"
    CONTACT_HOUR_VIOLATION_ATTEMPT = "CONTACT_HOUR_VIOLATION_ATTEMPT"
    ROLE_VIOLATION_ATTEMPT = "ROLE_VIOLATION_ATTEMPT"
    DEVICE_MISMATCH = "DEVICE_MISMATCH"
    DATA_EXPORT = "DATA_EXPORT"
    # 2026-08-19 — a manager confirming or dismissing a visit anomaly.
    ANOMALY_REVIEWED = "ANOMALY_REVIEWED"
    # 2026-09-09 — the retraining lifecycle. A model reaching production is the
    # highest-consequence manual action in the system, so it gets an audit row
    # of its own rather than living only in the candidate's state history.
    MODEL_CANDIDATE_APPROVED = "MODEL_CANDIDATE_APPROVED"
    MODEL_CANDIDATE_REJECTED = "MODEL_CANDIDATE_REJECTED"
    MODEL_PROMOTED = "MODEL_PROMOTED"


class AuditLog(Base, UUIDPrimaryKey):
    """Immutable audit trail — no updates, no deletes. Required for RBI compliance."""
    __tablename__ = "audit_logs"

    # Timestamps stored directly — no mixin (must be immutable)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[AuditAction] = mapped_column(SAEnum(AuditAction, name="audit_action_enum"), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Change detail
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    old_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    success: Mapped[bool] = mapped_column(default=True, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User | None"] = relationship("User", back_populates="audit_logs")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_audit_user_action", "user_id", "action"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_created_at", "created_at"),
    )
