# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B02) — the shared column vocabulary of the v2 data model
#   (docs/DATA-MODEL-V2.md §2.2-2.3). One definition each, imported by every
#   model, so a type decision is made once:
#     - ids are native UUID, handled as `str` in Python (as_uuid=False): every
#       `Mapped[str]` annotation, JSON payload, dict key and `id in list`
#       membership test keeps working. as_uuid=True would make
#       `str_id in {uuid_obj}` silently False.
#     - money is NUMERIC(14,2) stored exactly, read as float (asdecimal=False):
#       Decimal + float raises TypeError and every money expression in the
#       codebase is float arithmetic today.
#     - JSON is JSONB on Postgres, plain JSON elsewhere (the pattern
#       repayment_snapshot.py already used).
# ────────────────────────────────────────────────────────────────────────────
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

__all__ = [
    "Base", "TimestampMixin", "CreatedAtMixin", "UUIDPrimaryKey",
    "UUIDType", "Money", "Rate", "JsonDoc", "new_id", "uuid_fk", "PUBLIC",
]

# Shared native enum types live in `public` (design §2.1).
PUBLIC = "public"

UUIDType = Uuid(as_uuid=False)
Money = Numeric(14, 2, asdecimal=False)
Rate = Numeric(6, 3, asdecimal=False)          # interest rates, commission %
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_fk(target: str, *, ondelete: str | None = None, nullable: bool = False,
            index: bool = False, use_alter: bool = False, **kw):
    """A UUID foreign-key column. NO ACTION is the default (design §2.8):
    business rows are never hard-deleted, and a cascade that silently removes
    money, evidence or who-approved-what is the failure the rule prevents.
    2026-09-24 (audit MED 7): this defaulted to RESTRICT, which refuses the
    same deletes but cannot be DEFERRED and is checked mid-statement; NO
    ACTION is checked at statement end (or commit, when deferrable)."""
    return mapped_column(UUIDType, ForeignKey(target, ondelete=ondelete, use_alter=use_alter),
                         nullable=nullable, index=index, **kw)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now(), nullable=False
    )


class UUIDPrimaryKey:
    # The server default (gen_random_uuid()) is added by the migration, not
    # here: SQLite create_all cannot express it.
    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=new_id)
