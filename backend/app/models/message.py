"""Bank↔agency messaging: a thread anchored to an approval item, and its
append-only messages (bank↔agency comms feature).

A thread hangs off a subject the two tenants already share — a reversal now, a
placement later — so the conversation sits next to the decision it is about, not
in a free-for-all inbox. One thread per subject (UNIQUE(subject_type, subject_id));
the service lazy-creates it on the first message. Both the bank and the owning
agency can read and post; `sender_side` is DERIVED from the poster's scope, never
taken from the client.

Both tables live in the `collections` schema (no separate schema: messaging has no
isolation need that collections + its agency-owned RLS doesn't already serve, and a
new domain schema would touch the baseline search_path — coordinator call, 73).

Tenancy: both tables are `_AGENCY_OWNED` (v2_0012's two-party template) — a bank
sees its agencies' threads, an agency sees only its own. The thread carries
bank_id/agency_id (the service sets them from the subject); a message inherits
them from its thread (`__tenant_parents__`). Isolation is SERVICE-enforced today
(RLS is dormant until the API connects as tiq_app); the policy backs it.

subject_type / status / sender_side are String + CheckConstraint, not native pg
enums: post-baseline native enums are untracked by the enum-drift test (same
convention as strategy.simulation_runs and the reversal status).
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


class ThreadSubject(str, enum.Enum):
    REVERSAL = "REVERSAL"      # a payment_reversal_requests row (live)
    PLACEMENT = "PLACEMENT"    # a placement decision (reserved; wired later)


class ThreadStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class SenderSide(str, enum.Enum):
    BANK = "BANK"
    AGENCY = "AGENCY"


SUBJECT_TYPES = tuple(s.value for s in ThreadSubject)
THREAD_STATUSES = tuple(s.value for s in ThreadStatus)
SENDER_SIDES = tuple(s.value for s in SenderSide)


def _check_in(col: str, values: tuple[str, ...]) -> str:
    return col + " IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class MessageThread(Base, UUIDPrimaryKey, TimestampMixin):
    """One conversation about one shared subject. At most one per subject."""
    __tablename__ = "message_threads"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    status: Mapped[str] = mapped_column(
        String(8), default=ThreadStatus.OPEN.value, server_default=text("'OPEN'"), nullable=False)

    __table_args__ = (
        CheckConstraint(_check_in("subject_type", SUBJECT_TYPES), name="subject_type"),
        CheckConstraint(_check_in("status", THREAD_STATUSES), name="status"),
        # One thread per subject: the service lazy-creates on this.
        Index("uq_thread_per_subject", "subject_type", "subject_id", unique=True),
        Index(None, "bank_id", "status"),
        Index(None, "agency_id", "status"),
        {"schema": "collections"},
    )


class Message(Base, UUIDPrimaryKey, CreatedAtMixin):
    """One message in a thread. Append-only: created, never edited or deleted."""
    __tablename__ = "messages"
    # Tenant inherited from the thread (the service sets the thread's tenant from
    # the subject; messages never carry a tenant the client chose).
    __tenant_parents__ = (("thread_id", "MessageThread"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    thread_id: Mapped[str] = uuid_fk("collections.message_threads.id", ondelete="CASCADE")
    sender_user_id: Mapped[str] = uuid_fk("tenancy.users.id")
    sender_side: Mapped[str] = mapped_column(String(6), nullable=False)   # DERIVED from scope, not trusted
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(_check_in("sender_side", SENDER_SIDES), name="sender_side"),
        Index(None, "thread_id", "created_at"),
        {"schema": "collections"},
    )
