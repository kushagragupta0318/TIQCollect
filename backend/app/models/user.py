# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B04) — tenancy.users (docs/DATA-MODEL-V2.md §4.1).
#   - Five roles added: PLATFORM_ADMIN, BANK_ADMIN, BANK_ANALYST, BANK_TECHOPS,
#     SERVICE. What a role may DO lives in core/permissions.py (capabilities),
#     never in role-name checks — that is how AGENCY_ADMIN stays meaningful.
#   - bank_id / agency_id say whose user this is; ck_users_role_scope says
#     which combinations each role may carry.
#   - date_of_birth is a DATE and last_login_at / locked_until are instants
#     (they were strings; auth_service parsed locked_until with fromisoformat).
#   - hashed_refresh_token is gone: one slot per user meant a login on a second
#     device silently killed the first. Sessions are rows in user_sessions.
#   - registered_device_fingerprint moved to workforce.agent_devices.
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKeyConstraint, Index,
    SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import PUBLIC, Base, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


class UserRole(str, enum.Enum):
    FIELD_AGENT = "FIELD_AGENT"
    AGENCY_MANAGER = "AGENCY_MANAGER"
    AGENCY_ADMIN = "AGENCY_ADMIN"
    # 2026-09-24 — the bank side and the platform (plan §2.1).
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    BANK_ADMIN = "BANK_ADMIN"
    BANK_ANALYST = "BANK_ANALYST"
    BANK_TECHOPS = "BANK_TECHOPS"
    SERVICE = "SERVICE"


USER_ROLE_SQL = SAEnum(UserRole, name="user_role_enum", schema=PUBLIC, metadata=Base.metadata)

BANK_ROLES = frozenset({UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS})
AGENCY_ROLES = frozenset({UserRole.AGENCY_ADMIN, UserRole.AGENCY_MANAGER})


def tenant_scope(role: UserRole) -> str:
    """The RLS scope a principal acts in (DATA-MODEL-V2 §8.1). Anything not a
    platform or bank role is agency-scoped, so an unknown role never widens."""
    if role == UserRole.PLATFORM_ADMIN:
        return "PLATFORM"
    return "BANK" if role in BANK_ROLES else "AGENCY"


class User(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "users"

    bank_id: Mapped[str | None] = uuid_fk("tenancy.banks.id", nullable=True)
    agency_id: Mapped[str | None] = mapped_column(UUIDType)
    scope_region_id: Mapped[str | None] = mapped_column(UUIDType)   # BANK_ANALYST region limit

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(15), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(USER_ROLE_SQL, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # v2_0004 (2026-09-28): TEXT, was String(64) — the secret is stored
    # Fernet-encrypted (TOTP_ENC_KEY) and a Fernet token is longer than 64.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    # The last accepted 30-second TOTP step; replay refused by compare-and-swap.
    # NULL = no code accepted yet. Never defaulted.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_attempts: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    agent_profile: Mapped["Agent"] = relationship("Agent", foreign_keys="[Agent.user_id]", primaryjoin="User.id == Agent.user_id", back_populates="user", uselist=False)  # type: ignore[name-defined]  # noqa: F821
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="user", lazy="noload", foreign_keys="[AuditLog.user_id]")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint("email"),
        UniqueConstraint("phone"),
        UniqueConstraint("id", "bank_id"),
        UniqueConstraint("id", "agency_id"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["scope_region_id", "bank_id"], ["tenancy.regions.id", "tenancy.regions.bank_id"]),
        # Which tenant ids each role may carry (design §4.1).
        CheckConstraint(
            "(role = 'PLATFORM_ADMIN' AND bank_id IS NULL AND agency_id IS NULL)"
            " OR (role IN ('BANK_ADMIN', 'BANK_ANALYST', 'BANK_TECHOPS') AND bank_id IS NOT NULL AND agency_id IS NULL)"
            " OR (role IN ('AGENCY_ADMIN', 'AGENCY_MANAGER', 'FIELD_AGENT') AND bank_id IS NOT NULL AND agency_id IS NOT NULL)"
            " OR (role = 'SERVICE' AND bank_id IS NOT NULL)",
            name="role_scope",
        ),
        Index(None, "bank_id", "role", "is_active"),
        Index(None, "agency_id", "role", "is_active"),
        {"schema": "tenancy"},
    )

    @property
    def is_bank_user(self) -> bool:
        return self.role in BANK_ROLES

    @property
    def is_agency_user(self) -> bool:
        return self.role in AGENCY_ROLES

    def __repr__(self) -> str:
        return f"<User {self.email} [{self.role}]>"
