# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-17 — New file. Promises used to stay ACTIVE for ever.
#
#   Measured on the live book before this existed: 222 ACTIVE promises whose
#   committed date had already passed (27 inside a week, 195 between 7 and 30
#   days old), and 0 BROKEN rows written by the running application — every
#   one of the 109 BROKEN rows was seed data. `payment_service` moves a promise
#   to HONORED when a verified payment satisfies it, and nothing anywhere moved
#   one that was NOT paid. So the borrower who never paid and the borrower whose
#   date has not arrived yet looked identical to every reader of `PTP.status`:
#   the allocator's PTP-fatigue gate (three broken promises to one agent bars
#   that agent — it could never trip), `ptp_kept_ratio` (a live input of the
#   recovery_risk model: ACTIVE counts as not kept, so a promise partly paid
#   and never resolved read as a failure, and `ptp_broken_6m` /
#   `recent_ptp_status` read every dead promise as OPEN), the Promises card's kept rate, the
#   visit-priority and repayment scorecards, and the 09:00 reminder query, which
#   was still counting promises that died weeks ago.
#
#   This module is the ONE place a promise is resolved by the calendar. The
#   nightly task (00:05) and the one-off backfill both call `process_scope`;
#   neither carries a rule of its own.
#
#   The payment arithmetic is `verified_paid_against`, lifted out of
#   `payment_service._honor_active_ptps_paid_by_due_date` so there is one
#   definition of "money paid against this promise" — same case, same agent,
#   VERIFIED only, dated on or before a cut-off. payment_service still calls it
#   with the committed date; this module calls it with the committed date PLUS
#   the grace day, which is the whole point of a grace day: a payment made on
#   it counts.
#
# 2026-09-17 (later) — the sum gained a LOWER bound: the promise's origin.
#   As lifted, the sum had no lower bound, so money paid BEFORE a promise was
#   made could honour it. Measured on the 220 promises the first dry run
#   would have resolved: a Rs 3,675 promise made on 1 Sep read HONORED on the
#   strength of Rs 31,481 paid on 27 Aug, five days before anyone promised
#   anything; 15 of 220 outcomes rested on money that predated the promise,
#   and a strict variant showed NONE of the 220 received a rupee after its
#   taking visit ended. Nothing in the product ever meant that: the agent app
#   titles the promise taken with a part payment "PTP for Remaining Balance",
#   the seed dates an honoured promise's payment ON the promised day (pinned
#   in test_seed_ptp_payment), both simulators the models were validated
#   against credit only money paid on or after the promise was born, and the
#   runtime honouring path can only ever fire on a payment event AFTER the
#   promise exists — so the unbounded sum was an omission in a one-line sync
#   fix, never a decision. Approved 2026-09-17. The origin is the taking
#   visit's check-in (a payment recorded DURING that visit counts — same
#   doorstep), falling back to `created_at` only for a promise with no visit;
#   `created_at` is the row-insert time and on a seeded book means "when the
#   seed ran", which is why it is the fallback and not the rule.
# ───────────────────────────────────────────────────────────────────────────
"""Promise-to-pay lifecycle: resolve ACTIVE promises once their date has passed.

THE RULE, in calendar days (never elapsed hours):

    committed_date = D        the due date
    D + 1                     the grace day — still ACTIVE, still payable
    D + 2 (and later)         eligible: resolved at the next housekeeping run

so a promise is eligible when  committed_date <= effective_date - 2 days,
i.e. `committed_date + GRACE_DAYS < effective_date` with GRACE_DAYS = 1.

THE OUTCOME, from verified money only (PENDING_VERIFICATION, REJECTED and
REVERSED never count), on the same case by the same agent, dated on or after
the promise's ORIGIN (the taking visit's check-in, else the row's created_at)
and on or before the grace day:

    paid >= committed_amount   -> HONORED
    0 < paid < committed       -> PARTIALLY_HONORED
    paid == 0                  -> BROKEN

RESCHEDULED is never touched: it is a terminal label meaning "replaced by a
newer promise", and the newer promise is its own ACTIVE row judged on its own
committed_date. Nor is anything else that is not ACTIVE — HONORED, BROKEN,
PARTIALLY_HONORED and EXPIRED are terminal, which is what makes the job
idempotent: a second run over the same book finds nothing ACTIVE and eligible
and changes nothing.

Every transition writes one `AuditLog` row — `PTP_UPDATED`, `user_id=None`,
`details.actor = "SYSTEM"` — in the same transaction as the status change, the
same shape `payment_service` uses when a payment honours a promise. A manager
asking "who marked this broken?" gets "the calendar did, on this date, with
this much paid", never an agent's name.

TENANT SAFETY: `process_scope` refuses to run without an explicit set of agent
ids. Callers resolve a manager's agents and pass them; nothing here can reach
across managers, and nothing here can be called "for everyone" by accident.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus

logger = structlog.get_logger()

# One full calendar day after the committed date during which the promise is
# still open and a payment still honours it. Approved 2026-09-17.
GRACE_DAYS = 1

# What the audit row says caused the transition. One constant, so the backfill
# and the nightly run are distinguishable in the trail by `source`, never by
# a different reason string.
REASON_GRACE_EXPIRED = "GRACE_PERIOD_EXPIRED"
SOURCE_NIGHTLY = "ptp_lifecycle_housekeeping"
SOURCE_BACKFILL = "ptp_lifecycle_backfill"


def eligibility_cutoff(effective_date: date) -> date:
    """The latest committed_date that is eligible on `effective_date`.

    A promise due on the cutoff has had its due date and its full grace day.
    """
    return effective_date - timedelta(days=GRACE_DAYS + 1)


def is_eligible(committed_date: date, effective_date: date) -> bool:
    """Calendar-date test; the only place the grace rule is expressed."""
    return committed_date <= eligibility_cutoff(effective_date)


def ptp_origin(ptp: PTP) -> datetime:
    """When the promise was made: the taking visit's check-in, else created_at.

    The visit wins whenever one is linked. `created_at` is `server_default
    now()` — the moment the ROW was written — which on a seeded or replayed
    book is the seed's clock, not the borrower's; it is the fallback for a
    promise that has no visit at all, never an alternative to one.
    """
    visit = ptp.visit
    if visit is not None and visit.check_in_time is not None:
        return visit.check_in_time
    return ptp.created_at


def verified_paid_against(db: Session, ptp: PTP, through: date) -> float:
    """Verified money on the promise's case, by the promise's agent, dated on
    or after the promise's origin and on or before `through` (a calendar
    date: the upper bound compares the payment's DATE, so the time of day on
    the last day does not matter; the lower bound is a TIMESTAMP, so a
    payment recorded during the taking visit — at or after check-in —
    counts, and one recorded before it does not).

    THE ONE DEFINITION. `payment_service` honours a promise with it at the
    committed date; the lifecycle resolves a promise with it at the grace
    day. It is a case-level sum, not an apportionment: two promises on one
    case can both be credited with the same rupee, which is how the honouring
    path has always read it and is recorded rather than changed here.
    """
    total = (
        db.query(func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(
            Payment.case_id == ptp.case_id,
            Payment.agent_id == ptp.agent_id,
            Payment.status == PaymentStatus.VERIFIED,
            Payment.payment_date >= ptp_origin(ptp),
            func.date(Payment.payment_date) <= through,
        )
        .scalar()
        or 0.0
    )
    return float(total)


def resolve_status(committed_amount: float, paid: float) -> PTPStatus:
    """The outcome for an eligible promise given the verified money against it."""
    if paid >= float(committed_amount):
        return PTPStatus.HONORED
    if paid > 0.0:
        return PTPStatus.PARTIALLY_HONORED
    return PTPStatus.BROKEN


@dataclass
class Transition:
    ptp_id: str
    case_id: str
    agent_id: str
    previous_status: str
    new_status: str
    committed_date: str
    committed_amount: float
    paid_through_grace: float
    previous_actual_paid_amount: float
    origin: str                                  # when the promise was made (ISO)
    audit_log_id: str | None = None   # set only when applied
    processed_at: str | None = None   # set only when applied


@dataclass
class LifecycleSummary:
    effective_date: str
    cutoff: str
    dry_run: bool
    eligible: int = 0
    honored: int = 0
    partially_honored: int = 0
    broken: int = 0
    rescheduled_skipped: int = 0
    grace_period_skipped: int = 0     # ACTIVE and overdue, but inside the grace window
    failed: int = 0
    transitions: list[Transition] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "effective_date": self.effective_date, "cutoff": self.cutoff, "dry_run": self.dry_run,
            "eligible": self.eligible, "honored": self.honored,
            "partially_honored": self.partially_honored, "broken": self.broken,
            "rescheduled_skipped": self.rescheduled_skipped,
            "grace_period_skipped": self.grace_period_skipped, "failed": self.failed,
        }


def process_scope(
    db: Session,
    *,
    agent_ids: list[str],
    effective_date: date,
    source: str,
    dry_run: bool = False,
    processed_at: datetime | None = None,
) -> LifecycleSummary:
    """Resolve every eligible ACTIVE promise held by `agent_ids`.

    `agent_ids` is REQUIRED and an empty list processes nothing: the caller
    names the tenant, this function never guesses one. With `dry_run` the
    outcome of every eligible promise is computed and returned and nothing is
    written — the backfill's report is produced by exactly the code that then
    applies it. When applying, each promise is committed on its own with its
    audit row; a failure on one promise is counted, logged at ERROR, rolled
    back for that row only, and does not stop the rest.
    """
    if not agent_ids:
        return LifecycleSummary(effective_date=effective_date.isoformat(),
                                cutoff=eligibility_cutoff(effective_date).isoformat(), dry_run=dry_run)
    processed_at = processed_at or datetime.now(timezone.utc)
    cutoff = eligibility_cutoff(effective_date)
    summary = LifecycleSummary(effective_date=effective_date.isoformat(), cutoff=cutoff.isoformat(), dry_run=dry_run)

    # Counted for the report, never processed: RESCHEDULED rows that are past
    # their date (a newer promise replaced them) and ACTIVE rows that are past
    # their date but still inside the grace window.
    summary.rescheduled_skipped = int(
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(agent_ids), PTP.status == PTPStatus.RESCHEDULED, PTP.committed_date < effective_date)
        .scalar() or 0
    )
    summary.grace_period_skipped = int(
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(agent_ids), PTP.status == PTPStatus.ACTIVE,
                PTP.committed_date > cutoff, PTP.committed_date < effective_date)
        .scalar() or 0
    )

    eligible = (
        db.query(PTP)
        .filter(PTP.agent_id.in_(agent_ids), PTP.status == PTPStatus.ACTIVE, PTP.committed_date <= cutoff)
        .order_by(PTP.committed_date, PTP.id)
        .all()
    )
    summary.eligible = len(eligible)

    for ptp in eligible:
        try:
            paid = verified_paid_against(db, ptp, through=ptp.committed_date + timedelta(days=GRACE_DAYS))
            new_status = resolve_status(ptp.committed_amount, paid)
            t = Transition(
                ptp_id=ptp.id, case_id=ptp.case_id, agent_id=ptp.agent_id,
                previous_status=ptp.status.value, new_status=new_status.value,
                committed_date=ptp.committed_date.isoformat(),
                committed_amount=float(ptp.committed_amount), paid_through_grace=paid,
                previous_actual_paid_amount=float(ptp.actual_paid_amount or 0.0),
                origin=ptp_origin(ptp).isoformat(),
            )
            if not dry_run:
                audit_id = str(uuid.uuid4())
                ptp.status = new_status
                ptp.actual_paid_amount = paid
                db.add(AuditLog(
                    id=audit_id,
                    created_at=processed_at,
                    user_id=None,          # nobody did this; the calendar did
                    bank_id=ptp.bank_id, agency_id=ptp.agency_id,
                    action=AuditAction.PTP_UPDATED,
                    entity_type="PTP",
                    entity_id=ptp.id,
                    details={
                        "from": t.previous_status,
                        "to": t.new_status,
                        "reason": REASON_GRACE_EXPIRED,
                        "actor": "SYSTEM",
                        "source": source,
                        "effective_date": effective_date.isoformat(),
                        "grace_days": GRACE_DAYS,
                        "committed_date": t.committed_date,
                        "committed_amount": t.committed_amount,
                        "origin": t.origin,
                        "verified_paid_through_grace": paid,
                        "processed_at": processed_at.isoformat(),
                    },
                    success=True,
                ))
                # One commit per promise: the status and its audit row land
                # together or not at all, and a failure on the next row cannot
                # roll this one back.
                db.commit()
                t.audit_log_id = audit_id
                t.processed_at = processed_at.isoformat()
            summary.transitions.append(t)
            if new_status is PTPStatus.HONORED:
                summary.honored += 1
            elif new_status is PTPStatus.PARTIALLY_HONORED:
                summary.partially_honored += 1
            else:
                summary.broken += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not hide the batch
            summary.failed += 1
            if not dry_run:
                db.rollback()   # discards only this promise's pending change
            logger.error("ptp_lifecycle.transition_failed", ptp_id=ptp.id,
                         error_type=type(exc).__name__, error=str(exc), exc_info=True)

    return summary
