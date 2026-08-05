import enum
from sqlalchemy import String, Boolean, Enum as SAEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class UserRole(str, enum.Enum):
    FIELD_AGENT = "FIELD_AGENT"
    AGENCY_MANAGER = "AGENCY_MANAGER"
    AGENCY_ADMIN = "AGENCY_ADMIN"


class User(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(15), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, name="user_role_enum"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Device binding — agents are locked to registered device fingerprint
    registered_device_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Refresh token stored hashed for rotation
    hashed_refresh_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[str | None] = mapped_column(String(50), nullable=True)

    agent_profile: Mapped["Agent"] = relationship("Agent", foreign_keys="[Agent.user_id]", back_populates="user", uselist=False)  # type: ignore[name-defined]  # noqa: F821
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="user", lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User {self.email} [{self.role}]>"
