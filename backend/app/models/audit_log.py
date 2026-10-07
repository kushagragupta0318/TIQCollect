import enum
from datetime import datetime
from sqlalchemy import String, Enum as SAEnum, Index, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import PUBLIC, Base, JsonDoc, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


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
    # 2026-09-24 (audit gates 2 and 3) — browser calling and device resets.
    # Declared only with their write sites (voice_outbound, reset_agent_device).
    VOICE_CALL_PLACED = "VOICE_CALL_PLACED"
    VOICE_CALL_REFUSED = "VOICE_CALL_REFUSED"
    DEVICE_RESET = "DEVICE_RESET"
    # v2_0004 (2026-09-28): identity — invites, passwords, sessions, MFA (P1 A06-A08, A16)
    USER_INVITED = "USER_INVITED"
    INVITE_ACCEPTED = "INVITE_ACCEPTED"
    INVITE_REVOKED = "INVITE_REVOKED"
    USER_CREATED = "USER_CREATED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET_ISSUED = "PASSWORD_RESET_ISSUED"
    PASSWORD_RESET = "PASSWORD_RESET"
    SESSION_REVOKED = "SESSION_REVOKED"
    MFA_ENABLED = "MFA_ENABLED"
    MFA_DISABLED = "MFA_DISABLED"
    MFA_FAILED = "MFA_FAILED"
    # v2_0004: agency lifecycle + placement — DECLARED for P2 (D01, placement),
    # written when those services exist. Until then they are declared-unwritten,
    # the state CLAUDE.md issue 3 counts; listed here so nobody reads them as wired.
    AGENCY_ONBOARDED = "AGENCY_ONBOARDED"
    AGENCY_ACTIVATED = "AGENCY_ACTIVATED"
    AGENCY_SUSPENDED = "AGENCY_SUSPENDED"
    AGENCY_OFFBOARDED = "AGENCY_OFFBOARDED"
    CONTRACT_CHANGED = "CONTRACT_CHANGED"
    PLACEMENT_CREATED = "PLACEMENT_CREATED"
    PLACEMENT_RECALLED = "PLACEMENT_RECALLED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    # v2_0006 (2026-09-28): an agent's fields edited without a status change (G02, ce)
    AGENT_UPDATED = "AGENT_UPDATED"
    # v2_0010 (2026-09-28, A09b audit): every device binding, first or re-bind
    DEVICE_BOUND = "DEVICE_BOUND"
    # v2_0011 (2026-09-28, D02): an agency document reviewed; the verifier is never the uploader
    DOCUMENT_VERIFIED = "DOCUMENT_VERIFIED"
    DOCUMENT_REJECTED = "DOCUMENT_REJECTED"
    # v2_0016 (2026-09-29, P3): the bank feed ended a placement (paid direct / settled -> RESOLVED,
    # written off -> RETURNED); a recall is PLACEMENT_RECALLED
    PLACEMENT_ENDED = "PLACEMENT_ENDED"
    # #2 payment reversal: the two-stage agency→bank void of a mistaken collection.
    # APPENDED at the end — audit_action_enum is a native Postgres enum and
    # ALTER TYPE ADD VALUE appends, so the Python order must match the DB's or
    # test_every_native_enum_holds_the_models_values_in_order fails.
    PAYMENT_REVERSAL_REQUESTED = "PAYMENT_REVERSAL_REQUESTED"
    PAYMENT_REVERSED = "PAYMENT_REVERSED"
    # bank↔agency messaging: one row per message sent (append-only, carries the
    # thread's bank_id/agency_id). APPENDED at the end — see the note above.
    MESSAGE_SENT = "MESSAGE_SENT"


class AuditLog(Base, UUIDPrimaryKey):
    """Immutable audit trail — no updates, no deletes. Required for RBI compliance."""
    __tablename__ = "audit_logs"
    # A13b S1a: a row carries its ACTOR's tenant (filled by tenancy_listener);
    # a row no user wrote passes its entity's tenant (core/audit.py). RLS's
    # WITH CHECK refuses a NULL-bank row from any principal but PLATFORM.
    __tenant_parents__ = (("user_id", "User"),)

    # Timestamps stored directly — no mixin (must be immutable)
    # 2026-09-24 (B11, lead-dev audit 3.11): no index=True here — it built a
    # second index identical to ix_audit_created_at below.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 2026-09-24 (B09): NO ACTION, was SET NULL (this said RESTRICT; corrected
    # 2026-09-24, see base.uuid_fk). Users are never deleted, and the
    # immutability trigger (design §7.4) would block a SET NULL cascade anyway.
    user_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    # Denormalised tenant, so the bank / agency audit views need no join and
    # system-written rows (user_id NULL) can still be scoped (known issue 4).
    bank_id: Mapped[str | None] = mapped_column(UUIDType)
    agency_id: Mapped[str | None] = mapped_column(UUIDType)
    action: Mapped[AuditAction] = mapped_column(SAEnum(AuditAction, name="audit_action_enum", schema=PUBLIC, metadata=Base.metadata), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Change detail
    details: Mapped[dict | None] = mapped_column(JsonDoc, nullable=True)
    old_values: Mapped[dict | None] = mapped_column(JsonDoc, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JsonDoc, nullable=True)

    success: Mapped[bool] = mapped_column(default=True, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User | None"] = relationship("User", back_populates="audit_logs")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_audit_user_action", "user_id", "action"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_created_at", "created_at"),
        Index(None, "bank_id", "created_at"),
        Index(None, "agency_id", "created_at"),
        {"schema": "audit"},
    )
