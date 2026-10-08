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

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.models.agent import Agent
from app.models.audit_log import AuditAction
from app.models.message import (
    ISSUE_STATUSES, EscalationIssue, IssueStatus, Message, MessageThread, SenderSide,
    ThreadRead, ThreadStatus, ThreadSubject,
)
from app.models.payment_reversal import PaymentReversalRequest
from app.models.user import User

_NOT_FOUND = "Conversation not found."
_PREVIEW = 140   # inbox last-message preview length


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
        self._mark_read(ctx, thread)      # opening the thread clears its unread for this user
        self.db.commit()
        return self._dump(thread)

    # ── open a general escalation (agency only): issue + thread + first message ─
    def open_escalation(self, ctx, title: str, body: str) -> dict:
        if not (title or "").strip():
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "An escalation needs a title.")
        if not (body or "").strip():
            raise AppException(422, ErrorCode.VALIDATION_ERROR, "An escalation needs a first message.")
        if self._side(ctx) is not SenderSide.AGENCY:    # the cap is agency-only; defend it here too
            raise AppException(403, ErrorCode.FORBIDDEN, "Only an agency can open an escalation.")
        issue = EscalationIssue(bank_id=ctx.bank_id, agency_id=ctx.agency_id, title=title.strip()[:200],
                                status=IssueStatus.OPEN.value, created_by_user_id=ctx.user_id)
        self.db.add(issue)
        self.db.flush()
        return self.post_message(ctx, ThreadSubject.ISSUE.value, issue.id, body)

    # ── change an escalation's status (owning agency or bank; audited) ─────────
    def change_status(self, ctx, issue_id: str, status: str) -> dict:
        if status not in ISSUE_STATUSES:
            raise AppException(422, ErrorCode.VALIDATION_ERROR, f"Unknown status: {status}.")
        issue = self.db.get(EscalationIssue, issue_id)
        if issue is None:
            raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
        self._require_tenant(ctx, issue.bank_id, issue.agency_id)
        previous, issue.status = issue.status, status
        stage_audit(self.db, action=AuditAction.ESCALATION_STATUS_CHANGED, user_id=ctx.user_id,
                    entity_type="EscalationIssue", entity_id=issue.id,
                    bank_id=issue.bank_id, agency_id=issue.agency_id,
                    details={"issue_id": issue.id, "from": previous, "to": status,
                             "changed_by_side": self._side(ctx).value})
        self.db.commit()
        thread = self._thread_for(ThreadSubject.ISSUE.value, issue.id)
        return self._dump(thread) if thread is not None else {
            "thread": None, "subject_type": ThreadSubject.ISSUE.value, "subject_id": issue.id, "messages": []}

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
            last = self._last_message(thread.id)
            if last is not None and last.sender_side != side.value:
                out.append(self._summary(thread, last))
        return out

    # ── inbox: every visible thread, newest-first, with unread/pending flags ───
    def list_inbox(self, ctx, *, pending_only: bool = False) -> list[dict]:
        side = self._side(ctx)
        threads = self._visible_threads(ctx).all()
        return self._build_inbox_rows(ctx, threads, side, pending_only=pending_only)

    def _last_message(self, thread_id: str) -> Message | None:
        return (self.db.query(Message).filter(Message.thread_id == thread_id)
                .order_by(Message.created_at.desc()).first())

    # ── agent axis: manager↔agent 1:1 threads ─────────────────────────────────
    def list_agent_threads(self, ctx) -> list[dict]:
        """An AGENT sees only their own thread; a manager/admin sees every
        AGENT_DIRECT thread for agents of their agency. Per-agent isolation is
        SERVICE-enforced (agency-coarse RLS; see _subject_tenant / §8.6)."""
        side = self._side(ctx)
        q = (self.db.query(MessageThread)
             .filter(MessageThread.subject_type == ThreadSubject.AGENT_DIRECT.value,
                     MessageThread.bank_id == str(ctx.bank_id)))
        if ctx.scope == "AGENT":
            q = q.filter(MessageThread.subject_id == str(self._caller_agent_id(ctx) or ""))
        elif ctx.scope == "AGENCY":
            q = q.filter(MessageThread.agency_id == str(ctx.agency_id))
        else:
            return []
        return self._build_inbox_rows(ctx, q.all(), side, pending_only=False)

    # ── inbox row-building, batched (perf round 2, 2026-10-08) ────────────────
    # Each of the 5 reads below used to happen ONCE PER THREAD inside the old
    # per-row _inbox_row/_title/_row_status/_unread (last message, message
    # count, read marker, and for ISSUE threads the SAME EscalationIssue
    # fetched twice over, for title and status separately) -- an inbox of N
    # threads cost 4-5N queries. Measured by QA as 834-941ms on /threads and
    # /agent-threads. One query per lookup for every thread in the page
    # instead, grouped in Python; every row's title/status/unread/pending
    # value is unchanged.
    def _last_messages_batch(self, thread_ids: list[str]) -> dict[str, Message]:
        if not thread_ids:
            return {}
        out: dict[str, Message] = {}
        for m in (self.db.query(Message).filter(Message.thread_id.in_(thread_ids))
                  .order_by(Message.created_at.desc())):
            out.setdefault(m.thread_id, m)   # first seen per thread = latest, DESC order
        return out

    def _message_counts_batch(self, thread_ids: list[str]) -> dict[str, int]:
        if not thread_ids:
            return {}
        return dict(self.db.query(Message.thread_id, func.count(Message.id))
                    .filter(Message.thread_id.in_(thread_ids))
                    .group_by(Message.thread_id).all())

    def _thread_reads_batch(self, ctx, thread_ids: list[str]) -> dict[str, ThreadRead]:
        if not thread_ids:
            return {}
        return {r.thread_id: r for r in
                self.db.query(ThreadRead).filter(ThreadRead.thread_id.in_(thread_ids),
                                                 ThreadRead.user_id == ctx.user_id)}

    def _titles_and_statuses_batch(self, threads: list[MessageThread]) -> tuple[dict[str, str], dict[str, str]]:
        """Same per-subject-type rules _title/_row_status used to apply one
        thread at a time (ISSUE: the issue's own title/status; REVERSAL: a
        short label; AGENT_DIRECT: the agent's user's name; everything else:
        the thread's own subject_type/status) -- just fed from two preloaded
        dicts instead of a query per thread."""
        issue_ids = [t.subject_id for t in threads if t.subject_type == ThreadSubject.ISSUE.value]
        agent_ids = [t.subject_id for t in threads if t.subject_type == ThreadSubject.AGENT_DIRECT.value]
        issues = ({i.id: i for i in self.db.query(EscalationIssue).filter(EscalationIssue.id.in_(issue_ids))}
                 if issue_ids else {})
        agents = ({a.id: a for a in self.db.query(Agent).filter(Agent.id.in_(agent_ids))}
                 if agent_ids else {})
        user_ids = [a.user_id for a in agents.values()]
        users = {u.id: u for u in self.db.query(User).filter(User.id.in_(user_ids))} if user_ids else {}

        titles: dict[str, str] = {}
        statuses: dict[str, str] = {}
        for t in threads:
            if t.subject_type == ThreadSubject.ISSUE.value:
                issue = issues.get(t.subject_id)
                titles[t.id] = issue.title if issue is not None else "Escalation"
                statuses[t.id] = issue.status if issue is not None else t.status
            elif t.subject_type == ThreadSubject.REVERSAL.value:
                titles[t.id] = f"Reversal {str(t.subject_id)[:8]}"
                statuses[t.id] = t.status
            elif t.subject_type == ThreadSubject.AGENT_DIRECT.value:
                agent = agents.get(t.subject_id)
                user = users.get(agent.user_id) if agent is not None else None
                titles[t.id] = user.full_name if user is not None else "Agent"
                statuses[t.id] = t.status
            else:
                titles[t.id] = t.subject_type.title()
                statuses[t.id] = t.status
        return titles, statuses

    def _build_inbox_rows(self, ctx, threads: list[MessageThread], side: SenderSide,
                          *, pending_only: bool) -> list[dict]:
        ids = [t.id for t in threads]
        last_by = self._last_messages_batch(ids)
        counts = self._message_counts_batch(ids)
        reads = self._thread_reads_batch(ctx, ids)
        titles, statuses = self._titles_and_statuses_batch(threads)

        rows = []
        for t in threads:
            last = last_by.get(t.id)
            pending = last is not None and t.status == ThreadStatus.OPEN.value and last.sender_side != side.value
            if pending_only and not pending:
                continue
            read = reads.get(t.id)
            unread = last is not None and (read is None or read.last_read_at < last.created_at)
            rows.append({
                "thread_id": t.id, "subject_type": t.subject_type, "subject_id": t.subject_id,
                "title": titles.get(t.id, t.subject_type.title()), "status": statuses.get(t.id, t.status),
                "counterparty": self._counterparty(t, side),
                "unread": unread, "pending": pending, "message_count": int(counts.get(t.id, 0) or 0),
                "last_message": ({"sender_side": last.sender_side, "preview": last.body[:_PREVIEW],
                                  "at": last.created_at.isoformat() if last.created_at else None}
                                 if last is not None else None),
                "_created": t.created_at.isoformat() if t.created_at else "",
            })
        # newest-first by last activity (a thread with no message yet sorts by its creation)
        rows.sort(key=lambda r: r["last_message"]["at"] if r["last_message"] else r["_created"], reverse=True)
        for r in rows:
            r.pop("_created", None)
        return rows

    # ── helpers ───────────────────────────────────────────────────────────────
    def _thread_for(self, subject_type: str, subject_id: str) -> MessageThread | None:
        return (self.db.query(MessageThread)
                .filter(MessageThread.subject_type == subject_type,
                        MessageThread.subject_id == subject_id).first())

    def _subject_tenant(self, ctx, subject_type: str, subject_id: str) -> tuple[str, str]:
        """The (bank_id, agency_id) the subject belongs to — and the caller must be
        able to see that subject, or 404. REVERSAL and ISSUE (general escalation)
        are wired; PLACEMENT is reserved (a thread cannot be opened on it yet)."""
        if subject_type == ThreadSubject.REVERSAL.value:
            rev = self.db.get(PaymentReversalRequest, subject_id)
            if rev is None:
                raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            self._require_tenant(ctx, rev.bank_id, rev.agency_id)
            return rev.bank_id, rev.agency_id
        if subject_type == ThreadSubject.ISSUE.value:
            issue = self.db.get(EscalationIssue, subject_id)
            if issue is None:
                raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            self._require_tenant(ctx, issue.bank_id, issue.agency_id)
            return issue.bank_id, issue.agency_id
        if subject_type == ThreadSubject.AGENT_DIRECT.value:
            # subject_id IS the agent. PER-AGENT scoping is SERVICE-ONLY: the
            # _AGENCY_OWNED RLS policy is agency-coarse (no current_agent_id()), so
            # after the tiq_app cutover an AGENT scope would read agency-mates'
            # threads via the DB unless that GUC + a tightened policy land first —
            # a CUTOVER-BLOCKING item in DATA-MODEL-V2 §8.6. Here the service is the
            # enforcer: an agent reaches only their own thread.
            agent = self.db.get(Agent, subject_id)
            if agent is None or str(ctx.bank_id) != str(agent.bank_id):
                raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            if ctx.scope == "AGENT":
                if str(self._caller_agent_id(ctx)) != str(agent.id):
                    raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            elif ctx.scope == "AGENCY":
                if str(ctx.agency_id) != str(agent.agency_id):
                    raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            else:                                   # bank / platform have no agent axis
                raise AppException(404, ErrorCode.NOT_FOUND, _NOT_FOUND)
            return agent.bank_id, agent.agency_id
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
        if ctx.scope == "AGENT":
            return SenderSide.AGENT
        raise AppException(403, ErrorCode.FORBIDDEN, "Messaging is for a bank, agency or agent user.")

    def _caller_agent_id(self, ctx) -> str | None:
        """The Agent.id of an AGENT-scoped caller (their own agent row), else None.
        None fails closed: an AGENT with no agent row matches no thread."""
        a = self.db.query(Agent).filter(Agent.user_id == ctx.user_id).first()
        return a.id if a is not None else None

    def _mark_read(self, ctx, thread: MessageThread) -> None:
        """Upsert the caller's read marker for this thread (the inbox `unread`)."""
        now = datetime.now(timezone.utc)
        read = (self.db.query(ThreadRead)
                .filter(ThreadRead.thread_id == thread.id, ThreadRead.user_id == ctx.user_id).first())
        if read is None:
            self.db.add(ThreadRead(bank_id=thread.bank_id, agency_id=thread.agency_id,
                                   thread_id=thread.id, user_id=ctx.user_id, last_read_at=now))
        else:
            read.last_read_at = now

    def _counterparty(self, thread: MessageThread, side: SenderSide) -> str:
        """The other side of THIS thread, by axis. Agent axis is AGENT↔AGENCY;
        every other axis is BANK↔AGENCY."""
        if thread.subject_type == ThreadSubject.AGENT_DIRECT.value:
            return SenderSide.AGENT.value if side is SenderSide.AGENCY else SenderSide.AGENCY.value
        return SenderSide.AGENCY.value if side is SenderSide.BANK else SenderSide.BANK.value

    def _dump(self, thread: MessageThread) -> dict:
        msgs = (self.db.execute(select(Message).where(Message.thread_id == thread.id)
                                .order_by(Message.created_at)).scalars().all())
        issue = None
        if thread.subject_type == ThreadSubject.ISSUE.value:
            row = self.db.get(EscalationIssue, thread.subject_id)
            issue = self._issue_dict(row) if row is not None else None
        return {"thread": self._thread_dict(thread), "issue": issue,
                "subject_type": thread.subject_type, "subject_id": thread.subject_id,
                "messages": [self._msg_dict(m) for m in msgs]}

    def _issue_dict(self, i: EscalationIssue) -> dict:
        return {"id": i.id, "title": i.title, "status": i.status, "bank_id": i.bank_id,
                "agency_id": i.agency_id, "created_by_user_id": i.created_by_user_id,
                "created_at": i.created_at.isoformat() if i.created_at else None}

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
