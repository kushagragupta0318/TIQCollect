"""Bank↔agency messaging: threads anchored to a shared approval item, and their
append-only messages (bank↔agency comms feature).

Visibility mirrors the `_AGENCY_OWNED` RLS policy: a thread is the caller's when
`ctx.bank_id` matches and the caller is BANK-scoped or its `agency_id` matches.
A subject the caller cannot see, or a thread in another tenant, reads as 404
(never 403 — the same not-found body, so one cannot probe other tenants' ids).

`sender_side` is DERIVED from the caller's scope, never taken from the client.
Cross-tenant is through l8's RequestContext (`ctx`), the sanctioned mechanism —
the same one the reversal bank stage uses. Isolation is SERVICE-enforced today
(RLS is dormant until the API connects as tiq_app); the policy backs it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.models.message import Message, MessageThread, SenderSide, ThreadStatus, ThreadSubject
from app.models.payment_reversal import PaymentReversalRequest

_NOT_FOUND = "Conversation not found."


class MessagingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── read: one subject's thread (empty if not started yet) ─────────────────
    def get_thread(self, ctx, subject_type: str, subject_id: str) -> dict:
        self._subject_tenant(ctx, subject_type, subject_id)   # 404 if the subject isn't the caller's
        thread = self._thread_for(subject_type, subject_id)
        if thread is None:
            return {"thread": None, "subject_type": subject_type, "subject_id": subject_id, "messages": []}
        self._require_visible(ctx, thread)
        return self._dump(thread)

    # ── send: lazy-create the thread, append the message ──────────────────────
    def post_message(self, ctx, subject_type: str, subject_id: str, body: str) -> dict:
        if not (body or "").strip():
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "A message needs a body.")
        bank_id, agency_id = self._subject_tenant(ctx, subject_type, subject_id)
        thread = self._thread_for(subject_type, subject_id)
        if thread is None:
            thread = MessageThread(bank_id=bank_id, agency_id=agency_id,
                                   subject_type=subject_type, subject_id=subject_id,
                                   status=ThreadStatus.OPEN.value)
            self.db.add(thread)
            self.db.flush()
        else:
            self._require_visible(ctx, thread)
        side = self._side(ctx)
        msg = Message(bank_id=thread.bank_id, agency_id=thread.agency_id, thread_id=thread.id,
                      sender_user_id=ctx.user_id, sender_side=side.value, body=body.strip())
        self.db.add(msg)
        self.db.flush()
        # One construction of an audit row (core/audit). stage_audit takes the
        # thread's tenant explicitly, so the row carries the thread's bank_id AND
        # agency_id — a bank user's message on an agency thread is still visible to
        # that agency's audit read (not left to be inferred from the actor).
        stage_audit(self.db, action=AuditAction.MESSAGE_SENT, user_id=ctx.user_id,
                    entity_type="MessageThread", entity_id=thread.id,
                    bank_id=thread.bank_id, agency_id=thread.agency_id,
                    details={"thread_id": thread.id, "subject_type": subject_type,
                             "subject_id": subject_id, "sender_side": side.value})
        self.db.commit()
        self.db.refresh(thread)
        return self._dump(thread)

    # ── pending: open threads whose last word came from the other side ────────
    def list_pending(self, ctx) -> list[dict]:
        side = self._side(ctx)
        out = []
        for thread in self._visible_threads(ctx).filter(MessageThread.status == ThreadStatus.OPEN.value):
            last = (self.db.query(Message).filter(Message.thread_id == thread.id)
                    .order_by(Message.created_at.desc()).first())
            if last is not None and last.sender_side != side.value:
                out.append(self._summary(thread, last))
        return out

    # ── helpers ───────────────────────────────────────────────────────────────
    def _thread_for(self, subject_type: str, subject_id: str) -> MessageThread | None:
        return (self.db.query(MessageThread)
                .filter(MessageThread.subject_type == subject_type,
                        MessageThread.subject_id == subject_id).first())

    def _subject_tenant(self, ctx, subject_type: str, subject_id: str) -> tuple[str, str]:
        """The (bank_id, agency_id) the subject belongs to — and the caller must be
        able to see that subject, or 404. Only REVERSAL is wired; PLACEMENT is
        reserved (a thread cannot be opened on it yet)."""
        if subject_type == ThreadSubject.REVERSAL.value:
            rev = self.db.get(PaymentReversalRequest, subject_id)
            if rev is None:
                raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            self._require_tenant(ctx, rev.bank_id, rev.agency_id)
            return rev.bank_id, rev.agency_id
        if subject_type == ThreadSubject.PLACEMENT.value:
            raise AppException(422, ErrorCode.VALIDATION_ERROR,
                               "Messaging on placements is not available yet.")
        raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown subject type: {subject_type}.")

    def _require_tenant(self, ctx, bank_id: str, agency_id: str) -> None:
        """The _AGENCY_OWNED rule, in the service: same bank, and BANK-scoped or
        the owning agency. Anything else is 404, not 403."""
        if str(getattr(ctx, "bank_id", None)) != str(bank_id):
            raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
        if ctx.scope != "BANK" and str(getattr(ctx, "agency_id", None)) != str(agency_id):
            raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)

    def _require_visible(self, ctx, thread: MessageThread) -> None:
        self._require_tenant(ctx, thread.bank_id, thread.agency_id)

    def _visible_threads(self, ctx):
        q = self.db.query(MessageThread).filter(MessageThread.bank_id == str(ctx.bank_id))
        if ctx.scope != "BANK":
            q = q.filter(MessageThread.agency_id == str(ctx.agency_id))
        return q

    def _side(self, ctx) -> SenderSide:
        """DERIVED from scope, never trusted from the client. Fail CLOSED on an
        unexpected scope: today the caps gate this to bank/agency roles, but a
        future grant (e.g. PLATFORM support, Q20) must never be silently recorded
        as the agency on an append-only money-dispute thread — raise instead."""
        if ctx.scope == "BANK":
            return SenderSide.BANK
        if ctx.scope == "AGENCY":
            return SenderSide.AGENCY
        raise AppException(403, ErrorCode.FORBIDDEN, "Messaging is for a bank or agency user.")

    def _dump(self, thread: MessageThread) -> dict:
        msgs = (self.db.execute(select(Message).where(Message.thread_id == thread.id)
                                .order_by(Message.created_at)).scalars().all())
        return {"thread": self._thread_dict(thread),
                "subject_type": thread.subject_type, "subject_id": thread.subject_id,
                "messages": [self._msg_dict(m) for m in msgs]}

    def _thread_dict(self, t: MessageThread) -> dict:
        return {"id": t.id, "subject_type": t.subject_type, "subject_id": t.subject_id,
                "status": t.status, "bank_id": t.bank_id, "agency_id": t.agency_id}

    def _msg_dict(self, m: Message) -> dict:
        return {"id": m.id, "sender_user_id": m.sender_user_id, "sender_side": m.sender_side,
                "body": m.body, "created_at": m.created_at.isoformat() if m.created_at else None}

    def _summary(self, t: MessageThread, last: Message) -> dict:
        return {"thread_id": t.id, "subject_type": t.subject_type, "subject_id": t.subject_id,
                "status": t.status, "last_sender_side": last.sender_side,
                "last_body": last.body, "last_at": last.created_at.isoformat() if last.created_at else None}
