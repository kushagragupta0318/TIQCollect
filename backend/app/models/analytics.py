# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B13) — NEW. The analytics schema's one TABLE: the refresh log
#   of the materialized views (design §6.2 "writes mv_refresh_log"). The views
#   themselves are migration-only DDL (v2_0007_analytics); they are not ORM
#   models, and autogenerate does not reflect views.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey

SCHEMA = "analytics"
REFRESH_STATUSES = ("OK", "FAILED", "SKIPPED")


class MvRefreshLog(Base, UUIDPrimaryKey):
    """One row per materialized-view refresh attempt."""
    __tablename__ = "mv_refresh_log"

    view_name: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('OK', 'FAILED', 'SKIPPED')", name="status"),
        Index(None, "view_name", "started_at"),
        {"schema": SCHEMA},
    )
