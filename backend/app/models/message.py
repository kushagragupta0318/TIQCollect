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

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKey, UUIDType, uuid_fk


class ThreadSubject(str, enum.Enum):
    REVERSAL = "REVERSAL"      # a payment_reversal_requests row (live)
    PLACEMENT = "PLACEMENT"    # a placement decision (reserved; wired later)
    ISSUE = "ISSUE"            # a general agency→bank escalation (collections.escalation_issues)
    AGENT_DIRECT = "AGENT_DIRECT"   # a manager↔agent 1:1 chat, subject_id = agent_id (one per agent)


class ThreadStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class IssueStatus(str, enum.Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class SenderSide(str, enum.Enum):
    BANK = "BANK"
    AGENCY = "AGENCY"
    AGENT = "AGENT"      # a field agent on the manager↔agent axis


SUBJECT_TYPES = tuple(s.value for s in ThreadSubject)
THREAD_STATUSES = tuple(s.value for s in ThreadStatus)
SENDER_SIDES = tuple(s.value for s in SenderSide)
ISSUE_STATUSES = tuple(s.value for s in IssueStatus)


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


class EscalationIssue(Base, UUIDPrimaryKey, TimestampMixin):
    """A general agency→bank escalation — "escalate any issue", not tied to a
    reversal or placement. The agency opens it (messaging.escalate); it is the
    subject of exactly one ISSUE thread (subject_id = this id). The bank or the
    owning agency can change its status; every change is audited."""
    __tablename__ = "escalation_issues"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), default=IssueStatus.OPEN.value, server_default=text("'OPEN'"), nullable=False)
    created_by_user_id: Mapped[str] = uuid_fk("tenancy.users.id")

    __table_args__ = (
        CheckConstraint(_check_in("status", ISSUE_STATUSES), name="status"),
        Index(None, "agency_id", "status"),
        Index(None, "bank_id", "status"),
        {"schema": "collections"},
    )


class ThreadRead(Base, UUIDPrimaryKey):
    """Per-user read marker for a thread: the inbox's `unread` flag. The service
    upserts the caller's row when they open the thread; unread = the thread's last
    message is newer than this. Carries the thread's tenant for _AGENCY_OWNED RLS."""
    __tablename__ = "thread_reads"
    __tenant_parents__ = (("thread_id", "MessageThread"),)

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    thread_id: Mapped[str] = uuid_fk("collections.message_threads.id", ondelete="CASCADE")
    user_id: Mapped[str] = uuid_fk("tenancy.users.id")
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One read marker per (thread, user): the service upserts on this.
        Index("uq_thread_read_per_user", "thread_id", "user_id", unique=True),
        {"schema": "collections"},
    )
