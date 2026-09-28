# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B04, for A05-A07) — New file: sessions, invites and password
#   reset tokens (docs/DATA-MODEL-V2.md §4.1).
#
#   user_sessions replaces users.hashed_refresh_token. That column was ONE
#   slot per user, so a login on a second device silently invalidated the
#   first device's refresh token, and "token reuse detected" nulled the slot
#   and logged the user out everywhere. A session is now a row per device;
#   reuse revokes that session only.
#
#   Tokens are stored as sha256, not bcrypt: they are high-entropy signed
#   values and must be looked up by an index, which bcrypt's per-hash salt
#   makes impossible.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text as text  # noqa: F401
from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKey, UUIDType, uuid_fk
from app.models.user import USER_ROLE_SQL, UserRole

SCHEMA = "tenancy"

SESSION_REVOKE_REASONS = (
    "LOGOUT", "ADMIN_REVOKED", "REUSE_DETECTED", "PASSWORD_CHANGED", "USER_DEACTIVATED", "EXPIRED",
    "DEVICE_RESET",   # 2026-09-24 (audit gate 3): a manager unbound the agent's phone
    "MFA_CHANGED",    # v2_0005 (2026-09-28, A08): MFA enrolled; sessions opened without it end
)
# The CHECK below is migrated by v2_0005 (the latest frozen list);
# tests/test_alembic_v2_baseline pins the two equal.


class UserSession(Base, UUIDPrimaryKey, CreatedAtMixin):
    """One refresh token per device. `id` travels in the JWT as `sid`."""
    __tablename__ = "user_sessions"

    user_id: Mapped[str] = uuid_fk("tenancy.users.id", ondelete="CASCADE")
    bank_id: Mapped[str | None] = mapped_column(UUIDType)
    agency_id: Mapped[str | None] = mapped_column(UUIDType)
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(100))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_created: Mapped[str | None] = mapped_column(String(45))
    ip_last: Mapped[str | None] = mapped_column(String(45))
    refresh_token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    refresh_jti: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(20))
    revoked_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    __table_args__ = (
        UniqueConstraint("refresh_token_sha256"),
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ("
            + ", ".join(repr(r) for r in SESSION_REVOKE_REASONS) + ")",
            name="revoked_reason",
        ),
        Index("ix_user_sessions_live", "user_id",
              postgresql_where=text("revoked_at IS NULL"), sqlite_where=text("revoked_at IS NULL")),
        Index(None, "bank_id", "last_used_at"),
        {"schema": SCHEMA},
    )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


INVITE_PURPOSES = ("USER_ONBOARD", "AGENCY_MASTER_LOGIN")
INVITE_CHANNELS = ("EMAIL", "SMS", "LINK")


class UserInvite(Base, UUIDPrimaryKey, CreatedAtMixin):
    """Single-use, hashed at rest, 72 hours. The invitee sets their own
    password, so nobody ever sees or stores another person's password."""
    __tablename__ = "user_invites"

    bank_id: Mapped[str | None] = mapped_column(UUIDType)
    agency_id: Mapped[str | None] = mapped_column(UUIDType)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(15))
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(USER_ROLE_SQL, nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_channel: Mapped[str] = mapped_column(String(8), nullable=False, default="LINK")
    invited_by: Mapped[str] = uuid_fk("tenancy.users.id")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    __table_args__ = (
        UniqueConstraint("token_sha256"),
        UniqueConstraint("accepted_user_id"),
        CheckConstraint("purpose IN ('USER_ONBOARD', 'AGENCY_MASTER_LOGIN')", name="purpose"),
        CheckConstraint("delivery_channel IN ('EMAIL', 'SMS', 'LINK')", name="channel"),
        # One open invite per address.
        Index("uq_user_invites_open_email", "email", unique=True,
              postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
              sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL")),
        Index(None, "agency_id", "created_at"),
        Index(None, "bank_id", "created_at"),
        {"schema": SCHEMA},
    )


RESET_KINDS = ("ADMIN_RESET", "SELF_SERVICE", "FIRST_LOGIN")


class PasswordResetToken(Base, UUIDPrimaryKey, CreatedAtMixin):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = uuid_fk("tenancy.users.id", ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    otp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(String(45))

    __table_args__ = (
        UniqueConstraint("token_sha256"),
        CheckConstraint("kind IN ('ADMIN_RESET', 'SELF_SERVICE', 'FIRST_LOGIN')", name="kind"),
        Index("ix_password_reset_tokens_open", "user_id",
              postgresql_where=text("used_at IS NULL"), sqlite_where=text("used_at IS NULL")),
        {"schema": SCHEMA},
    )
