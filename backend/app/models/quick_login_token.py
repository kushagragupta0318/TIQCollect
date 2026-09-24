# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — New file. Enforces single-use on quick-login link tokens: one
#   row per redeemed JWT `jti`, checked/inserted by
#   services/auth_service.quick_login() before a token is allowed to mint a
#   real session. Closes the "reusable for 90 days" half of the quick-login
#   exposure finding — see core/security.py and changelog.md for the other
#   half (drastically shorter expiry).
# 2026-09-24 (B04) — moved to the tenancy schema; records who redeemed it and
#   when the token would have expired anyway, so a sweep can prune.
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.models.base import uuid_fk


class UsedQuickLoginToken(Base):
    """One row per redeemed quick-login JWT `jti` — enforces single-use.

    The JWT's own `jti` is the natural primary key; no separate id/timestamps
    mixin needed for a table that only ever gets inserted into and read by key.
    """
    __tablename__ = "used_quick_login_tokens"
    __table_args__ = {"schema": "tenancy"}

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
