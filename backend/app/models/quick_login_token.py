# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — New file. Enforces single-use on quick-login link tokens: one
#   row per redeemed JWT `jti`, checked/inserted by
#   services/auth_service.quick_login() before a token is allowed to mint a
#   real session. Closes the "reusable for 90 days" half of the quick-login
#   exposure finding — see core/security.py and changelog.md for the other
#   half (drastically shorter expiry).
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class UsedQuickLoginToken(Base):
    """One row per redeemed quick-login JWT `jti` — enforces single-use.

    The JWT's own `jti` is the natural primary key; no separate id/timestamps
    mixin needed for a table that only ever gets inserted into and read by key.
    """
    __tablename__ = "used_quick_login_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
