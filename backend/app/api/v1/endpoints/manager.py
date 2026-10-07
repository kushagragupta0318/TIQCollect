# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — New POST /agents/{agent_id}/sos/acknowledge. The dashboard's
#   SOS "Respond"/"Dispatch" buttons (ManagerOverviewPage.tsx,
#   ManagerAgentsPage.tsx) previously called nothing — just a toast claiming
#   "emergency services notified". This is the real action behind them: an
#   SMS/WhatsApp to the agent confirming their manager has seen the alert.
# 2026-07-15 (later) — get_case_detail()'s per-visit dict gained `notes`/
#   `consent_given` (line ~453) — the agent-side equivalent
#   (endpoints/agent.py) already had both; the manager view was missing them
#   entirely, so a manager could never see what the agent wrote or whether
#   consent was captured. While here: deduped `ai_visit_note`/
#   `agent_recording_transcript`/`borrower_recording_transcript`, which were
#   each being set twice in this same dict (harmless but dead — same bug
#   already fixed in agent.py's version of this dict on 2026-07-14).
#   Full detail: /changelog.md.
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
# Aliased: `Case` in this module is the SQLAlchemy model for a collections
# case, so importing the SQL CASE construct under its own name would read as
# the model with a typo.
from sqlalchemy import case as sa_case
from sqlalchemy import false as sa_false
from sqlalchemy.orm import joinedload

from app.core.dependencies import DbSession, ManagerOnly
from app.core.config import settings
from app.core.ids import UUIDPath, UUIDQuery, UUIDQueryRequired, UUIDStr
from app.core import llm as _llm
from app.ml import eligibility as _elig
from app.models.agent import Agent, AgentStatus, AgentPerformance, month_start
# Module level, not a local import: the audit-log endpoints below share a
# scoping helper, and a per-function import would make it easy for one of the
# two callers to drift onto a different model reference.
from app.models.audit_log import AuditAction, AuditLog
from app.core import storage
from app.models.beat import Beat
from app.models.case import Case, CaseStatus
from app.models.loan import DPDBucket, Loan
from app.models.repayment_snapshot import RepaymentSnapshot
from app.ml.recovery_scorecard import (
    expected_recoverable_amount as _recovery_expected_amount,
)
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.core.permissions import require_perm
from app.core.csv_safe import csv_row
from app.models.user import User
from app.models.visit import Visit, VisitOutcome
from app.services.brand import brand_for
from app.services.notification_service import NotificationService
from app.services.leave_service import agent_ids_on_leave, effective_status, leave_today

router = APIRouter(prefix="/manager", tags=["manager"])


def _require_own_agent(db, current_user, agent_id: str) -> Agent:
    """The agent, if this manager owns them. 404 otherwise — never 403.

    404 rather than 403 deliberately: a 403 confirms the id exists, which
    turns this into an enumeration oracle for another agency's roster.

    Tenant scoping in this router is hand-repeated at every call site, and
    that is precisely why two endpoints shipped without it. New scoped
    endpoints should call this; the existing ones can migrate to it, but
    that is a separate change from closing the leaks.
    """
    agent = (
        db.query(Agent)
        .filter(Agent.id == agent_id, Agent.manager_user_id == current_user.id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent



def _effective_today(agent_ids: list[str], db) -> tuple[datetime, datetime, date]:
    """Return (start_of_day, end_of_day, eff_date) scoped to the most recent
    beat date on or before today across the given agents. Falls back to date.today()
    when no beats exist yet (fresh install without a seed).

    A DISPLAY convenience only, same as endpoints/agent._effective_day: it lets
    money/case/visit figures agree on which day's beat they describe when the
    latest beat is not literally today (a paused demo book, a weekend, a missed
    nightly run). Is-this-agent-on-leave never reads it — leave is judged
    against the real IST calendar day, services/leave_service.leave_today(),
    same as services/scope.access_day(). Mixing the two used to make on-duty/
    on-leave counts silently wrong whenever the effective day fell behind
    (coordinator audit, 2026-09-30): GET /dashboard, GET /agents,
    GET /agents/performance, GET /analytics and GET /ai/briefing all computed
    "on leave" from eff_date instead of leave_today() and have been fixed;
    their OTHER uses of eff_date (which day's/month's money and cases to sum)
    are correct as they were and are untouched.
    """
    row = (
        db.query(func.max(Beat.beat_date))
        .filter(Beat.agent_id.in_(agent_ids), Beat.beat_date <= date.today())
        .scalar()
    )
    eff_date: date = row if row else date.today()
    start = datetime.combine(min(eff_date, date.today()), datetime.min.time()).replace(tzinfo=timezone.utc)
    end   = datetime.combine(max(eff_date, date.today()), datetime.max.time()).replace(tzinfo=timezone.utc)
    return start, end, eff_date


# ── Recovery potential (2026-08-24) ──────────────────────────────────────────
# Read from the SNAPSHOT, never from Loan.recovery_potential.
#
# That is not a detail. RECOVERY_WRITE_LABEL is off by default, so the Loan
# column still holds whatever it held before this feature shipped — for a seeded
# database, the output of random.choices(). Reading it here would show managers
# the old noise while the computed label sat in a table nobody looked at, which
# is the exact opposite of the point of shipping this.
#
# The snapshot is written on every scoring run regardless of the gate, so this is
# always the current computed answer.


def _latest_recovery_by_loan(db, loan_ids: list[str]) -> dict[str, dict]:
    """The most recent recovery label for each of these loans.

    One query for the page, not one per row. Returns {} for an empty input rather
    than issuing a query with an empty IN clause.
    """
    if not loan_ids:
        return {}
    newest = (
        db.query(RepaymentSnapshot.loan_id,
                 func.max(RepaymentSnapshot.as_of_date).label("as_of_date"))
        .filter(RepaymentSnapshot.loan_id.in_(loan_ids),
                RepaymentSnapshot.recovery_rate_90.is_not(None))
        .group_by(RepaymentSnapshot.loan_id)
        .subquery()
    )
    rows = (
        db.query(RepaymentSnapshot)
        .join(newest, and_(RepaymentSnapshot.loan_id == newest.c.loan_id,
                           RepaymentSnapshot.as_of_date == newest.c.as_of_date))
        .all()
    )
    return {
        r.loan_id: {
            "recovery_potential": r.recovery_potential,
            "rate_30": r.recovery_rate_30,
            "rate_60": r.recovery_rate_60,
            "rate_90": r.recovery_rate_90,
            "label_horizon_days": settings.RECOVERY_LABEL_HORIZON_DAYS,
            "speed_index": r.recovery_speed_index,
            "evidence_coverage": r.recovery_evidence_coverage,
            "model_version": r.recovery_model_version,
            "source": r.recovery_source,
            # A hand-weighted scorecard, never a trained model. Sent so the UI
            # cannot present it as one; there is no accuracy figure to send
            # because none exists.
            "is_modelled": False,
            "as_of": r.as_of_date.isoformat() if r.as_of_date else None,
        }
        for r in rows
    }


def _loan_ids_with_recovery(db, band: str) -> list[str]:
    """Loans whose LATEST label is this band — for the manager case filter."""
    newest = (
        db.query(RepaymentSnapshot.loan_id,
                 func.max(RepaymentSnapshot.as_of_date).label("as_of_date"))
        .filter(RepaymentSnapshot.recovery_rate_90.is_not(None))
        .group_by(RepaymentSnapshot.loan_id)
        .subquery()
    )
    return [
        r[0] for r in
        db.query(RepaymentSnapshot.loan_id)
        .join(newest, and_(RepaymentSnapshot.loan_id == newest.c.loan_id,
                           RepaymentSnapshot.as_of_date == newest.c.as_of_date))
        .filter(RepaymentSnapshot.recovery_potential == band)
        .all()
    ]


def _format_case(case: Case, agent_name_map: dict | None = None,
                 visited_today_ids: set[str] | None = None,
                 recovery_map: dict[str, dict] | None = None,
                 reassignment_map: dict[str, dict] | None = None) -> dict:
    c = case.customer
    l = case.loan
    agent_name = None
    if agent_name_map and case.agent_id:
        agent_name = agent_name_map.get(case.agent_id)
    return {
        "id": case.id,
        "case_number": case.case_number,
        "status": case.status,
        "priority": case.priority,
        "target_amount": case.target_amount,
        "collected_amount": case.collected_amount,
        "allocation_date": case.allocation_date,
        "visit_count": case.visit_count,
        "is_escalated": case.is_escalated,
        "agent_id": case.agent_id,
        "agent_name": agent_name,
        # The most recent manager reassignment, read from the audit trail. None
        # when the case has never been moved by hand — which, with sticky
        # ownership, means the nightly plan alone put it where it is.
        "last_reassignment": (reassignment_map or {}).get(case.id),
        "max_visits_allowed": case.max_visits_allowed,
        "handover_notes": case.handover_notes,
        "customer": {
            "id": c.id,
            "customer_ref": c.customer_ref,
            "full_name": c.full_name,
            "phone_primary": c.phone_primary,
            "phone_alternate": c.phone_alternate,
            "address_line1": c.address_line1,
            "city": c.city,
            "state": c.state,
            "pincode": c.pincode,
            "latitude": c.latitude,
            "longitude": c.longitude,
            "risk_category": c.risk_category,
            "risk_score": c.risk_score,
            "cibil_score": c.cibil_score,
            "is_hostile": c.is_hostile,
            "do_not_contact": c.do_not_contact,
            # Was omitted while its two sibling flags were sent, so the manager
            # case view could not show it even though allocation enforces it.
            "requires_female_agent": c.requires_female_agent,
            "fraud_flag": c.fraud_flag,
            "customer_segment": c.customer_segment,
            "language_preference": c.language_preference,
        },
        "loan": {
            "id": l.id,
            "loan_account_number": l.loan_account_number,
            "loan_type": l.loan_type,
            "bank_name": l.bank_name,
            "sanctioned_amount": l.sanctioned_amount,
            "outstanding_principal": l.outstanding_principal,
            "outstanding_interest": l.outstanding_interest,
            "penal_charges": l.penal_charges,
            "total_outstanding": l.total_outstanding,
            "overdue_amount": l.overdue_amount,
            "emi_amount": l.emi_amount,
            "tenure_months": l.tenure_months,
            "interest_rate": l.interest_rate,
            "dpd": l.dpd,
            "dpd_bucket": l.dpd_bucket,
            "status": l.status,
            "npa_flag": l.npa_flag,
            "last_payment_date": l.last_payment_date,
            "last_payment_amount": l.last_payment_amount,
            "next_due_date": l.next_due_date,
            "legal_status": l.legal_status,
            "settlement_status": l.settlement_status,
            "bank_risk_score": l.bank_risk_score,
            "collection_priority_score": l.collection_priority_score,
        },
        # ── "due_now" REMOVED FROM THE CASE PAYLOAD (2026-08-27) ────────────
        # It was added on 2026-08-24 as overdue_amount + penal_charges — a ledger
        # fact, and defensible in isolation. In practice it misled, and the
        # reason is position rather than arithmetic: on a case row it sat beside
        # "Target / Collected", and being the largest number there it read as the
        # amount to collect. It is not that. A manager comparing "Due now
        # Rs 15.0 L" against "Target Rs 70,000 / Collected Rs 70,000" cannot tell
        # which figure the case is judged on.
        #
        # The two components are still on the payload as loan.overdue_amount and
        # loan.penal_charges, so nothing is lost — only the pre-summed figure
        # that invited the wrong reading is gone.
        #
        # NOTE FOR THE ANALYTICS PATH: the recovery breakdown still pairs arrears
        # against the 90-day estimate per band. That pairing is the point of that
        # card — a bare estimate with no fact beside it would be worse than the
        # problem being fixed — so the figure stayed and only its NAME changed,
        # from "due_now" to "arrears_and_penal". A total on a summary card is
        # read as a total; the same number on a case row is read as an
        # instruction, which is the whole difference.
        #
        # Computed HIGH/MEDIUM/LOW plus the 30/60/90 ramp, or None when this loan
        # has not been scored yet. None rather than a default band: an unscored
        # loan is unknown, and rendering it as LOW would write off money nobody
        # has looked at.
        "recovery": (recovery_map or {}).get(case.loan_id),
        "collection_stage": case.collection_stage,
        "bank_ptp_date": case.bank_ptp_date,
        "bank_ptp_amount": case.bank_ptp_amount,
        "bank_ptp_status": case.bank_ptp_status,
        "bank_agent_remarks": case.bank_agent_remarks,
        "is_visited_today": (case.id in visited_today_ids) if visited_today_ids is not None else False,
    }


# ---------------------------------------------------------------------------
# GET /manager/dashboard
# ---------------------------------------------------------------------------

@router.get("/dashboard")
def dashboard(current_user: ManagerOnly, db: DbSession):
    # Scope all stats to this manager's agents only
    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    start_of_day, end_of_day, today_date = _effective_today(my_agent_ids, db)

    total_agents = len(my_agent_ids)
    # On duty = stored ON_DUTY minus anyone on APPROVED leave today. The
    # stored status alone lied whenever the 00:10 sync was missed — see
    # services/leave_service.py, 2026-09-22.
    _on_leave_today = agent_ids_on_leave(db, leave_today(), my_agent_ids)
    agents_on_leave = len(_on_leave_today)
    agents_on_duty = sum(
        1 for a in db.query(Agent).filter(Agent.id.in_(my_agent_ids)).all()
        if effective_status(a, _on_leave_today) is AgentStatus.ON_DUTY
    )
    total_cases = (
        db.query(func.count(Case.id)).filter(Case.agent_id.in_(my_agent_ids)).scalar() or 0
    )
    cases_assigned = (
        db.query(func.count(Case.id))
        .filter(
            Case.agent_id.in_(my_agent_ids),
            Case.status.in_([
                CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS, CaseStatus.PTP_SET,
                CaseStatus.PARTIALLY_PAID, CaseStatus.ESCALATED,
            ])
        )
        .scalar() or 0
    )
    # 2026-09-16 — every case this manager's agents hold, by status, for the
    # overview's case-pipeline donut. One GROUP BY rather than a query per
    # status. Every CaseStatus member is emitted, zero-filled, so a client can
    # tell "no ESCALATED cases" from "the field was not sent". The grouping into
    # resolved / in progress / not started is the CLIENT's, deliberately: the
    # server hands over facts, and RESOLVED_STATUSES (models/case.py) is the one
    # definition of "resolved" the client mirrors — see the frontend note.
    status_rows = (
        db.query(Case.status, func.count(Case.id))
        .filter(Case.agent_id.in_(my_agent_ids))
        .group_by(Case.status)
        .all()
    )
    case_status_counts = {st.value: 0 for st in CaseStatus}
    for st, n in status_rows:
        key = st.value if hasattr(st, "value") else str(st)
        case_status_counts[key] = int(n or 0)
    cases_resolved_today = (
        db.query(func.count(Case.id))
        .filter(
            Case.agent_id.in_(my_agent_ids),
            Case.status == CaseStatus.PAID,
            Case.resolved_at >= start_of_day,
            Case.resolved_at <= end_of_day,
        )
        .scalar() or 0
    )
    amount_collected_today = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.agent_id.in_(my_agent_ids), Payment.payment_date >= start_of_day, Payment.payment_date <= end_of_day)
        .scalar() or 0.0
    )
    ptps_due_today = (
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids), PTP.committed_date == today_date, PTP.status == PTPStatus.ACTIVE)
        .scalar() or 0
    )
    sos_active_count = (
        db.query(func.count(Agent.id))
        .filter(Agent.id.in_(my_agent_ids), Agent.sos_active == True)  # noqa: E712
        .scalar() or 0
    )
    visits_today = (
        db.query(func.count(Visit.id))
        .filter(Visit.agent_id.in_(my_agent_ids), Visit.check_in_time >= start_of_day)
        .scalar() or 0
    )
    # Beats FOR THE EFFECTIVE DAY. This query had no date filter and the loop
    # below kept each agent's LATEST beat, so as soon as a plan existed for
    # tomorrow the dashboard silently switched to describing tomorrow: the
    # target, the case count and the per-agent goals all came from the planned
    # beat while the money came from today. On 2026-09-02 that put today's
    # Rs 81.6L against tomorrow's book and rendered "102% achieved" with agents
    # showing more collected than they had been given.
    #
    # Only 142 of the two days' 214 cases overlapped, so it was not a rounding
    # artefact — it was two different case sets either side of one division.
    #
    # _effective_today already resolves this (max beat_date <= today, added by
    # commit 6eeb48c for exactly this reason); the beat query just never used
    # its answer. Fixed 2026-09-02.
    active_beats = (
        db.query(Beat)
        .filter(Beat.agent_id.in_(my_agent_ids), Beat.beat_date == today_date)
        .all()
    )
    latest_beat_by_agent: dict[str, Beat] = {}
    for b in active_beats:
        if b.agent_id not in latest_beat_by_agent or b.beat_date > latest_beat_by_agent[b.agent_id].beat_date:
            latest_beat_by_agent[b.agent_id] = b

    all_beat_case_ids: list[str] = []
    for b in latest_beat_by_agent.values():
        all_beat_case_ids.extend(b.ordered_case_ids or [])

    cases_today = len(set(all_beat_case_ids)) if all_beat_case_ids else cases_assigned
    # The FULL assigned collection target for today's beat cases.
    #
    # Not Loan.total_outstanding (the borrower's whole bill, Rs 30.36 Cr across
    # these same cases), and not target-less-what-was-already-collected. It is
    # the amount the agency was given to recover, which is what a manager is
    # held to.
    #
    # Consequence, stated plainly because it otherwise reads as a fault: the
    # gauge cannot reach 100% on a book carrying prior collections, because part
    # of the target was banked in earlier months and cannot be collected twice.
    # On 2026-09-02, Rs 74.5L of the Rs 1.77 Cr was already paid, putting the
    # ceiling for a single day at 58%. That is a property of measuring one day
    # against a multi-month target, not a defect in the arithmetic.
    amount_target_today = (
        db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
        .filter(Case.id.in_(all_beat_case_ids))
        .scalar() or 0.0
    ) if all_beat_case_ids else 0.0

    # 2026-09-16 — TODAY'S cases by DPD bucket, for the overview's donut. The
    # same case set as cases_today / amount_target_today (the effective day's
    # beats), grouped by the loan's current bucket. The overview used to show
    # the LIFETIME portfolio by bucket here — every case the agents have ever
    # held, resolved ones included — which duplicated the Analytics page's
    # "Collection by DPD Bucket" card row for row. This answers a different
    # question: what is on the team's plate today, and how old is it.
    # `collectable` is target − collected, the figure the planner works from.
    today_dpd_breakdown: list[dict] = []
    if all_beat_case_ids:
        for bucket, n, target, collected in (
            db.query(
                Loan.dpd_bucket,
                func.count(func.distinct(Case.id)),
                func.coalesce(func.sum(Case.target_amount), 0.0),
                func.coalesce(func.sum(Case.collected_amount), 0.0),
            )
            .join(Loan, Loan.id == Case.loan_id)
            .filter(Case.id.in_(all_beat_case_ids))
            .group_by(Loan.dpd_bucket)
            .all()
        ):
            key = bucket.value if hasattr(bucket, "value") else str(bucket)
            t = float(target or 0.0); c = float(collected or 0.0)
            today_dpd_breakdown.append({
                "bucket": key,
                "case_count": int(n or 0),
                "target_amount": round(t, 2),
                "collectable_amount": round(max(t - c, 0.0), 2),
            })

    # Return as percentage (0-100) so the frontend doesn't need to multiply
    collection_rate_pct = (
        round(amount_collected_today / amount_target_today * 100, 1) if amount_target_today > 0 else 0.0
    )

    # 2026-09-17 — the overview's Promises card. Promise status is a STOCK,
    # not a flow, so kept / broken are all-time over this manager's agents'
    # promises; "due" is forward-looking from the effective date. kept_rate is
    # honoured / (honoured + broken) — the two terminal outcomes — never
    # diluted by promises still open. One GROUP BY plus one COUNT.
    ptp_status_counts = {st.value: 0 for st in PTPStatus}
    for st, n in (
        db.query(PTP.status, func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids))
        .group_by(PTP.status)
        .all()
    ):
        ptp_status_counts[st.value if hasattr(st, "value") else str(st)] = int(n or 0)
    _kept = ptp_status_counts.get("HONORED", 0)
    _broken = ptp_status_counts.get("BROKEN", 0)
    ptp_due_next_7 = (
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids),
                PTP.status == PTPStatus.ACTIVE,
                PTP.committed_date >= today_date,
                PTP.committed_date <= today_date + timedelta(days=7))
        .scalar() or 0
    )
    ptp_health = {
        "status_counts": ptp_status_counts,
        "honored": _kept,
        "broken": _broken,
        "active": ptp_status_counts.get("ACTIVE", 0),
        "rescheduled": ptp_status_counts.get("RESCHEDULED", 0),
        "kept_rate_pct": round(_kept / (_kept + _broken) * 100, 1) if (_kept + _broken) > 0 else None,
        "due_next_7_days": int(ptp_due_next_7),
        "due_from": today_date.isoformat(),
        "due_to": (today_date + timedelta(days=7)).isoformat(),
    }

    return {
        "total_agents": total_agents,
        "agents_on_duty": agents_on_duty,
        "agents_on_leave": agents_on_leave,
        "total_cases": total_cases,
        "cases_assigned": cases_assigned,
        "case_status_counts": case_status_counts,
        "today_dpd_breakdown": today_dpd_breakdown,
        "ptp_health": ptp_health,
        "cases_today": cases_today,
        "cases_resolved_today": cases_resolved_today,
        "visits_today": visits_today,
        "amount_collected_today": amount_collected_today,
        "amount_target_today": amount_target_today,
        "collection_rate_today": collection_rate_pct,
        "ptps_due_today": ptps_due_today,
        "sos_active_count": sos_active_count,
        # The day every "today" figure above is actually measured against —
        # the latest beat date, which on seeded data trails the wall clock.
        # Exposed so drill-through links can filter on the same day the card
        # counted, instead of date.today() and landing on an empty result.
        "effective_date": today_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /manager/dashboard/field-activity
# ---------------------------------------------------------------------------
#
# 2026-09-17. The overview's Field Activity funnel: what happened to the cases
# PLANNED for field work inside one window — today (default), 7d or 30d. All
# definitions live in services/field_activity_service.py, shared with the
# Cases list's `activity` filter so the link under a number lands on exactly
# the cases the number counted.
#
# TODAY MEANS TODAY'S VISITS ONLY. The window is anchored on the same
# effective date and UTC day boundaries as every other "today" figure on the
# dashboard (`_effective_today`), and the service is handed only visits
# inside it — a visit yesterday cannot make a case visited, met or paid today.

@router.get("/dashboard/field-activity")
def dashboard_field_activity(
    current_user: ManagerOnly,
    db: DbSession,
    window: str = "today",
):
    from app.services import field_activity_service as fa

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    _, end_of_day, eff_date = _effective_today(my_agent_ids, db)
    bounds = fa.window_bounds(window, eff_date, end_of_day)
    result = fa.load_field_activity(db, my_agent_ids, bounds)
    return fa.payload(result, bounds)


# ---------------------------------------------------------------------------
# GET /manager/agents
# ---------------------------------------------------------------------------

# How many complete months the collection-rate sparkline covers. Five keeps the
# mark readable at ~60px wide; the seed carries six months of history, of which
# the newest is the in-progress month and is deliberately excluded.
_TREND_MONTHS = 5


def _complete_months_before(anchor: date, count: int) -> list[str]:
    """The `count` complete months immediately before `anchor`'s own month.

    Oldest first, as "YYYY-MM" — the API's month key (AgentPerformance.month
    is a DATE since 2026-09-24; convert with models.agent.month_start). `anchor`'s month
    is excluded because it is still accruing — see the call site.
    """
    months: list[str] = []
    year, month = anchor.year, anchor.month
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        months.append(f"{year:04d}-{month:02d}")
    return list(reversed(months))


def _recent_months(anchor: date, count: int) -> list[str]:
    # The `count` months ending at anchor's month, oldest first (YYYY-MM).
    # Steps back one month arithmetically rather than subtracting 30-day
    # timedelta chunks: 30 days from the 1st can land two iterations in the
    # same month and skip another entirely, silently shortening the window.
    if count <= 0:
        return []
    months: list[str] = []
    year, month = anchor.year, anchor.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def _ptp_kept(db):
    """"The borrower kept this promise" — ONE definition, used by every caller.

    It exists as a function because it did not, and the cost showed up
    immediately. The per-month figures counted HONORED, PARTIALLY_HONORED or a
    verified payment by the due date; the headline KPI in analytics() counted
    HONORED alone. Both feed the SAME CARD — the UI shows the selected month's
    rate when a month is picked and this KPI when none is — so clicking a month
    moved the number from 29.0% to 61.1% on identical data. A card whose value
    depends on what you clicked is worse than a card with no value, and two
    copies of a predicate is how that happens twice.

    The payment clause is the interesting half. Some PTPs sit at ACTIVE even
    though the borrower paid on time — 69 of the 293 matured promises on this
    book — because nothing closed them out. Reading status alone reports those
    agents as having converted nothing. The payment ledger is the harder
    evidence: money arrived, on or before the promised date, and it was
    verified. Status hygiene is not allowed to be the thing that decides whether
    an agent's work counts.

    Correlated on PTP deliberately — the subquery references PTP.case_id and
    PTP.agent_id from the enclosing query, so it must be built where PTP is in
    the outer FROM. That is why this takes `db` rather than being a constant.
    """
    paid_by_due_date = (
        db.query(Payment.id)
        .filter(
            Payment.case_id == PTP.case_id,
            Payment.agent_id == PTP.agent_id,
            Payment.status == PaymentStatus.VERIFIED,
            func.date(Payment.payment_date) <= PTP.committed_date,
        )
        .exists()
    )
    return (
        PTP.status.in_([PTPStatus.HONORED, PTPStatus.PARTIALLY_HONORED])
        | paid_by_due_date
    )


def _live_monthly_metrics(db, agent_ids: list[str], months: list[str]) -> dict[str, dict[str, dict]]:
    """Per-agent, per-month performance computed from the transactional tables.

    AgentPerformance is deliberately NOT read here. That table is written by one
    job — take_monthly_snapshot, `crontab(day_of_month=1)` — and it writes the
    row for the month that just ENDED, so the current month's row is never
    updated while the month is running. On a seeded box the current row exists
    only because seed_data.py created it, with
    `coll_rate = random.uniform(0.10, 0.55) * ...` — a random number. The Agents
    page was reading that number and overwriting the live counters with it, so
    an agent with 24 real visits displayed 152 and a rate that never moved no
    matter how much they collected.

    One definition, applied identically to every month including the current
    one, so the gauge, the delta chip and the sparkline are the same
    measurement rather than three:

      collected     VERIFIED payments dated in the month. PENDING_VERIFICATION
                    is money the borrower has not yet confirmed by OTP, so it
                    is not collected yet.
      target        target_amount summed over the DISTINCT cases the agent
                    VISITED that month — the same notion of "target" the
                    today_target figure in this endpoint already uses, extended
                    from a day to a month.
      rate          collected / target, 0 when the agent visited nothing.
      ptps_set      PTPs whose committed_date falls in the month AND has passed.
                    Promises not yet due are excluded — one dated next week is
                    an unknown, not a broken promise.
      ptps_honored  How many of those were honoured. Scoping to the month
                    matters: the snapshot task counts every honoured PTP the
                    agent has ever set against a single month's total, which
                    climbs forever and can exceed ptps_set.
      ptps_captured VISITS that secured a commitment (PTP or PART_PAID_PTP).
      visits_needing_promise
                    Visits where a commitment was the right outcome — every
                    visit except PAID_FULL, which left nothing to promise.
      ptp_capture_pct
                    captured / needing a promise. A DIFFERENT metric from
                    ptps_honored: capture grades the agent at the door, honoured
                    grades the borrower's follow-through. See the note at the
                    visit query.

    Four grouped queries for the whole team, not four per agent — this endpoint
    already runs four per-agent queries in its main loop and does not need more.
    """
    if not agent_ids or not months:
        return {}

    wanted = set(months)
    oldest = min(months)
    window_start = datetime(int(oldest[:4]), int(oldest[5:7]), 1, tzinfo=timezone.utc)

    out: dict[str, dict[str, dict]] = {
        aid: {mo: {"visits": 0, "collected": 0.0, "target": 0.0,
                   "ptps_set": 0, "ptps_honored": 0,
                   "visits_needing_promise": 0, "ptps_captured": 0}
              for mo in months}
        for aid in agent_ids
    }

    def _month_of(col):
        # Postgres-only, like the enum types and psycopg2 driver this app
        # already depends on. Produces the API's "YYYY-MM" month key (this
        # comment said it matched AgentPerformance.month, which is a DATE since
        # 2026-09-24).
        return func.to_char(col, "YYYY-MM")

    # 1. Collected
    pay_month = _month_of(Payment.payment_date)
    for aid, mo, total in (
        db.query(Payment.agent_id, pay_month, func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(Payment.agent_id.in_(agent_ids),
                Payment.payment_date >= window_start,
                Payment.status == PaymentStatus.VERIFIED)
        .group_by(Payment.agent_id, pay_month)
        .all()
    ):
        if mo in wanted and aid in out:
            out[aid][mo]["collected"] = float(total or 0.0)

    # 2. Visit count, and PTP CAPTURE off the same scan.
    #
    # Capture answers a different question from ptps_honored below, and the two
    # must not be confused: capture grades the AGENT AT THE DOOR (did the visit
    # produce a commitment, or nothing?), while ptps_honored grades the
    # BORROWER'S FOLLOW-THROUGH (was the promise met?). They fail independently
    # and the gap between them is the diagnosis — high capture with low kept
    # means the team is collecting soft promises to close the visit; low capture
    # with high kept means they only commit the reliable ones. One number cannot
    # tell you which, and the wrong one sends you to the wrong fix.
    #
    # PAID_FULL is the only outcome excluded from the denominator: nothing is
    # left to promise, so no promise was the right result and counting it as a
    # miss would penalise the best visit of the day. PART_PAID DELIBERATELY
    # STAYS IN — a balance survives, so a commitment for the remainder was the
    # right outcome and its absence is a genuine miss.
    #
    # PART_PAID_PTP counts as captured: money arrived AND the rest was
    # committed to. It is the best mixed outcome there is.
    #
    # Grain is the VISIT, not the case. Three visits to one stubborn case count
    # as three attempts, because this measures doorstep effort efficiency rather
    # than case coverage — and an agent who needs three knocks to get one
    # promise is doing something different from one who needs one.
    visit_month = _month_of(Visit.check_in_time)
    _captured = Visit.outcome.in_([VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP])
    _needs_promise = Visit.outcome != VisitOutcome.PAID_FULL
    for aid, mo, n, n_elig, n_cap in (
        db.query(
            Visit.agent_id, visit_month,
            func.count(Visit.id),
            func.count(sa_case((_needs_promise, Visit.id))),
            func.count(sa_case((_captured, Visit.id))),
        )
        .filter(Visit.agent_id.in_(agent_ids), Visit.check_in_time >= window_start)
        .group_by(Visit.agent_id, visit_month)
        .all()
    ):
        if mo in wanted and aid in out:
            out[aid][mo]["visits"] = int(n or 0)
            out[aid][mo]["visits_needing_promise"] = int(n_elig or 0)
            out[aid][mo]["ptps_captured"] = int(n_cap or 0)

    # 3. Target — distinct cases visited, then summed. Two visits to the same
    #    case in a month must not count its target twice.
    visited = (
        db.query(
            Visit.agent_id.label("agent_id"),
            visit_month.label("mo"),
            Visit.case_id.label("case_id"),
            Case.target_amount.label("target_amount"),
        )
        .join(Case, Case.id == Visit.case_id)
        .filter(Visit.agent_id.in_(agent_ids), Visit.check_in_time >= window_start)
        .distinct()
        .subquery()
    )
    for aid, mo, total in (
        db.query(visited.c.agent_id, visited.c.mo, func.coalesce(func.sum(visited.c.target_amount), 0.0))
        .group_by(visited.c.agent_id, visited.c.mo)
        .all()
    ):
        if mo in wanted and aid in out:
            out[aid][mo]["target"] = float(total or 0.0)

    # 4. PTPs DUE in the month, and how many of those were honoured.
    #
    # 2026-08-25: this bucketed on PTP.created_at, and the Analytics PTP
    # conversion gauge read 0% for every month except the one the database was
    # seeded in. created_at is `server_default now()` — the row-insert
    # timestamp, not a business date — so all 457 seeded PTPs carried
    # 2026-08-21 and every other month had an empty denominator.
    #
    # The bug is not merely a seeded-data artefact. The other three metrics in
    # this helper already key off business dates (Payment.payment_date,
    # Visit.check_in_time); PTP was the only one keying off a row timestamp,
    # which is wrong for a monthly business figure however the rows were
    # created. committed_date — the date the borrower promised to pay — is the
    # business date, and it is already indexed twice.
    #
    # NOT-YET-DUE PROMISES ARE EXCLUDED, and that is the other half of the fix.
    # Simply switching the column would have shown August at 6.2%, because 178
    # of its 211 promises had not come due yet and every one of them counted
    # against the denominator. A promise dated next week is not a broken
    # promise; it is an unknown one — the same distinction the recovery
    # labeller draws between "observed and zero" and "not yet matured".
    #
    # The maturity test is on the DATE, not on the status. Excluding rows that
    # are still ACTIVE instead would make the figure depend on agents keeping
    # PTP statuses tidy, and would flatter it: August would read 39.4% by
    # ignoring the 99 promises that came due and are still unresolved. Keying
    # on the date needs no status hygiene and states a fact — as of today, that
    # promise came due and was not honoured. It reads conservatively, which is
    # the right direction for a figure a manager acts on.
    #
    # A verified payment by the promised date is also treated as honoured
    # evidence. Some legacy/seed rows were left ACTIVE even though the borrower
    # paid on time, which made the manager table show 0% for agents who had
    # actually converted promises.
    ptp_month = _month_of(PTP.committed_date)
    for aid, mo, n_set, n_hon in (
        db.query(
            PTP.agent_id,
            ptp_month,
            func.count(PTP.id),
            func.count(sa_case((_ptp_kept(db), PTP.id))),
        )
        .filter(PTP.agent_id.in_(agent_ids),
                PTP.committed_date >= window_start.date(),
                PTP.committed_date <= date.today())
        .group_by(PTP.agent_id, ptp_month)
        .all()
    ):
        if mo in wanted and aid in out:
            out[aid][mo]["ptps_set"] = int(n_set or 0)
            out[aid][mo]["ptps_honored"] = int(n_hon or 0)

    # Derive the rates once, here, so no caller can reinvent them differently.
    # That is not a style preference: the PTP conversion figure has now had the
    # same two-definitions-one-card bug twice, both times because a caller
    # computed its own version of a rate this helper already knew.
    for per_month in out.values():
        for stats in per_month.values():
            stats["rate_pct"] = (
                round(stats["collected"] / stats["target"] * 100, 1)
                if stats["target"] > 0 else 0.0
            )
            stats["ptp_capture_pct"] = (
                round(stats["ptps_captured"] / stats["visits_needing_promise"] * 100, 1)
                if stats["visits_needing_promise"] > 0 else 0.0
            )
    return out


@router.get("/agents")
def list_agents(current_user: ManagerOnly, db: DbSession):
    agents = (db.query(Agent)
              .filter(Agent.manager_user_id == current_user.id)
              .options(joinedload(Agent.user))
              .order_by(Agent.employee_code)
              .all())
    agent_ids = [a.id for a in agents]
    start_of_day, end_of_day, eff_date = _effective_today(agent_ids, db)
    eff_date_str = eff_date.isoformat()
    on_leave_today = agent_ids_on_leave(db, leave_today(), agent_ids)

    # ── Today's figures: three grouped queries for the WHOLE team ────────────
    # This was four queries PER AGENT in the loop below — 61 statements for a
    # 15-agent team, measured 2026-08-28 with an event listener on the live
    # book. The per-agent versions are preserved semantically, only grouped:
    #
    #   cases_today      count of cases allocated the effective day
    #   today_collected  VERIFIED payments since start of day. VERIFIED only,
    #                    matching the monthly figures below — without the filter
    #                    this counted PENDING_VERIFICATION too (money the
    #                    borrower has not yet confirmed by OTP), so "collected
    #                    today" could exceed the month's collected total.
    #   today_target     target_amount summed over the DISTINCT cases the agent
    #                    visited today. The subquery keeps the DISTINCT at the
    #                    (agent, case) grain the old two-step version had: two
    #                    visits to one case must not count its target twice,
    #                    while two AGENTS visiting the same case each count it,
    #                    exactly as before.
    # Beats FOR THE EFFECTIVE DAY. This query had no date filter and the loop
    # below kept each agent's LATEST beat, so as soon as a plan existed for
    # tomorrow the dashboard silently switched to describing tomorrow: the
    # target, the case count and the per-agent goals all came from the planned
    # beat while the money came from today. On 2026-09-02 that put today's
    # Rs 81.6L against tomorrow's book and rendered "102% achieved" with agents
    # showing more collected than they had been given.
    #
    # Only 142 of the two days' 214 cases overlapped, so it was not a rounding
    # artefact — it was two different case sets either side of one division.
    #
    # _effective_today already resolves this (max beat_date <= today, added by
    # commit 6eeb48c for exactly this reason); the beat query just never used
    # its answer. Fixed 2026-09-02.
    active_beats = (
        db.query(Beat)
        .filter(Beat.agent_id.in_(agent_ids), Beat.beat_date == eff_date)
        .all()
    )
    latest_beat_by_agent: dict[str, Beat] = {}
    for b in active_beats:
        if b.agent_id not in latest_beat_by_agent or b.beat_date > latest_beat_by_agent[b.agent_id].beat_date:
            latest_beat_by_agent[b.agent_id] = b

    cases_today_by_agent: dict[str, int] = {}
    target_by_agent: dict[str, float] = {}

    # One query for the whole team, not one per agent.
    #
    # 2026-09-03 — this summed Case.target_amount inside the loop below, so a
    # 15-agent team cost 15 round trips and the endpoint ran 23 statements. That
    # is the same fault the comment above records fixing on 2026-08-28: the four
    # per-agent queries were consolidated, and a fifth was later added back
    # underneath. This endpoint is the leaderboard and it is polled, so the cost
    # is paid on every refresh by every manager.
    #
    # set() is load-bearing. SQL's SUM ... WHERE id IN (...) counts a case ONCE
    # however many times the beat lists it; summing a Python list would count it
    # twice. No beat holds a duplicate today and nothing enforces that, so the
    # rewrite matches SQL's semantics rather than relying on the data staying
    # clean.
    _beat_case_ids = {
        cid for b in latest_beat_by_agent.values() for cid in (b.ordered_case_ids or [])
    }
    _target_of: dict[str, float] = {}
    if _beat_case_ids:
        _target_of = {
            cid: float(amt or 0.0) for cid, amt in
            db.query(Case.id, Case.target_amount).filter(Case.id.in_(_beat_case_ids)).all()
        }

    for aid, b in latest_beat_by_agent.items():
        c_ids = b.ordered_case_ids or []
        cases_today_by_agent[aid] = len(c_ids)
        if c_ids:
            target_by_agent[aid] = sum(_target_of.get(cid, 0.0) for cid in set(c_ids))

    collected_by_agent = dict(
        db.query(Payment.agent_id, func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(Payment.agent_id.in_(agent_ids),
                Payment.payment_date >= start_of_day,
                Payment.status == PaymentStatus.VERIFIED)
        .group_by(Payment.agent_id)
        .all()
    )

    result = []
    for agent in agents:
        cases_today = cases_today_by_agent.get(agent.id, 0) or 0
        today_collected = collected_by_agent.get(agent.id, 0.0) or 0.0
        today_target = target_by_agent.get(agent.id, 0.0) or 0.0
        result.append({
            "id": agent.id,
            "user_id": agent.user_id,
            "employee_code": agent.employee_code,
            "id_card_number": agent.id_card_number,
            "full_name": agent.user.full_name,
            "date_of_birth": agent.user.date_of_birth,
            # G02 (Manage Agents): the table and edit drawer both need these,
            # already on the row through the same joinedload(Agent.user)
            # above — no new query, just two more keys off it.
            "email": agent.user.email,
            "phone": agent.user.phone,
            "gender": agent.gender,
            "vehicle_type": agent.vehicle_type,
            "territory_region_id": agent.territory_region_id,
            "base_latitude": agent.base_latitude,
            "base_longitude": agent.base_longitude,
            "suspended_at": agent.suspended_at,
            "suspended_reason": agent.suspended_reason,
            "territory": agent.territory,
            "tier": agent.tier,
            "status": effective_status(agent, on_leave_today),
            "specialization": agent.specialization,
            "languages_spoken": agent.languages_spoken,
            "ranking_score": agent.ranking_score,
            "max_cases_per_day": agent.max_cases_per_day,
            "current_month_visits": agent.current_month_visits,
            "current_month_collections": agent.current_month_collections,
            "current_month_ptps_set": agent.current_month_ptps_set,
            "current_month_ptps_honored": agent.current_month_ptps_honored,
            "lifetime_collection_rate": agent.lifetime_collection_rate,
            "last_known_latitude": agent.last_known_latitude,
            "last_known_longitude": agent.last_known_longitude,
            "last_location_update": agent.last_location_update,
            "sos_active": agent.sos_active,
            "cases_today": cases_today,
            "today_collected": round(float(today_collected), 2),
            "today_target": round(float(today_target), 2),
        })

    # ── Current month + trend, both from live data ───────────────────────────
    # One call, one definition (see _live_monthly_metrics). The current month
    # feeds the headline figures; the COMPLETE months behind it feed the
    # sparkline and the delta.
    #
    # The current month is deliberately kept out of the trend: on any day but
    # the last it is a part-month and always reads low, so trending it would
    # show every agent declining every month as an artifact of the calendar.
    current_month_str = eff_date.strftime("%Y-%m")
    trend_months = _complete_months_before(eff_date, _TREND_MONTHS)
    metrics = _live_monthly_metrics(db, agent_ids, [*trend_months, current_month_str])

    for item in result:
        per_month = metrics.get(item["id"], {})
        now = per_month.get(current_month_str)

        if now:
            item["current_month_visits"] = now["visits"]
            item["current_month_collections"] = round(now["collected"], 2)
            item["current_month_ptps_set"] = now["ptps_set"]
            item["current_month_ptps_honored"] = now["ptps_honored"]
            item["collection_rate_pct"] = now["rate_pct"]
            item["ptp_rate_pct"] = (
                round(now["ptps_honored"] / now["ptps_set"] * 100, 1)
                if now["ptps_set"] > 0 else 0.0
            )
        else:
            item["collection_rate_pct"] = 0.0
            item["ptp_rate_pct"] = 0.0

        # A month the agent visited nothing in has no rate to plot — that is a
        # gap, not a zero, and the client draws it as a break in the line
        # rather than as a collapse to the axis.
        series = [
            per_month[mo]["rate_pct"] if per_month.get(mo, {}).get("target", 0) > 0 else None
            for mo in trend_months
        ]
        item["collection_rate_trend"] = [
            {"month": mo, "rate_pct": val} for mo, val in zip(trend_months, series)
        ]
        observed = [v for v in series if v is not None]
        item["collection_rate_delta_pts"] = (
            round(observed[-1] - observed[-2], 1) if len(observed) >= 2 else None
        )

    return result



# ---------------------------------------------------------------------------
# GET /manager/cases
# ---------------------------------------------------------------------------

# Statuses that drop out of the actionable priority view. Imported from the one
# place that defines them rather than restated — services/visit_priority_service
# already withholds a score for these, and two lists would drift.
from app.services.visit_priority_service import (  # noqa: E402
    _RESOLVED_STATUSES as _VISIT_PRIORITY_EXCLUDED,
    score_cases,
    sort_key as _vp_sort_key,
)


# ── The page payload, built once for both orderings (2026-08-27) ─────────────
# list_cases has two paths now — the legacy allocation_date ordering and the
# visit-priority ordering — and both must return byte-identical row shapes. Two
# copies of this block would drift the first time one of them gained a field.
def _cases_payload(db, cases: list, my_agent_ids: list[str], total: int,
                   scored: dict[str, dict] | None = None) -> dict:
    # Recovery labels for this page only — one query, not one per row.
    recovery_map = _latest_recovery_by_loan(
        db, [c.loan_id for c in cases if c.loan_id])

    # Build agent name map for this page only
    page_agent_ids = {c.agent_id for c in cases if c.agent_id}
    agent_name_map: dict[str, str] = {}
    if page_agent_ids:
        rows = (
            db.query(Agent.id, User.full_name)
            .join(User, Agent.user_id == User.id)
            .filter(Agent.id.in_(page_agent_ids))
            .all()
        )
        agent_name_map = {r[0]: r[1] for r in rows}

    # Compute which cases on this page carry the "Visited" chip.
    page_case_ids = {c.id for c in cases}
    visited_today_ids: set[str] = set()
    # The wall clock, and only the wall clock.
    #
    # 2026-09-03 — a DEMO_VISITED_BY_ALLOCATION_DATE branch used to sit here,
    # matching each visit against its own case's allocation_date instead of
    # today. It existed because the seed ran once and its visits aged out, so a
    # demo given weeks later showed no chips at all.
    #
    # It was on in backend/.env AND in .env.example, so every new deployment
    # inherited it — the code default of False was never the effective value.
    # And once allocation_date was corrected to mean "the day this case was last
    # worked", the comparison became circular: a visit's date matches the date
    # derived from that visit. Measured before removal, it marked 617 cases as
    # visited today on a day when 0 had been visited.
    #
    # A stale demo is fixed by generating today's activity — the daily feed and
    # scripts/demo_collect_to_target.py both do — not by relabelling last
    # month's as today's.
    if page_case_ids:
        today = date.today()
        day_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        day_end   = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
        visited_today_ids = {
            row[0] for row in
            db.query(Visit.case_id)
            .filter(
                Visit.case_id.in_(page_case_ids),
                Visit.check_in_time >= day_start,
                Visit.check_in_time <= day_end,
            )
            .distinct().all()
        }

    reassignment_map = _latest_reassignments(db, list(page_case_ids)) if page_case_ids else {}

    # 2026-09-18 — the promise the agent took, per case. The Promises card's
    # "Due this week" opens this list filtered by promise date, and every row
    # then showed only allocation_date (<= today), so a manager saw a list
    # called "due this week" with no date in it later than today. The earliest
    # ACTIVE promise per case is what "due" means; `bank_ptp_*` on the row is
    # the BANK's field and a different thing. One query for the page.
    next_ptp_map: dict[str, dict] = {}
    if page_case_ids:
        for cid, due, amt in (
            db.query(PTP.case_id, PTP.committed_date, PTP.committed_amount)
            .filter(PTP.case_id.in_(page_case_ids), PTP.status == PTPStatus.ACTIVE)
            .order_by(PTP.case_id, PTP.committed_date.asc())
            .all()
        ):
            next_ptp_map.setdefault(cid, {"committed_date": due.isoformat() if due else None,
                                          "committed_amount": float(amt or 0.0)})

    return {
        "total": total,
        "cases": [
            {**_format_case(c, agent_name_map, visited_today_ids, recovery_map,
                            reassignment_map),
             # The SAME score object the case-detail panel renders. Attached per
             # page rather than per book: 50 rows cost three bounded queries.
             "visit_priority": (scored or {}).get(c.id),
             "next_ptp": next_ptp_map.get(c.id)}
            for c in cases
        ],
    }


@router.get("/cases")
def list_cases(
    current_user: ManagerOnly,
    db: DbSession,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    agent_id: UUIDQuery = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    # HIGH / MEDIUM / LOW. Filters on the loan's LATEST computed label, so it
    # answers "show me where the recoverable money is" rather than "show me what
    # the label said on some past day".
    recovery: Optional[str] = None,
    # CURRENT / BUCKET_1 / BUCKET_2 / BUCKET_3 / NPA — the loan's DPD bucket.
    # Server-side, same reason as `recovery` just above: the overview's "Today's
    # Cases by DPD" donut links here with ?bucket=, and until 2026-10-01 the
    # frontend filtered this client-side on the one page already fetched (and
    # only recognised 3 of the 5 buckets), so the donut's count and the list
    # disagreed (demo QA sweep).
    dpd_bucket: Optional[str] = None,
    # ── Visit priority (2026-08-27) ─────────────────────────────────────────
    # sort="priority_desc" | "priority_asc" turns the list into the ACTIONABLE
    # priority view: resolved cases drop out (a settled case has no next visit
    # to rank), and the order comes from ml/visit_priority.py.
    #
    # Anything else — including the default — keeps the long-standing
    # allocation_date ordering untouched, so the page a manager already knows
    # behaves exactly as before.
    sort: Optional[str] = None,
    # HIGH / MEDIUM / LOW on the visit-priority band. Distinct from `recovery`,
    # which bands how much of the LOAN comes back; this bands how much the case
    # is worth working NEXT.
    priority_band: Optional[str] = None,
    # ── Field activity (2026-09-17) ──────────────────────────────────────────
    # The overview's funnel links here. `activity` is a funnel stage —
    # planned | visited | met | paid_or_promised | not_met | met_no_money —
    # and `activity_window` is today | 7d | 30d, anchored exactly as the
    # funnel is. `visit_outcome` (comma list) narrows a reason stage to the
    # cases whose classifying in-window outcome is one of them. Resolved
    # through the SAME service the funnel uses, so the rows are the count.
    # Window-scoped by construction: with activity_window=today, a case whose
    # only visit was yesterday is not "visited".
    activity: Optional[str] = None,
    activity_window: Optional[str] = None,
    visit_outcome: Optional[str] = None,
    # ── Promises (2026-09-17) ────────────────────────────────────────────────
    # Cases with an ACTIVE promise committed inside [ptp_due_from, ptp_due_to].
    ptp_due_from: Optional[str] = None,
    ptp_due_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    q = (db.query(Case)
         .filter(Case.agent_id.in_(my_agent_ids))
         .options(joinedload(Case.customer), joinedload(Case.loan)))

    if activity:
        from app.services import field_activity_service as fa
        if activity not in fa.STAGES:
            raise HTTPException(status_code=422, detail=f"activity must be one of {list(fa.STAGES)}")
        _, end_of_day, eff_date = _effective_today(my_agent_ids, db)
        bounds = fa.window_bounds(activity_window or "today", eff_date, end_of_day)
        outcomes = [o.strip().upper() for o in visit_outcome.split(",") if o.strip()] if visit_outcome else None
        ids = fa.case_ids_for(db, my_agent_ids, bounds, activity, outcomes)
        # An empty set must yield NO rows, not all rows: `in_([])` is false.
        q = q.filter(Case.id.in_(list(ids)) if ids else sa_false())
    if ptp_due_from or ptp_due_to:
        due_q = db.query(PTP.case_id).filter(PTP.agent_id.in_(my_agent_ids), PTP.status == PTPStatus.ACTIVE)
        if ptp_due_from:
            due_q = due_q.filter(PTP.committed_date >= date.fromisoformat(ptp_due_from))
        if ptp_due_to:
            due_q = due_q.filter(PTP.committed_date <= date.fromisoformat(ptp_due_to))
        q = q.filter(Case.id.in_(due_q.scalar_subquery()))

    if status:
        q = q.filter(Case.status == status)
    if priority:
        q = q.filter(Case.priority == priority)
    if recovery:
        # Resolved to loan ids first rather than joined in: the case query
        # already carries two joinedloads and a window-free correlated subquery
        # here would be re-evaluated per row.
        q = q.filter(Case.loan_id.in_(_loan_ids_with_recovery(db, recovery.upper())))
    if dpd_bucket:
        bucket = dpd_bucket.upper()
        if bucket not in DPDBucket.__members__:
            raise HTTPException(status_code=422, detail=f"dpd_bucket must be one of {list(DPDBucket.__members__)}")
        q = q.filter(Case.loan_id.in_(db.query(Loan.id).filter(Loan.dpd_bucket == bucket)))
    if agent_id:
        q = q.filter(Case.agent_id == agent_id)
    if date_from:
        q = q.filter(Case.allocation_date >= date_from)
    if date_to:
        q = q.filter(Case.allocation_date <= date_to)

    # ── Priority mode ───────────────────────────────────────────────────────
    # The score is COMPUTED, so it cannot be an ORDER BY. Two ways out were
    # possible: persist it on Case, or score the filtered set and paginate in
    # Python. This takes the second, deliberately.
    #
    # Persisting would need a nightly writer to keep ~500 open cases fresh, and
    # the list would then show a value up to a day stale while the case-detail
    # panel computes live — the two screens disagreeing about the same case is
    # the one outcome this feature cannot afford. Scoring in-request makes them
    # equal BY CONSTRUCTION: one function, one call, one answer.
    #
    # Measured on the live book: 487 open cases load in 67 ms, score in 49 ms
    # (three bounded queries plus pure arithmetic), sort in under 1 ms. And it
    # only runs when a manager actually asks for the priority view.
    # /manager/analytics already loads the whole open book this way.
    _priority_sort = (sort or "").lower() in ("priority_desc", "priority_asc")
    _band = (priority_band or "").upper() or None
    if _priority_sort or _band:
        # A settled case has no next visit, so it is not part of the actionable
        # view. ESCALATED is deliberately still in: open, visitable, and the
        # work a manager most wants surfaced.
        q = q.filter(Case.status.notin_(_VISIT_PRIORITY_EXCLUDED))
        ranked = q.all()
        scored = score_cases(db, ranked)
        if _band:
            ranked = [c for c in ranked
                      if (scored.get(c.id) or {}).get("band") == _band]
        # sort_key is the SAME key factory the allocator and the agent list use,
        # so "highest priority first" means one thing across the product.
        ranked.sort(key=_vp_sort_key(scored))
        if (sort or "").lower() == "priority_asc":
            ranked.reverse()
        total = len(ranked)
        cases = ranked[offset:offset + limit]
        return _cases_payload(db, cases, my_agent_ids, total,
                              scored={c.id: scored[c.id] for c in cases
                                      if c.id in scored})

    total = q.count()
    # Newest allocation_date first, and WITHIN a day the cases carrying money
    # lead, largest collected first. Date stays the primary key so page 1 is
    # still the most recent day's work; payment only reorders inside it.
    #
    # Ordered in the query, not in the page component: the list is paginated
    # server-side, so a client-side sort would only reorder the 50 rows already
    # fetched and leave a paid case on page 3 sitting on page 3.
    #
    # Two NULL guards, both because Postgres defaults a DESC sort to NULLS
    # FIRST — the opposite of what either column wants:
    #   * allocation_date is nullable (models/case.py:58), so undated cases
    #     would otherwise head the list ahead of the newest real day.
    #   * collected_amount is NOT NULL today (models/case.py:54), but were that
    #     to change, unpaid rows would float above paid ones.
    # allocation_date is String(10) 'YYYY-MM-DD', so lexicographic DESC is
    # chronological DESC.
    cases = (
        q.order_by(
            Case.allocation_date.desc().nullslast(),
            func.coalesce(Case.collected_amount, 0).desc(),
            Case.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Legacy path: score only the rows on this page, so the band chip is
    # present without paying for the whole book.
    return _cases_payload(db, cases, my_agent_ids, total,
                          scored=score_cases(db, cases))


# ---------------------------------------------------------------------------
# GET /manager/cases/date-range   — the span the Cases page defaults to
#
# MUST stay above /cases/{case_id}: FastAPI matches in declaration order, so
# below it "date-range" would be taken as a case_id. That fails as a 500, not a
# 404 — Postgres rejects the value as an invalid UUID before any lookup runs.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GET /manager/cases/unallocated
# ---------------------------------------------------------------------------
# Why a case has not been given to anybody. Added 2026-08-19 alongside the
# allocation eligibility rules: those rules can now WITHHOLD a case, and a
# control nobody can observe is one nobody can trust or audit. Reasons are
# recomputed here from the same ml/eligibility functions the allocator uses,
# rather than stored, so the two can never disagree.

@router.get("/cases/unallocated")
def unallocated_cases(current_user: ManagerOnly, db: DbSession):
    from app.ml.eligibility import (
        BLOCKED_DO_NOT_CONTACT, BLOCKED_NEEDS_FEMALE_AGENT,
        agent_block_reason, case_block_reason,
    )
    my_agents = (
        db.query(Agent).join(Agent.user)
        .filter(Agent.manager_user_id == current_user.id)
        .options(joinedload(Agent.user)).all()
    )
    _on_leave_today = agent_ids_on_leave(db, leave_today(), [a.id for a in my_agents])
    on_duty = [a for a in my_agents if effective_status(a, _on_leave_today) is AgentStatus.ON_DUTY]

    cases = (
        db.query(Case)
        .filter(Case.status == CaseStatus.UNASSIGNED)
        .options(joinedload(Case.customer))
        .all()
    )

    rows: list[dict] = []
    for case in cases:
        customer = case.customer
        blocked = case_block_reason(customer)
        if blocked is not None:
            reason, detail = blocked, "The bank has marked this customer do-not-contact."
        elif not on_duty:
            reason, detail = "NO_AGENT_ON_DUTY", "No agent in your team is on duty."
        elif not any(agent_block_reason(a, customer) is None for a in on_duty):
            reason = "NO_ELIGIBLE_AGENT"
            # Deliberately does not say WHY any INDIVIDUAL agent is ineligible.
            # The only current reason is the customer's female-agent
            # requirement, and agent gender is not a manager's to see —
            # surfacing it per agent would let the person whose workload the
            # rule blocks work around it.
            #
            # 2026-08-27 — but it now separates the two causes at TEAM level,
            # which names nobody:
            #
            #   * no agent record carries a gender at all — a CONFIGURATION
            #     defect. Agent.gender is nullable and was never populated, so
            #     is_female returns False for everyone and the rule is
            #     unsatisfiable. On the seeded book that stranded 324 cases
            #     permanently, and the old message ("no agent can be assigned")
            #     read as a staffing problem, which it is not. A control that
            #     withholds 8% of the book with no way to find out why is
            #     indistinguishable from a bug.
            #
            #   * genders ARE recorded and still nobody qualifies — a genuine
            #     staffing/shift problem, which a manager can act on.
            #
            # The reason CODE is unchanged either way: Command Center switches
            # on it, and splitting it would break that contract for a message.
            if not any(a.gender for a in on_duty):
                detail = ("No agent record in your team has a gender recorded, "
                          "so this customer's female-agent requirement cannot "
                          "be satisfied by anyone. This is a data configuration "
                          "gap, not a shift problem.")
            else:
                detail = "No agent in your team can be assigned to this customer."
        elif all(a.max_cases_per_day <= 0 for a in on_duty):
            reason, detail = "NO_CAPACITY", "Every agent is at their daily case limit."
        else:
            reason, detail = "AWAITING_ALLOCATION", "Will be assigned by tonight's run."

        rows.append({
            "case_id": case.id,
            "case_number": case.case_number,
            "customer_name": customer.full_name if customer else None,
            "city": customer.city if customer else None,
            "priority": case.priority,
            "target_amount": case.target_amount,
            "reason": reason,
            "detail": detail,
        })

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["reason"]] = counts.get(r["reason"], 0) + 1

    return {
        "cases": sorted(rows, key=lambda r: (r["reason"] != "AWAITING_ALLOCATION", r["case_number"]), reverse=True),
        "counts": counts,
        "total": len(rows),
        "blocked_reasons": {
            "DO_NOT_CONTACT": BLOCKED_DO_NOT_CONTACT,
            "REQUIRES_FEMALE_AGENT": BLOCKED_NEEDS_FEMALE_AGENT,
        },
    }


@router.get("/cases/date-range")
def cases_date_range(current_user: ManagerOnly, db: DbSession):
    """Oldest and newest allocation_date across this manager's cases.

    The Cases page used to default to "six months ago → today", which on seeded
    data both clipped the earliest cases and included a long empty tail: the
    newest allocation_date trails the wall clock. Returning the real span lets
    the page open on exactly the data that exists.

    *(That last sentence is no longer true of the DATA — the nightly allocator
    stamps TOMORROW's date on everything it plans, so the newest value now LEADS
    the clock, and on 2026-09-10 that put 229 of one manager's 877 cases on a
    date nobody could work yet. A clamp to today was added for exactly that
    reason and REVERTED the same day, because it was the wrong fix and the
    measurement says so plainly:

        cases visited today                   45
        of those carrying tomorrow's date     44
        cases that collected money today      16
        of those carrying tomorrow's date     16

    `allocation_date` is not "when this case was worked". It is "which beat this
    case is next on", and every still-open case is re-stamped by each plan run —
    including the ones an agent visited hours earlier. Clamping it therefore hid
    every case that had collected money that day, which is the first thing a
    manager looks for. The confusing part was never the range; it was a column
    labelled DATE that means "next beat". That is fixed in the page instead.)*

    Both are null when the manager has no cases; the page then leaves its date
    inputs empty, which list_cases above treats as unbounded.
    """
    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    if not my_agent_ids:
        return {"min": None, "max": None}

    # allocation_date is String(10) holding an ISO date, not a Date column (see
    # models/case.py), so MIN/MAX compare lexicographically — exactly right for
    # yyyy-mm-dd — and the values come back as strings already.
    row = (
        db.query(func.min(Case.allocation_date), func.max(Case.allocation_date))
        .filter(Case.agent_id.in_(my_agent_ids))
        .one()
    )
    return {"min": row[0], "max": row[1]}


# ---------------------------------------------------------------------------
# GET /manager/cases/{case_id}   — full case detail with visit trail
# ---------------------------------------------------------------------------

@router.get("/cases/{case_id}")
def get_case_detail(case_id: UUIDPath, current_user: ManagerOnly, db: DbSession):
    from sqlalchemy.orm import joinedload as jl
    from app.models.visit import Visit as VisitModel
    from app.models.payment import Payment as PaymentModel
    from app.models.ptp import PTP as PTPModel

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    case = (
        db.query(Case)
        .options(
            jl(Case.customer),
            jl(Case.loan),
            jl(Case.visits),
            jl(Case.payments),
            jl(Case.ptps),
        )
        .filter(Case.id == case_id, Case.agent_id.in_(my_agent_ids))
        .first()
    )
    if not case:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Case not found or not under your team")

    # Resolve agent name
    agent_name = None
    if case.agent_id:
        row = (
            db.query(User.full_name, Agent.employee_code, Agent.tier, Agent.territory)
            .join(Agent, Agent.user_id == User.id)
            .filter(Agent.id == case.agent_id)
            .first()
        )
        if row:
            agent_name = row[0]

    base = _format_case(
        case, {case.agent_id: agent_name} if agent_name else None,
        recovery_map=_latest_recovery_by_loan(db, [case.loan_id] if case.loan_id else []),
        reassignment_map=_latest_reassignments(db, [case.id]),
    )

    # Why this case sits where it does in the visit queue: the three named
    # components and their points. On the DETAIL only, never the list — the case
    # table has ten proportional tracks already and the last column added there
    # truncated agent names.
    #
    # The value component reports POINTS, not rupees. That is the rule this
    # module has followed since 2026-08-24 and tests/test_manager_recovery_surface
    # greps for it: a rupee figure on a case row is read as "collect this", and
    # rate_90 x total outstanding is not that.
    from app.services.visit_priority_service import score_cases
    base["visit_priority"] = score_cases(db, [case]).get(case.id)

    # Resolve agent names for all visits (visits may have different agents)
    visit_agent_ids = {v.agent_id for v in case.visits if v.agent_id}
    visit_agent_names: dict[str, str] = {}
    if visit_agent_ids:
        rows = (
            db.query(Agent.id, User.full_name)
            .join(User, User.id == Agent.user_id)
            .filter(Agent.id.in_(visit_agent_ids))
            .all()
        )
        visit_agent_names = {r[0]: r[1] for r in rows}

    from app.services.media_service import MediaService

    base["visits"] = [
        {
            "id": v.id,
            "visit_number": v.visit_number,
            "outcome": v.outcome,
            "customer_met": v.customer_met,
            "person_met": v.person_met,
            "default_reason": v.default_reason,
            "not_met_reason": v.not_met_reason,
            "notes": v.notes,
            "consent_given": v.consent_given,
            "geo_verified": v.geo_verified,
            "within_contact_hours": v.within_contact_hours,
            "distance_from_customer_metres": v.distance_from_customer_metres,
            "check_in_latitude": v.check_in_latitude,
            "check_in_longitude": v.check_in_longitude,
            "check_in_time": v.check_in_time.isoformat() if v.check_in_time else None,
            "check_out_time": v.check_out_time.isoformat() if v.check_out_time else None,
            "property_type": v.property_type,
            "occupancy_status": v.occupancy_status,
            "vehicle_present": v.vehicle_present,
            "business_running": v.business_running,
            "agent_name": visit_agent_names.get(v.agent_id) if v.agent_id else None,
            # Photos
            "has_agent_photo": bool(v.agent_photo_key),
            "has_borrower_photo": bool(v.borrower_photo_key),
            "has_object_photo": bool(v.object_photo_key),
            "agent_photo_lat": v.agent_photo_lat,
            "agent_photo_lon": v.agent_photo_lon,
            "borrower_photo_lat": v.borrower_photo_lat,
            "borrower_photo_lon": v.borrower_photo_lon,
            "object_photo_lat": v.object_photo_lat,
            "object_photo_lon": v.object_photo_lon,
            # Recordings
            "has_agent_recording": bool(v.agent_recording_key),
            "has_borrower_recording": bool(v.borrower_recording_key),
            # Transcripts (saved after transcription)
            "agent_recording_transcript": v.agent_recording_transcript,
            "borrower_recording_transcript": v.borrower_recording_transcript,
            # AI-generated audit report
            "ai_visit_note": v.ai_visit_note,
            # N1: what the agent typed and collected, which used to be dropped on submit
            "escalation_notes": v.escalation_notes,
            "witness_present": v.witness_present,
            "witness_name": v.witness_name,
            "documents": MediaService.document_entries(v),
        }
        # Most recent visit first — opening a case should show what happened
        # last, not the oldest attempt. The datetime.min fallback keeps visits
        # with no check-in time at the BOTTOM under this reverse ordering, which
        # is where an unknown timestamp belongs.
        for v in sorted(
            case.visits,
            key=lambda x: x.check_in_time or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    ]

    base["payments"] = [
        {
            "id": p.id,
            "receipt_number": p.receipt_number,
            "amount": p.amount,
            "mode": p.mode,
            "status": p.status,
            # NULL when the borrower paid remotely — settling a promise days
            # after the visit, with nobody at their door. Sent because without
            # it the Payments tab shows a payment count that cannot be
            # reconciled against the Visits count, and a manager reasonably
            # concludes a visit is missing. It is not: the money arrived by UPI
            # or transfer, and attributing it to a visit would inflate both the
            # contact rate and the per-visit yield.
            "visit_id": p.visit_id,
            "upi_reference": p.upi_reference,
            "cheque_number": p.cheque_number,
            "bank_reference": p.bank_reference,
            "payment_date": p.payment_date.isoformat() if p.payment_date else None,
        }
        for p in sorted(case.payments, key=lambda x: x.payment_date or datetime.min.replace(tzinfo=timezone.utc))
    ]

    base["ptps"] = [
        {
            "id": ptp.id,
            "committed_amount": ptp.committed_amount,
            "committed_date": ptp.committed_date.isoformat() if ptp.committed_date else None,
            "follow_up_date": ptp.follow_up_date.isoformat() if ptp.follow_up_date else None,
            "status": ptp.status,
            "customer_reason": ptp.customer_reason,
            "agent_notes": ptp.agent_notes,
        }
        for ptp in case.ptps
    ]

    # Photos — actual presigned URLs for manager review
    # from app.api.v1.endpoints.agent import _photos_from_visits
    # base["photos"] = _photos_from_visits(case.visits)

    base["photos"] = MediaService.photos_from_visits(case.visits)
    return base


# ---------------------------------------------------------------------------
# GET /manager/agents/performance  — per-agent monthly stats for all agents
# ---------------------------------------------------------------------------

@router.get("/agents/performance")
def agents_performance(
    current_user: ManagerOnly,
    db: DbSession,
    months: int = 6,
):
    my_agent_ids_perf = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    _, _, eff_today_perf = _effective_today(my_agent_ids_perf, db)
    on_leave_today_perf = agent_ids_on_leave(db, leave_today(), my_agent_ids_perf)
    # Arithmetically stepped so no month is skipped or duplicated — see
    # _recent_months. The old 30-day-timedelta loop could drop a month.
    month_list: list[str] = _recent_months(eff_today_perf, max(months, 1))

    my_agents = (
        db.query(Agent, User.full_name)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.manager_user_id == current_user.id)
        .order_by(Agent.ranking_score.desc())
        .all()
    )

    # 2026-08-21 — This endpoint used to read AgentPerformance for `monthly`,
    # and it was showing FABRICATED numbers on the Analytics chart. That table
    # is written once a month by take_monthly_snapshot; on a seeded box the rows
    # exist only because seed_data.py invented them
    # (`sim_visits * random.uniform(8000, 45000)`). For agent002 it claimed 538
    # visits across six months against 127 real ones, and ₹102.7L collected
    # against ₹6.7L actually received — while the headline tiles on the SAME
    # response showed the real figures. One endpoint, two sources, a 13x gap.
    #
    # Worse, the per-month target was `collected / rate` — one invented number
    # divided by another. That is the same "divide the figure by itself" defect
    # already fixed on the trend line and the agent gauge; it survived here.
    #
    # Now uses _live_monthly_metrics, the same helper behind GET /manager/agents
    # and GET /manager/analytics, so all three pages answer from one definition:
    # collected = VERIFIED payments dated in the month, target = the target of
    # the cases actually visited that month.
    perf_idx = _live_monthly_metrics(db, [a.Agent.id for a in my_agents], month_list)

    result = []
    for agent, full_name in my_agents:
        by_month = perf_idx.get(agent.id, {})
        monthly = []
        for m in month_list:
            p = by_month.get(m) or {}
            collected_val = float(p.get("collected", 0.0))
            target_val = float(p.get("target", 0.0))
            monthly.append({
                "month": m,
                "collected": round(collected_val, 2),
                "target": round(target_val, 2),
                "visits": int(p.get("visits", 0)),
                "ptps_set": int(p.get("ptps_set", 0)),
                "ptps_honored": int(p.get("ptps_honored", 0)),
                # Capture: did the visit secure a commitment? A different
                # question from ptps_honored, which asks whether the borrower
                # then kept it. Both travel, so the UI never has to guess.
                "visits_needing_promise": int(p.get("visits_needing_promise", 0)),
                "ptps_captured": int(p.get("ptps_captured", 0)),
                "ptp_capture_pct": p.get("ptp_capture_pct", 0.0),
                # Rate comes from the helper, which computes it the one way the
                # rest of the product does: collected / target, 0 when nothing
                # was visited. Never collected / collected.
                "collection_rate_pct": p.get("rate_pct", 0.0),
            })

        # Totals are the SUM OF THE MONTHS shown, not an all-time figure from
        # a different source. The headline and the chart under it now describe
        # the same window; previously they could not have agreed even in
        # principle, because one was six months of live data and the other was
        # every case ever, counting unverified money.
        totals = {
            "target": round(sum(r["target"] for r in monthly), 2),
            "collected": round(sum(r["collected"] for r in monthly), 2),
        }
        result.append({
            "agent_id": agent.id,
            "agent_name": full_name,
            "employee_code": agent.employee_code,
            "tier": agent.tier,
            "territory": agent.territory,
            "ranking_score": round(agent.ranking_score, 1),
            "status": effective_status(agent, on_leave_today_perf),
            "total_target": totals["target"],
            "total_collected": totals["collected"],
            "overall_rate_pct": round(totals["collected"] / totals["target"] * 100, 1) if totals["target"] else 0.0,
            "monthly": monthly,
        })

    return {
        "months": month_list,
        "agents": result,
    }


# ---------------------------------------------------------------------------
# GET /manager/ai/health
# ---------------------------------------------------------------------------
# Which provider and model are actually in use, whether a key is present, and a
# rolling count of outcomes per feature. Added 2026-08-19 because six AI
# features could fail four different ways and every one of them looked
# identical from the outside: a written-in answer, served silently.
#
# Never returns the key itself, only whether one is set.

@router.get("/ai/health")
def ai_health(current_user: ManagerOnly):
    return _llm.health()


# ---------------------------------------------------------------------------
# GET /manager/ml/health
# ---------------------------------------------------------------------------
# The same idea as /ai/health, for the trained models: which artifacts are
# loaded, what version, whether they passed their gates, and what they scored
# out-of-time. Added 2026-09-08 because a pickled model can fail in ways that
# are invisible from the outside — absent artifact, checksum mismatch, a
# scikit-learn version it was not fitted under — and every one of them would
# otherwise look identical to "the fallback scorecard is fine".
#
# Reports `scoring_enabled` separately from `n_loaded`: a model can be present,
# healthy and deliberately not in use, and those are different states.

@router.get("/ml/health")
def ml_health(db: DbSession, current_user: User = require_perm("ml.read")):
    from app.ml.pipeline.engine import DecisionEngine, health_all

    out = health_all()
    out["scoring_enabled"] = settings.ML_SCORING_ENABLED
    out["prediction_logging_enabled"] = settings.ML_LOG_PREDICTIONS
    out["configured_version"] = settings.ML_MODEL_VERSION
    out["auto_retrain_enabled"] = settings.ML_AUTO_RETRAIN_ENABLED

    # 2026-09-09 — LIVE MONITORING, alongside the artifact's development
    # metrics and never instead of them. `health_all()` reports what the model
    # scored WHEN IT WAS BUILT; `monitoring` below reports what it is scoring
    # NOW, on matured production outcomes. Merging them would be the worst
    # possible answer: a development Gini presented as a live one is exactly
    # the number somebody would act on.
    #
    # `status` is never "healthy" on absent evidence. `not_ready` and
    # `insufficient_outcome_variation` are their own states precisely so that a
    # model nobody can judge yet cannot read as a model that has been judged.
    out["monitoring"] = _ml_monitoring_block(db)

    # Which artifact THIS PROCESS has in memory against what the pointer says.
    # A promotion rewrites champion.txt and does not reach into a running
    # process's cache; with several API containers the fleet can be split
    # across two champions and every one of them would otherwise report itself
    # healthy. The answer is per-instance and says so.
    # `health_all()["models"]` is a LIST of per-model dicts, each with a
    # "model" key — not a mapping. Iterating it as a mapping handed a dict
    # where a model name belonged.
    out["serving"] = {
        m["model"]: DecisionEngine.serving_state(m["model"])
        for m in (out.get("models") or []) if m.get("model")
    }
    return out


def _ml_monitoring_block(db, model_name: str = "recovery_risk") -> dict:
    """The production monitoring state, in the shape a reader can act on.

    Read-only and defensive: a health endpoint that 500s because a diagnostic
    raised is worse than one that reports the diagnostic failed.
    """
    from app.ml.pipeline.monitor import (
        CALIBRATION_GAP_THRESHOLD, GINI_RELATIVE_DROP_THRESHOLD,
        KS_RELATIVE_DROP_THRESHOLD, BRIER_RELATIVE_RISE_THRESHOLD,
        MIN_MATURED_FOR_MONITORING, PSI_RETRAIN_THRESHOLD, monitor_model,
        readiness,
    )
    from app.models.model_candidate import ModelCandidate

    thresholds = {
        "gini_relative_drop": GINI_RELATIVE_DROP_THRESHOLD,
        "ks_relative_drop": KS_RELATIVE_DROP_THRESHOLD,
        "brier_relative_rise": BRIER_RELATIVE_RISE_THRESHOLD,
        "calibration_gap_abs": CALIBRATION_GAP_THRESHOLD,
        "psi": PSI_RETRAIN_THRESHOLD,
        "min_matured": MIN_MATURED_FOR_MONITORING,
    }
    try:
        gate = readiness(db, model_name)
    except Exception as exc:                                # pragma: no cover
        return {"status": "error", "error": type(exc).__name__, "detail": str(exc)}

    base = {
        "model": model_name,
        "model_version": gate.model_version,
        "outcome_definition_version": gate.outcome_definition_version,
        "n_matured": gate.n_matured,
        "required_matured": gate.required,
        "excluded_other_model_version": gate.n_excluded_other_model_version,
        "excluded_other_outcome_version": gate.n_excluded_other_outcome_version,
        "thresholds": thresholds,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if not gate.ready:
        # NOT a verdict about the model. Said in as many words, because
        # "not_ready" rendered beside a green tick is how a reader concludes
        # the opposite of what it means.
        return {**base, "status": "not_ready", "verdict": None,
                "retrain_recommended": False, "retrain_reasons": [],
                "n_classes": None, "bad_rate_live": None,
                "performance": {}, "stability": {},
                "note": ("fewer than the required matured outcomes on the "
                         "serving version and current outcome definition; no "
                         "conclusion about model health is available yet")}
    try:
        rep = monitor_model(db, model_name, version=gate.model_version,
                            outcome_definition_version=gate.outcome_definition_version)
    except Exception as exc:                                # pragma: no cover
        return {**base, "status": "error", "error": type(exc).__name__,
                "detail": str(exc)}

    variation = rep.outcome_variation or {}
    latest = (db.query(ModelCandidate)
              .filter(ModelCandidate.model_name == model_name)
              .order_by(ModelCandidate.created_at.desc()).first())
    return {
        **base,
        # `status` and `verdict` are the same word here on purpose: the monitor
        # already distinguishes healthy / retrain_recommended /
        # insufficient_outcome_variation / insufficient_data, and inventing a
        # second vocabulary for the endpoint would be two names for one thing.
        "status": rep.verdict,
        "verdict": rep.verdict,
        "retrain_recommended": rep.retrain_recommended,
        "retrain_reasons": rep.reasons,
        "n_predictions": rep.n_predictions,
        "n_matured_scored": rep.n_matured,
        "n_classes": variation.get("n_classes"),
        "bad_rate_live": variation.get("bad_rate_live")
                         or (rep.performance or {}).get("bad_rate_live"),
        "performance": rep.performance,
        "stability": rep.stability,
        "excluded": rep.excluded,
        "latest_candidate": latest.to_dict() if latest else None,
    }


# ---------------------------------------------------------------------------
# The retraining lifecycle — 2026-09-09
# ---------------------------------------------------------------------------
# The 2026-09-09 audit found that a retrain recommendation had no consumer and
# a candidate had no surface: the whole lifecycle lived in worker logs. These
# five routes are the durable report AND the approval gate.
#
# NOTHING HERE CAN PROMOTE WITHOUT A PERSON. `/approve` records a judgement,
# `/promote` acts on it, and both are authenticated manager routes that refuse
# a candidate which did not pass validation and beat the incumbent. There is no
# code path from the nightly job to `champion.txt`.

@router.get("/ml/candidates")
def ml_candidates(db: DbSession, model: str = "recovery_risk", limit: int = 25,
                  state: Optional[str] = None,
                  current_user: User = require_perm("ml.read")):
    """Every retraining attempt, newest first — the durable retraining report.

    Deliberately NOT tenant-scoped: a model is one global object, not a
    manager's own data, and hiding another manager's view of the champion would
    make two people disagree about which model is live. It is manager-only.
    """
    from app.models.model_candidate import ModelCandidate

    q = db.query(ModelCandidate).filter(ModelCandidate.model_name == model)
    if state:
        q = q.filter(ModelCandidate.state == state.upper())
    rows = q.order_by(ModelCandidate.created_at.desc()).limit(min(limit, 100)).all()
    return {"model": model, "count": len(rows),
            "candidates": [c.to_dict() for c in rows]}


@router.get("/ml/candidates/{candidate_id}")
def ml_candidate_detail(candidate_id: UUIDPath, db: DbSession,
                        current_user: User = require_perm("ml.read")):
    from app.ml.pipeline import registry
    from app.models.model_candidate import ModelCandidate

    cand = (db.query(ModelCandidate)
            .filter(ModelCandidate.id == candidate_id).first())
    if cand is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    out = cand.to_dict()
    # The incumbent AS OF NOW, beside the one the candidate was compared
    # against. A reader approving a candidate needs to see when those differ,
    # because that is exactly the case `approve` will refuse.
    out["current_champion"] = registry.pointer_version(cand.model_name)
    out["is_stale"] = out["current_champion"] != cand.incumbent_version
    return out


@router.post("/ml/candidates/{candidate_id}/approve")
def ml_approve_candidate(candidate_id: UUIDPath, db: DbSession, note: Optional[str] = None,
                         current_user: User = require_perm("ml.approve")):
    """Record a person's decision to accept the challenger. Does NOT promote."""
    from app.ml.pipeline.lifecycle import ApprovalRefused, approve

    try:
        cand = approve(db, candidate_id, user_id=current_user.id, note=note)
    except ApprovalRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=404, detail="Candidate not found")

    db.add(AuditLog(
        created_at=datetime.now(timezone.utc), user_id=current_user.id,
        action=AuditAction.MODEL_CANDIDATE_APPROVED,
        entity_type="ModelCandidate", entity_id=cand.id,
        details={"model": cand.model_name, "version": cand.candidate_version,
                 "incumbent": cand.incumbent_version,
                 "gini_uplift": cand.gini_uplift, "note": note},
        success=True))
    db.commit()
    return cand.to_dict()


@router.post("/ml/candidates/{candidate_id}/reject")
def ml_reject_candidate(candidate_id: UUIDPath, db: DbSession, note: Optional[str] = None,
                        current_user: User = require_perm("ml.approve")):
    from app.ml.pipeline.lifecycle import ApprovalRefused, reject

    try:
        cand = reject(db, candidate_id, user_id=current_user.id, note=note)
    except ApprovalRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=404, detail="Candidate not found")

    db.add(AuditLog(
        created_at=datetime.now(timezone.utc), user_id=current_user.id,
        action=AuditAction.MODEL_CANDIDATE_REJECTED,
        entity_type="ModelCandidate", entity_id=cand.id,
        details={"model": cand.model_name, "version": cand.candidate_version,
                 "note": note},
        success=True))
    db.commit()
    return cand.to_dict()


@router.post("/ml/candidates/{candidate_id}/promote")
def ml_promote_candidate(candidate_id: UUIDPath, db: DbSession,
                         current_user: User = require_perm("ml.promote")):
    """The one write in this codebase that changes what borrowers are scored by.

    Separate from `/approve` on purpose: approval records a judgement, promotion
    acts on it, and time passes in between. Promotion re-checks that the
    champion has not moved since the candidate was compared against it.
    """
    from app.ml.pipeline.lifecycle import ApprovalRefused, promote

    try:
        cand = promote(db, candidate_id, user_id=current_user.id)
    except ApprovalRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=404, detail="Candidate not found")

    db.add(AuditLog(
        created_at=datetime.now(timezone.utc), user_id=current_user.id,
        action=AuditAction.MODEL_PROMOTED,
        entity_type="ModelCandidate", entity_id=cand.id,
        details={"model": cand.model_name, "to": cand.candidate_version,
                 "from": cand.promoted_from_version,
                 "gini_uplift": cand.gini_uplift},
        success=True))
    db.commit()
    return cand.to_dict()


# ---------------------------------------------------------------------------
# GET /manager/fraud-alerts
# ---------------------------------------------------------------------------
# Anomalies in field-visit evidence the app already collects. See
# services/fraud_service.py for what each check means and why the thresholds
# are set where they are.

@router.get("/fraud-alerts")
def fraud_alerts(
    current_user: ManagerOnly,
    db: DbSession,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_dismissed: bool = False,
):
    from app.services.fraud_service import FraudService

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]

    def _parse(raw: Optional[str], label: str):
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail=f"{label} must be YYYY-MM-DD")

    return FraudService(db).scan(
        my_agent_ids,
        date_from=_parse(date_from, "date_from"),
        date_to=_parse(date_to, "date_to"),
        include_dismissed=include_dismissed,
    )


# ---------------------------------------------------------------------------
# POST /manager/fraud-alerts/review
# ---------------------------------------------------------------------------
# A manager's verdict on one anomaly. Confirmed findings stay visible; dismissed
# ones drop out of the default view but are never deleted — a dismissal is
# itself a decision someone may need to answer for later.
#
# Writes an AuditLog row. This is the first place in the codebase that does:
# AuditLog declares 21 action types and only three were ever emitted. A judgement
# about an agent's conduct is exactly the kind of act the table exists for.

# _BM is declared further down this file, next to the status endpoint; import
# the base directly rather than move an existing definition.
from pydantic import BaseModel as _ReviewBase


class _ReviewBody(_ReviewBase):
    visit_id: UUIDStr
    finding_type: str
    verdict: str                       # CONFIRMED | DISMISSED
    note: Optional[str] = None


@router.post("/fraud-alerts/review")
def review_fraud_alert(body: _ReviewBody, current_user: ManagerOnly, db: DbSession):
    from app.models.audit_log import AuditAction, AuditLog
    from app.models.fraud_review import FraudReview, ReviewVerdict
    from app.models.visit import Visit as VisitModel

    verdict = (body.verdict or "").strip().upper()
    if verdict not in {v.value for v in ReviewVerdict}:
        raise HTTPException(status_code=422, detail="verdict must be CONFIRMED or DISMISSED")

    # Ownership is proved through the visit's agent, so a manager cannot record
    # a verdict about someone else's team.
    visit = (
        db.query(VisitModel)
        .join(Agent, Agent.id == VisitModel.agent_id)
        .filter(VisitModel.id == body.visit_id, Agent.manager_user_id == current_user.id)
        .first()
    )
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")

    existing = (
        db.query(FraudReview)
        .filter(FraudReview.visit_id == body.visit_id,
                FraudReview.finding_type == body.finding_type)
        .first()
    )
    previous = existing.verdict.value if existing else None
    if existing:
        existing.verdict = ReviewVerdict(verdict)
        existing.note = body.note
        existing.reviewed_by_user_id = current_user.id
        existing.reviewed_at = datetime.now(timezone.utc)
        review = existing
    else:
        review = FraudReview(
            visit_id=body.visit_id, finding_type=body.finding_type,
            agent_id=visit.agent_id, verdict=ReviewVerdict(verdict),
            note=body.note, reviewed_by_user_id=current_user.id,
        )
        db.add(review)

    db.add(AuditLog(
        created_at=datetime.now(timezone.utc),
        user_id=current_user.id,
        action=AuditAction.ANOMALY_REVIEWED,
        entity_type="visit",
        entity_id=body.visit_id,
        details={"finding_type": body.finding_type, "agent_id": visit.agent_id,
                 "note": body.note},
        old_values={"verdict": previous} if previous else None,
        new_values={"verdict": verdict},
        success=True,
    ))
    db.commit()

    return {
        "visit_id": body.visit_id,
        "finding_type": body.finding_type,
        "verdict": verdict,
        "note": review.note,
        "previous_verdict": previous,
    }


# ---------------------------------------------------------------------------
# GET /manager/audit-log   — the real audit trail
# ---------------------------------------------------------------------------
# 2026-09-06. The Compliance page carried a panel headed "Today's Audit Log"
# that rendered SIX HARDCODED ROWS — agent names absent from the roster
# ("Amit Singh", "Neha Gupta"), a PTP committed for "Jan 25, 2025", invented
# case numbers, and an Export button wired to nothing. On an RBI Fair Practices
# screen. It was removed rather than reworked, because no endpoint exposed the
# real table. This is that endpoint.
#
# `audit_logs` is genuinely populated — the model is well designed and
# deliberately carries no TimestampMixin because rows must be immutable.

_AUDIT_WINDOW_DAYS = 7
_AUDIT_PAGE_SIZE = 50
_AUDIT_MAX_PAGE_SIZE = 200

# Actions DECLARED on AuditAction that NO call site writes. Maintained by hand,
# because "never emitted" is a property of the CODE, not of the data: an action
# missing from a given window may simply have been quiet, and the difference is
# exactly what an auditor needs to know. A short log that does not say which of
# these two it is, is worse than no log.
#
# Re-derive with:
#   grep -rn "AuditAction\.[A-Z_]*" backend/app --include=*.py \
#       | grep -v models/audit_log.py
_AUDIT_ACTIONS_NOT_INSTRUMENTED = [
    "CASE_ASSIGNED", "CASE_UPDATED", "VISIT_RECORDED", "PAYMENT_SUBMITTED",
    "PTP_SET", "DOCUMENT_UPLOADED", "SOS_TRIGGERED", "SOS_RESOLVED",
    "BEAT_GENERATED", "BEAT_MODIFIED", "AGENT_STATUS_CHANGED",
    "CONTACT_HOUR_VIOLATION_ATTEMPT", "ROLE_VIOLATION_ATTEMPT", "DATA_EXPORT",
]


def _audit_visible_user_ids(db, current_user) -> list[str]:
    """The user ids whose actions this manager may read: their own team's
    agents, plus themselves.

    ONE definition, shared by the list endpoint and the CSV export. Writing the
    scope twice is precisely how GET /manager/allocation/export-decisions came
    to be exportable across tenants — the endpoint mentioned current_user.id
    while the service it delegated to quietly did not use it.
    """
    agent_user_ids = [
        r[0] for r in
        db.query(Agent.user_id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    return [current_user.id, *agent_user_ids]


def _audit_log_query(db, current_user, since: datetime):
    """Scoped, time-bounded, newest first.

    NOTE ON ACTOR-LESS ROWS: `AuditLog.user_id` is nullable, and some rows are
    written by the system rather than a person — PTP_UPDATED when a verified
    payment honours a promise names no user, deliberately ("nobody did this; a
    verified payment did"). SQL `user_id IN (...)` excludes NULL by definition,
    so those rows do not appear here. That is the correct default — an
    unattributed row cannot be proven to belong to this tenant — but it is a
    real gap, so the response says so rather than leaving it to be discovered.
    """
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.user_id.in_(_audit_visible_user_ids(db, current_user)),
            AuditLog.created_at >= since,
        )
        .order_by(AuditLog.created_at.desc())
    )


def _audit_since() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=_AUDIT_WINDOW_DAYS)


@router.get("/audit-log")
def audit_log(
    current_user: ManagerOnly,
    db: DbSession,
    limit: int = _AUDIT_PAGE_SIZE,
    offset: int = 0,
):
    """The last 7 days of recorded actions for this manager's team.

    Paginated deliberately: `audit_logs` has no retention sweep anywhere in the
    codebase, so it grows without bound and an unbounded query here would get
    slower for the life of the deployment.
    """
    limit = max(1, min(limit, _AUDIT_MAX_PAGE_SIZE))
    offset = max(0, offset)
    since = _audit_since()

    base = _audit_log_query(db, current_user, since)
    total = base.count()
    rows = base.offset(offset).limit(limit).all()

    # Actor names in one query, not one per row.
    actor_ids = {r.user_id for r in rows if r.user_id}
    names: dict[str, str] = {}
    if actor_ids:
        names = {
            uid: full_name for uid, full_name in
            db.query(User.id, User.full_name).filter(User.id.in_(actor_ids)).all()
        }

    # What actually appears in the window, so the UI can distinguish "quiet"
    # from "not instrumented" without hardcoding either list itself.
    present = dict(
        db.query(AuditLog.action, func.count(AuditLog.id))
        .filter(
            AuditLog.user_id.in_(_audit_visible_user_ids(db, current_user)),
            AuditLog.created_at >= since,
        )
        .group_by(AuditLog.action)
        .all()
    )

    return {
        "window_days": _AUDIT_WINDOW_DAYS,
        "since": since.isoformat(),
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "action": r.action.value if hasattr(r.action, "value") else str(r.action),
                "actor_name": names.get(r.user_id) if r.user_id else None,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "success": r.success,
                "failure_reason": r.failure_reason,
                "ip_address": r.ip_address,
                "details": r.details,
            }
            for r in rows
        ],
        "counts_by_action": {
            (k.value if hasattr(k, "value") else str(k)): v for k, v in present.items()
        },
        # The honesty block. Without it a short log reads as a quiet week.
        "coverage": {
            "declared_action_types": len(AuditAction),
            "not_instrumented": _AUDIT_ACTIONS_NOT_INSTRUMENTED,
            "excludes_system_rows": True,
            "note": (
                "Actions with no recorded actor (written by the system rather "
                "than a person) are not shown, because an unattributed row "
                "cannot be scoped to a team. Immutability is enforced by "
                "convention only — there is no database trigger and no revoked "
                "UPDATE/DELETE grant."
            ),
        },
    }


@router.get("/audit-log/export")
def export_audit_log_csv(current_user: ManagerOnly, db: DbSession):
    """The same scoped window as GET /manager/audit-log, as CSV.

    Reuses `_audit_log_query`, so the tenant scope cannot be present on one path
    and missing on the other.
    """
    import csv
    import io

    from fastapi.responses import Response

    rows = _audit_log_query(db, current_user, _audit_since()).all()

    actor_ids = {r.user_id for r in rows if r.user_id}
    names: dict[str, str] = {}
    if actor_ids:
        names = {
            uid: full_name for uid, full_name in
            db.query(User.id, User.full_name).filter(User.id.in_(actor_ids)).all()
        }

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(csv_row(["timestamp", "actor", "action", "entity_type", "entity_id",
                             "success", "failure_reason", "ip_address"]))
    for r in rows:
        # csv_row: an actor's name is typed by a person, and a spreadsheet
        # treats a cell starting with = + - @ as a FORMULA, not as text.
        writer.writerow(csv_row([
            r.created_at.isoformat() if r.created_at else "",
            names.get(r.user_id, "") if r.user_id else "",
            r.action.value if hasattr(r.action, "value") else str(r.action),
            r.entity_type or "", r.entity_id or "",
            "yes" if r.success else "no", r.failure_reason or "", r.ip_address or "",
        ]))

    stamp = date.today().isoformat()

    # 2026-09-10 — AN EXPORT OF THE AUDIT TRAIL WAS ITSELF UNAUDITED.
    # `DATA_EXPORT` has been declared in AuditAction since the table was written
    # and was emitted nowhere; the repo-wide audit found 14 of 25 actions in that
    # state, and this is the one that ships borrower-adjacent history off the
    # platform. Row count and window are recorded rather than the content: the
    # export is reproducible from them, and copying the payload into the audit
    # table would duplicate the very data the export is being logged for.
    db.add(AuditLog(
        created_at=datetime.now(timezone.utc), user_id=current_user.id,
        action=AuditAction.DATA_EXPORT,
        entity_type="AuditLog", entity_id=None,
        details={"format": "csv", "rows": len(rows),
                 "since": _audit_since().isoformat(),
                 "endpoint": "/manager/audit-log/export"},
        success=True))
    db.commit()

    return Response(
        content=out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=audit_log_{stamp}.csv"},
    )


# ---------------------------------------------------------------------------
# GET /manager/compliance
# ---------------------------------------------------------------------------

def _audit_action_coverage(db) -> dict:
    """Declared audit action types versus those EVER RECORDED IN THIS DATABASE.

    This is an observed-data figure and it is named as one. It is NOT
    implementation coverage, and the two differ: on 2026-09-11 a source scan
    found 13 of 25 action types written somewhere in app/, while this
    database had ever recorded 6. Both numbers are true; they answer different
    questions.

    Why the observed figure and not the wired one. "Wired" cannot be derived
    reliably at runtime: the only method is scanning source for
    `AuditAction.X` references, and a reference is not a write — this very
    endpoint reads CONTACT_HOUR_VIOLATION_ATTEMPT in a filter without writing
    it. A heuristic dressed up as coverage is how the page came to say
    "8 of 22 written" for five days after it was 13 of 25. The observed count
    is exact, cheap, cannot drift, and is a strict lower bound on wired: an
    action that is wired and has never fired reads as never recorded, which
    is the honest direction to err in. The page says "recorded", never
    "implemented".

    SYSTEM-WIDE, unlike every other figure on this page: which action types
    this deployment has ever produced is a property of the deployment, and a
    tenant-scoped count would read low for a quiet team. Only action NAMES
    leave this function — never a row, never a tenant's content.
    """
    declared = [a.name for a in AuditAction]
    recorded = {
        (r[0].name if hasattr(r[0], "name") else str(r[0]))
        for r in db.query(AuditLog.action).distinct().all()
    }
    return {
        "declared": len(declared),
        "ever_recorded": sum(1 for a in declared if a in recorded),
        "never_recorded": sorted(a for a in declared if a not in recorded),
        # Carried on the wire so no consumer can read this as implementation
        # coverage by accident.
        "semantics": "observed in this database; not implementation coverage",
    }


@router.get("/compliance")
def compliance_metrics(current_user: ManagerOnly, db: DbSession):
    """Month-to-date, this manager's team.

    ON out_of_hours_visits AND compliance_rate. They count STORED visits whose
    within_contact_hours flag is False. Through the real API that can only be
    zero: record_visit refuses an out-of-hours attempt with 403 and stores
    nothing, so the flag is False only on rows written by seed or demo
    scripts. Both fields are kept — they are a data-integrity signal for a
    seeded book and other callers may read them — but the page no longer
    presents them as a compliance measurement. The measurement is
    blocked_contact_attempts: CONTACT_HOUR_VIOLATION_ATTEMPT rows, one per
    refused visit, same month, same team.
    """
    # Scoped to this manager's own agents, like every other figure in this
    # router — compliance numbers are per-team, not company-wide.
    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]

    start_of_month = date.today().replace(day=1)
    start_of_month_dt = datetime.combine(start_of_month, datetime.min.time()).replace(tzinfo=timezone.utc)

    # Refused out-of-hours visit attempts, attributed to the agent's user id —
    # which is exactly the scope _audit_visible_user_ids gives the audit panel,
    # so the tile and the trail cannot disagree about whose attempts these are.
    blocked_attempts = (
        db.query(func.count(AuditLog.id))
        .filter(
            AuditLog.action == AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT,
            AuditLog.user_id.in_(_audit_visible_user_ids(db, current_user)),
            AuditLog.created_at >= start_of_month_dt,
        )
        .scalar() or 0
    )
    audit_actions = _audit_action_coverage(db)

    if not my_agent_ids:
        return {
            "month": date.today().strftime("%Y-%m"),
            "total_visits": 0,
            "out_of_hours_visits": 0,
            "blocked_contact_attempts": int(blocked_attempts),
            "geo_violations": 0,
            "sos_active_count": 0,
            "compliance_rate": 1.0,
            "geo_verification_rate": 1.0,
            "audit_actions": audit_actions,
        }

    stats = (
        db.query(
            func.count(Visit.id).label("total_visits"),
            func.coalesce(func.sum(sa_case((Visit.within_contact_hours == False, 1), else_=0)), 0).label("out_of_hours"),
            func.coalesce(func.sum(sa_case((Visit.geo_verified == False, 1), else_=0)), 0).label("geo_violations"),
        )
        .filter(Visit.agent_id.in_(my_agent_ids), Visit.check_in_time >= start_of_month_dt)
        .one()
    )
    total_visits = stats.total_visits or 0
    out_of_hours = int(stats.out_of_hours or 0)
    geo_violations = int(stats.geo_violations or 0)

    sos_active = (
        db.query(func.count(Agent.id))
        .filter(Agent.id.in_(my_agent_ids), Agent.sos_active == True).scalar() or 0  # noqa: E712
    )

    return {
        "month": date.today().strftime("%Y-%m"),
        "total_visits": total_visits,
        "out_of_hours_visits": out_of_hours,
        "blocked_contact_attempts": int(blocked_attempts),
        "geo_violations": geo_violations,
        "sos_active_count": sos_active,
        "compliance_rate": round(1 - (out_of_hours / total_visits), 4) if total_visits > 0 else 1.0,
        "geo_verification_rate": round(1 - (geo_violations / total_visits), 4) if total_visits > 0 else 1.0,
        "audit_actions": audit_actions,
    }


# ---------------------------------------------------------------------------
# GET /manager/analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
def analytics(current_user: ManagerOnly, db: DbSession):
    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    _, _, today = _effective_today(my_agent_ids, db)

    # 6-month window — arithmetically stepped via _recent_months; the old
    # 30-day-timedelta loop could land two rows in one month and skip another.
    months_ordered: list[str] = _recent_months(today, 6)

    # Monthly collection trend — live, from the transactional tables.
    #
    # Was read from AgentPerformance, whose total_collected the seed fills with
    # `sim_visits * random.uniform(8000, 45000) * tier_mult`. The target line was
    # worse: `target = collected / avg_rate`, i.e. one random number divided by
    # another, with `collected * 1.4` as the fallback when the divisor was zero.
    #
    # That is also why this page used to disagree with itself: the header KPI
    # summed these figures to ~₹1370L while the DPD card below it, which reads
    # real Case rows, showed ~₹106L — a 13x gap on one screen.
    #
    # Same helper, same definitions as GET /manager/agents, so the two pages now
    # agree by construction.
    team_metrics = _live_monthly_metrics(db, my_agent_ids, months_ordered)

    monthly_trend = []
    for m in months_ordered:
        collected = sum((team_metrics.get(a, {}).get(m, {}) or {}).get("collected", 0.0) for a in my_agent_ids)
        target = sum((team_metrics.get(a, {}).get(m, {}) or {}).get("target", 0.0) for a in my_agent_ids)
        visits = sum((team_metrics.get(a, {}).get(m, {}) or {}).get("visits", 0) for a in my_agent_ids)
        # Capture is summed as counts and divided once, NOT averaged across
        # agents: an agent who made three visits must not move the team line as
        # far as one who made ninety. Same reasoning as the collection rate below.
        cap_num = sum((team_metrics.get(a, {}).get(m, {}) or {}).get("ptps_captured", 0) for a in my_agent_ids)
        cap_den = sum((team_metrics.get(a, {}).get(m, {}) or {}).get("visits_needing_promise", 0) for a in my_agent_ids)
        monthly_trend.append({
            "month": m,
            "collected_lakhs": round(collected / 100000, 2),
            "target_lakhs": round(target / 100000, 2),
            "total_visits": int(visits),
            # Team rate is collected/target over the whole team, not the mean of
            # per-agent rates: an agent who visited one small case must not swing
            # the team line as hard as one who worked forty.
            "collection_rate_pct": round(collected / target * 100, 1) if target > 0 else 0.0,
            "visits_needing_promise": int(cap_den),
            "ptps_captured": int(cap_num),
            "ptp_capture_pct": round(cap_num / cap_den * 100, 1) if cap_den > 0 else 0.0,
        })

    # DPD bucket breakdown — cases for manager's agents
    bucket_rows = (
        db.query(
            Case.agent_id,
            Case.target_amount,
            Case.collected_amount,
            Case.status,
        )
        .filter(Case.agent_id.in_(my_agent_ids))
        .all()
    )
    # Need to join with loan for DPD bucket
    from app.models.loan import Loan
    loan_bucket_rows = (
        db.query(
            Loan.dpd_bucket,
            func.count(Case.id).label("case_count"),
            func.sum(Case.target_amount).label("target"),
            func.sum(Case.collected_amount).label("collected"),
        )
        .join(Case, Case.loan_id == Loan.id)
        .filter(Case.agent_id.in_(my_agent_ids))
        .group_by(Loan.dpd_bucket)
        .all()
    )
    dpd_breakdown = []
    for r in loan_bucket_rows:
        target = float(r.target or 0)
        collected = float(r.collected or 0)
        rate = round(collected / target * 100, 1) if target > 0 else 0.0
        dpd_breakdown.append({
            "bucket": r.dpd_bucket,
            "case_count": r.case_count,
            "target_lakhs": round(target / 100000, 2),
            "collected_lakhs": round(collected / 100000, 2),
            "collection_rate_pct": rate,
        })

    # Agent leaderboard for current month — live figures, ranked by what the
    # agent actually collected this month rather than by AgentPerformance's
    # ranking_score. That score is written only by take_monthly_snapshot
    # (crontab day_of_month=1) and the seed derives it from the same random
    # collection_rate, so ranking by it put Tier-1-by-random above genuinely
    # better performers — the Field Agents page and this one disagreed on who
    # the top agent was.
    #
    # ranking_score and tier are still REPORTED (both are in the response shape
    # this page and Command Center read) but they are no longer what the list is
    # sorted by. They remain stale until the snapshot job runs; that is a
    # separate fix on the Agent model, not something this endpoint can do.
    current_month = today.strftime("%Y-%m")
    lb_name_map: dict[str, str] = {}
    if my_agent_ids:
        lb_name_map = {
            r[0]: r[1]
            for r in db.query(Agent.id, User.full_name)
            .join(User, Agent.user_id == User.id)
            .filter(Agent.id.in_(my_agent_ids))
            .all()
        }
    agent_meta = {
        a.id: a for a in db.query(Agent).filter(Agent.id.in_(my_agent_ids)).all()
    } if my_agent_ids else {}

    leaderboard_all = []
    for aid in my_agent_ids:
        m = (team_metrics.get(aid, {}).get(current_month) or
             {"visits": 0, "collected": 0.0, "target": 0.0, "ptps_set": 0,
              "ptps_honored": 0, "rate_pct": 0.0,
              "visits_needing_promise": 0, "ptps_captured": 0,
              "ptp_capture_pct": 0.0})
        a = agent_meta.get(aid)
        leaderboard_all.append({
            "agent_id": aid,
            "agent_name": lb_name_map.get(aid, "Unknown"),
            "total_collected": round(m["collected"], 2),
            "total_visits": int(m["visits"]),
            "ptps_set": int(m["ptps_set"]),
            "ptps_honored": int(m["ptps_honored"]),
            "ptps_captured": int(m["ptps_captured"]),
            "ptp_capture_pct": m["ptp_capture_pct"],
            "collection_rate_pct": m["rate_pct"],
            "ranking_score": round(float(a.ranking_score or 0), 1) if a else 0.0,
            "tier": a.tier if a else "TIER_3",
        })
    # Collected first, rate as the tie-break: two agents on the same rupees are
    # not equal if one needed twice the target to get there.
    leaderboard_all.sort(key=lambda r: (-r["total_collected"], -r["collection_rate_pct"]))
    leaderboard = leaderboard_all[:10]

    # Duty summary: ON_DUTY / OFF_DUTY / ON_LEAVE for TODAY (leave_today(), the
    # real IST calendar day — not `today`/eff_date above, which anchors the
    # money and case figures on this page to whichever day the book last had
    # data for and would silently misreport leave on a paused book), with
    # leave read from approved requests (not only the stored status).
    _on_leave_today = agent_ids_on_leave(db, leave_today(), my_agent_ids)
    _eff = [effective_status(a, _on_leave_today)
            for a in db.query(Agent).filter(Agent.id.in_(my_agent_ids)).all()]
    on_duty_count = sum(1 for st in _eff if st is AgentStatus.ON_DUTY)
    on_leave_count = sum(1 for st in _eff if st is AgentStatus.ON_LEAVE)
    off_duty_count = len(_eff) - on_duty_count - on_leave_count
    leave_summary: dict = {"ON_DUTY": on_duty_count, "OFF_DUTY": off_duty_count, "ON_LEAVE": on_leave_count}
    total_leave_days = on_leave_count  # agents on approved leave today

    # Overall KPIs (all-time for manager's agents)
    overall_target = (
        db.query(func.sum(Case.target_amount))
        .filter(Case.agent_id.in_(my_agent_ids))
        .scalar() or 0.0
    )
    overall_collected = (
        db.query(func.sum(Case.collected_amount))
        .filter(Case.agent_id.in_(my_agent_ids))
        .scalar() or 0.0
    )
    # PTP conversion, counted off the PTP table rather than summed out of
    # AgentPerformance. The snapshot columns are seeded
    # (`sim_ptps_set = sim_visits * random.uniform(0.10, 0.30)`), and summing
    # every archived month also double-counts an agent's PTPs once per month row.
    #
    # 2026-08-25: the same maturity rule as _live_monthly_metrics, and the
    # reason is that these two figures share ONE CARD. The UI shows the selected
    # month's rate when a month is picked and falls back to this all-time figure
    # when none is, so clicking a month must change the PERIOD and nothing else.
    # Without the filter it changed the definition too: this counted all 457
    # PTPs including 129 dated in the future, reading 23.0% against the monthly
    # basis of 31.7%. A card whose number means something different depending on
    # what you clicked is worse than a card with no number.
    #
    # 2026-08-26: the NUMERATOR now comes from _ptp_kept too, for the same
    # reason. The maturity rule was shared but the kept rule was not, so when
    # the monthly path started accepting PARTIALLY_HONORED and payment evidence
    # this one kept counting HONORED alone — 29.0% here against 61.1% there, on
    # the same team and the same months. Both halves of the fraction have to
    # come from one place or this recurs a third time.
    _ptp_matured = PTP.committed_date <= date.today()
    total_ptps_set = (
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids), _ptp_matured)
        .scalar() or 0
    ) if my_agent_ids else 0
    total_ptps_honored = (
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids), _ptp_matured, _ptp_kept(db))
        .scalar() or 0
    ) if my_agent_ids else 0

    # Window totals for the capture KPI, taken from the trend rows that were
    # just built rather than re-queried — so the headline and the per-month
    # figures are arithmetically the same measurement.
    _cap_num = sum(t["ptps_captured"] for t in monthly_trend)
    _cap_den = sum(t["visits_needing_promise"] for t in monthly_trend)

    # Current month visits per agent (avg) — from the live month already
    # computed above, so it cannot drift from the trend chart's last point.
    cm_visits = sum(
        (team_metrics.get(a, {}).get(current_month, {}) or {}).get("visits", 0)
        for a in my_agent_ids
    )
    n_agents = len(my_agent_ids) or 1

    # ── Recovery potential across the open book ──────────────────────────────
    # Scoped to this manager's own agents, like every other figure here.
    #
    # Counts AND money. A count of HIGH cases says how much work there is; the
    # summed expected recoverable amount says how much it is worth, and those two
    # routinely disagree — a handful of large secured loans can outweigh a long
    # tail of small ones. Reporting only the count is what leaves a team working
    # the tail.
    #
    # The amount is derived at read time from the LIVE balance and the stored
    # rate, through the scorecard's own multiplication. It is deliberately not a
    # stored column: "where is the recoverable money now" wants today's
    # outstanding, and a rate cannot be summed while an amount can.
    # TWO figures per band, and the pairing is the point. `arrears_and_penal` is
    # a ledger fact — instalments missed plus penalties. The 90-day recovery
    # estimate is rate_90 x total outstanding, which includes principal not yet
    # due. On the 2026-08-24 dry run the two RANKED THE BANDS DIFFERENTLY: HIGH
    # led on the estimate (Rs 10.36 Cr vs Rs 8.98 Cr) while MEDIUM led on what is
    # actually collectable (Rs 3.72 Cr vs Rs 2.16 Cr), because HIGH loans are
    # secured, long-tenor and barely in arrears. Showing only one of them points
    # a team at the wrong pile, so Analytics carries both.
    open_cases = (
        db.query(Case.loan_id, Loan.total_outstanding,
                 Loan.overdue_amount, Loan.penal_charges)
        .join(Loan, Case.loan_id == Loan.id)
        .filter(Case.agent_id.in_(my_agent_ids),
                Case.status.notin_([CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF]))
        .all()
    )
    outstanding_by_loan = {r[0]: float(r[1] or 0.0) for r in open_cases}
    arrears_by_loan = {r[0]: float(r[2] or 0.0) + float(r[3] or 0.0)
                       for r in open_cases}
    labels = _latest_recovery_by_loan(db, list(outstanding_by_loan))

    recovery_breakdown = []
    for band in ("HIGH", "MEDIUM", "LOW"):
        loans = [lid for lid, lab in labels.items()
                 if lab["recovery_potential"] == band]
        recovery_breakdown.append({
            "band": band,
            "cases": len(loans),
            # The fact. Named for what it is — the sum of the two ledger
            # columns. It was "due_now" until 2026-08-27; that phrase was
            # removed product-wide for overstating what a figure licenses.
            "arrears_and_penal": round(sum(arrears_by_loan.get(lid, 0.0)
                                           for lid in loans), 2),
            # The estimate. Field name unchanged on the wire; the UI renders it
            # as "90-day recovery estimate" with the denominator spelled out.
            "expected_recoverable_amount": round(sum(
                _recovery_expected_amount(labels[lid]["rate_90"] or 0.0,
                                          outstanding_by_loan.get(lid, 0.0))
                for lid in loans), 2),
            "total_outstanding": round(sum(
                outstanding_by_loan.get(lid, 0.0) for lid in loans), 2),
        })

    # Loans with no label yet are reported rather than folded into LOW. An
    # unscored loan is unknown, and quietly banding it as the worst case writes
    # off money nobody has looked at.
    unscored = [lid for lid in outstanding_by_loan if lid not in labels]

    return {
        "monthly_trend": monthly_trend,
        "dpd_breakdown": dpd_breakdown,
        "recovery_breakdown": recovery_breakdown,
        "recovery_summary": {
            "scored_cases": len(labels),
            "unscored_cases": len(unscored),
            # Every open case, scored or not — arrears are a fact and do not wait
            # on the scorecard, so this total is deliberately wider than the
            # per-band rows, which cover scored loans only.
            "arrears_and_penal": round(sum(arrears_by_loan.values()), 2),
            "expected_recoverable_amount": round(
                sum(b["expected_recoverable_amount"] for b in recovery_breakdown), 2),
            "label_horizon_days": settings.RECOVERY_LABEL_HORIZON_DAYS,
            # A hand-weighted scorecard, not a model. No accuracy figure is sent
            # because none exists.
            "is_modelled": False,
        },
        "leaderboard": leaderboard,
        "leave_summary": {
            "total_leave_days_30d": total_leave_days,
            "by_type": leave_summary,
        },
        "kpis": {
            "overall_collection_rate_pct": round(float(overall_collected) / float(overall_target) * 100, 1) if overall_target else 0.0,
            "total_collected_lakhs": round(float(overall_collected) / 100000, 2),
            "total_target_lakhs": round(float(overall_target) / 100000, 2),
            # TWO PTP NUMBERS, deliberately, because they answer different
            # questions and fail independently:
            #   capture    — did the visit secure a commitment? Grades the AGENT
            #                at the door. Known the same day, so it is the
            #                figure a manager can act on now.
            #   conversion — was the commitment then kept? Grades the
            #                BORROWER's follow-through, and is not knowable for
            #                up to a month.
            # High capture with low conversion means the team is taking soft
            # promises to close visits. Low capture with high conversion means
            # they only commit the reliable ones. Reporting one number would
            # hide whichever failure is actually happening.
            "ptp_capture_rate_pct": round(_cap_num / _cap_den * 100, 1) if _cap_den else 0.0,
            "ptp_conversion_rate_pct": round(float(total_ptps_honored) / float(total_ptps_set) * 100, 1) if total_ptps_set else 0.0,
            "avg_visits_per_agent_current_month": round(cm_visits / n_agents, 1),
        },
    }


# ---------------------------------------------------------------------------
# Team-level daily attendance calendar
# ---------------------------------------------------------------------------

@router.get("/analytics/team-attendance")
def get_team_attendance(
    current_user: ManagerOnly,
    db: DbSession,
    month: Optional[str] = None,  # YYYY-MM; defaults to current month
):
    """Per-day on-duty agent count for the team for a given month."""
    today = date.today()
    if month:
        yr, mo = month.split("-")
        month_start = date(int(yr), int(mo), 1)
    else:
        month_start = today.replace(day=1)

    import calendar as cal_mod
    last_day = cal_mod.monthrange(month_start.year, month_start.month)[1]
    month_end = date(month_start.year, month_start.month, last_day)

    my_agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()]
    total_agents = len(my_agent_ids)

    rows = (
        db.query(
            Beat.beat_date,
            func.count(func.distinct(Beat.agent_id)).label("on_duty_count"),
        )
        .filter(
            Beat.agent_id.in_(my_agent_ids),
            Beat.beat_date >= month_start,
            Beat.beat_date <= month_end,
            Beat.is_leave_day == False,  # noqa: E712
        )
        .group_by(Beat.beat_date)
        .all()
    )

    by_date = {r.beat_date.isoformat(): r.on_duty_count for r in rows}

    # Working days in month (Mon–Sat, no Sundays)
    working_days = sum(
        1 for d in range(1, last_day + 1)
        if date(month_start.year, month_start.month, d).weekday() < 6
    )

    # Leave breakdown by type for the month
    leave_rows = (
        db.query(
            Beat.leave_type,
            func.count(Beat.id).label("cnt"),
        )
        .filter(
            Beat.agent_id.in_(my_agent_ids),
            Beat.beat_date >= month_start,
            Beat.beat_date <= month_end,
            Beat.is_leave_day == True,  # noqa: E712
        )
        .group_by(Beat.leave_type)
        .all()
    )
    leave_by_type = {(r.leave_type or "ABSENT"): r.cnt for r in leave_rows}
    total_leave_days = sum(leave_by_type.values())

    return {
        "month": month_start.strftime("%Y-%m"),
        "total_agents": total_agents,
        "by_date": by_date,
        "working_days": working_days,
        "total_leave_agent_days": total_leave_days,
        "leave_by_type": leave_by_type,
    }


# ---------------------------------------------------------------------------
# Team-level DPD breakdown with optional month filter
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GET /manager/analytics/payment-modes
# ---------------------------------------------------------------------------
#
# 2026-09-17. The Analytics page's "Collection by Payment Mode" card. One
# GROUP BY over VERIFIED payments collected by this manager's agents — the
# same status rule the Collection Trend uses (_live_monthly_metrics), so the
# card's total ties to the header's Total Collected — optionally narrowed to
# one calendar month with the same `month=YYYY-MM` convention as the DPD
# bucket card beside it, so one month-click filters both. Every PaymentMode
# is returned, zero-filled: a mode nobody uses is a fact worth seeing, not a
# missing row.
#
# The reason the card exists is on the payload: `cash_share_pct` and
# `digital_share_pct` (UPI + NEFT + RTGS + ONLINE). Cash handled by agents in
# the field is the compliance-relevant number, and until now nothing on any
# manager screen could show it.

DIGITAL_MODES = frozenset({"UPI", "NEFT", "RTGS"})


@router.get("/analytics/payment-modes")
def get_payment_modes(
    current_user: ManagerOnly,
    db: DbSession,
    month: Optional[str] = None,  # YYYY-MM — if provided, only payments dated in that month
):
    my_agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()]

    q = (
        db.query(Payment.mode, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(Payment.agent_id.in_(my_agent_ids), Payment.status == PaymentStatus.VERIFIED)
    )
    if month:
        yr, mo = month.split("-")
        month_start = datetime(int(yr), int(mo), 1)
        month_end = datetime(int(yr) + 1, 1, 1) if int(mo) == 12 else datetime(int(yr), int(mo) + 1, 1)
        q = q.filter(Payment.payment_date >= month_start, Payment.payment_date < month_end)

    # Two members are excluded on purpose. BANK_DIRECT: a bank-side payment
    # carries agent_id NULL (see the 2026-09-09 direct-payment note) so it can
    # never match the agent filter above. ONLINE: a prototype leftover no
    # agent-facing screen offers (see PaymentMode.ONLINE); its 124 demo rows
    # were re-split across UPI/NEFT/RTGS on 2026-09-17. Either would be a
    # permanent zero row reading as "a mode nobody uses" when it is "not a
    # mode an agent can record at all".
    by_mode = {m.value: {"count": 0, "amount": 0.0} for m in PaymentMode if m.value not in ("BANK_DIRECT", "ONLINE")}
    for mode, n, amt in q.group_by(Payment.mode).all():
        key = mode.value if hasattr(mode, "value") else str(mode)
        by_mode.setdefault(key, {"count": 0, "amount": 0.0})
        by_mode[key] = {"count": int(n or 0), "amount": round(float(amt or 0.0), 2)}

    total_amount = round(sum(v["amount"] for v in by_mode.values()), 2)
    total_count = sum(v["count"] for v in by_mode.values())
    cash = by_mode.get("CASH", {}).get("amount", 0.0)
    digital = sum(v["amount"] for k, v in by_mode.items() if k in DIGITAL_MODES)
    share = (lambda x: round(x / total_amount * 100, 1) if total_amount > 0 else 0.0)

    rows = [
        {
            "mode": k,
            "count": v["count"],
            "amount": v["amount"],
            "share_pct": share(v["amount"]),
            "avg_ticket": round(v["amount"] / v["count"], 2) if v["count"] else 0.0,
            "is_cash": k == "CASH",
            "is_digital": k in DIGITAL_MODES,
        }
        for k, v in by_mode.items()
    ]
    rows.sort(key=lambda r: (-r["amount"], r["mode"]))

    # The 6-month split beside the composition bar: cash / digital / paper
    # (cheque + DD) per calendar month, over the SAME six months the trend
    # chart shows, so a click there and a column here name the same month.
    # Unfiltered by `month` on purpose — the trend is the context the
    # selected month sits in.
    _, _, _eff = _effective_today(my_agent_ids, db)
    months6 = _recent_months(_eff, 6)
    first = datetime(int(months6[0][:4]), int(months6[0][5:7]), 1)
    # Same "YYYY-MM" form _live_monthly_metrics uses (its _month_of is local
    # to that function; the SQLite test shim registers to_char for it).
    pay_month = func.to_char(Payment.payment_date, "YYYY-MM")
    by_month: dict[str, dict[str, float]] = {m: {"cash": 0.0, "digital": 0.0, "paper": 0.0, "count": 0} for m in months6}
    for mo, mode, n, amt in (
        db.query(pay_month, Payment.mode, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(Payment.agent_id.in_(my_agent_ids), Payment.status == PaymentStatus.VERIFIED,
                Payment.payment_date >= first)
        .group_by(pay_month, Payment.mode)
        .all()
    ):
        key = str(mo)[:7]
        if key not in by_month:
            continue
        mk = mode.value if hasattr(mode, "value") else str(mode)
        group = "cash" if mk == "CASH" else "digital" if mk in DIGITAL_MODES else "paper"
        by_month[key][group] = round(by_month[key][group] + float(amt or 0.0), 2)
        by_month[key]["count"] += int(n or 0)
    monthly = []
    for m in months6:
        v = by_month[m]
        tot = v["cash"] + v["digital"] + v["paper"]
        monthly.append({
            "month": m,
            "cash": v["cash"], "digital": v["digital"], "paper": v["paper"],
            "total": round(tot, 2), "count": v["count"],
            "cash_share_pct": round(v["cash"] / tot * 100, 1) if tot > 0 else 0.0,
        })

    return {
        "month": month,
        "monthly": monthly,
        "total_amount": total_amount,
        "total_count": total_count,
        "cash_amount": round(cash, 2),
        "cash_share_pct": share(cash),
        "digital_amount": round(digital, 2),
        "digital_share_pct": share(digital),
        "modes": rows,
    }


from pydantic import BaseModel as _BreakdownBase  # local, as the other sections here do

from app.services import portfolio_breakdown


class BreakdownRowOut(_BreakdownBase):
    """One row of a portfolio breakdown. `key` is the dimension's value, already
    labelled: a loan with no branch recorded reads "Not recorded" rather than
    vanishing, so the rows still sum to the page's header."""
    key: str
    case_count: int
    target_lakhs: float
    collected_lakhs: float
    collection_rate_pct: float


def _team_breakdown(db, current_user, *, dimension: str, month: Optional[str]) -> list[BreakdownRowOut]:
    my_agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()]
    try:
        rows = portfolio_breakdown.breakdown(db, dimension=dimension, agent_ids=my_agent_ids, month=month)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [BreakdownRowOut(**vars(r)) for r in rows]


class DpdBreakdownRowOut(_BreakdownBase):
    """The DPD rows' long-standing shape, whose dimension field is `bucket`.

    The generic breakdown calls it `key`. This one keeps `bucket` because three
    producers feed one card -- this route, the per-agent route, and
    /manager/analytics's own dpd_breakdown -- and renaming the field would be a
    frontend change for no gain to the reader.
    """
    bucket: str
    case_count: int
    target_lakhs: float
    collected_lakhs: float
    collection_rate_pct: float


def _as_dpd_rows(rows: list[BreakdownRowOut]) -> list[DpdBreakdownRowOut]:
    return [DpdBreakdownRowOut(bucket=r.key, **{k: v for k, v in vars(r).items() if k != "key"})
            for r in rows]


@router.get("/analytics/dpd-breakdown", response_model=list[DpdBreakdownRowOut])
def get_team_dpd_breakdown(
    current_user: ManagerOnly,
    db: DbSession,
    month: Optional[str] = None,  # YYYY-MM - if provided, sums payments in that month
):
    """DPD collection breakdown across all team cases.
    Without month: all-time portfolio totals (Case.collected_amount).
    With month: only payments collected in that calendar month.

    The query lives in services/portfolio_breakdown now (known issue 8): the
    same metric rules serve branch, city and product, and one definition cannot
    drift from another. The response is unchanged, field names included.
    """
    return _as_dpd_rows(_team_breakdown(db, current_user, dimension="bucket", month=month))


@router.get("/analytics/breakdown", response_model=list[BreakdownRowOut])
def get_team_breakdown(
    current_user: ManagerOnly,
    db: DbSession,
    dimension: Literal["bucket", "product", "branch", "city"] = "bucket",
    month: Optional[str] = None,  # YYYY-MM - if provided, sums payments in that month
):
    """The team's book by branch, city, product or DPD bucket (known issue 8).

    Ordered largest collection first for every dimension except bucket, which
    keeps its severity order. Rows are this manager's agents' cases only.
    """
    return _team_breakdown(db, current_user, dimension=dimension, month=month)


# ---------------------------------------------------------------------------
# GET /manager/visits/{visit_id}/media-urls
# Returns short-lived pre-signed URLs for photos and recordings on a visit.
# ---------------------------------------------------------------------------

@router.get("/visits/{visit_id}/media-urls")
def get_visit_media_urls(visit_id: UUIDPath, current_user: ManagerOnly, db: DbSession):
    # Ownership is checked BEFORE any presigned URL is minted, and that
    # ordering is the whole point: these URLs carry borrower photographs and
    # call recordings, and once issued they are valid for an hour WITHOUT
    # authentication. A leak here is not a read of someone else's row, it is a
    # bearer token for another agency's biometric evidence.
    #
    # This endpoint had no check at all: any manager could pass any visit id.
    visit = (
        db.query(Visit)
        .join(Agent, Visit.agent_id == Agent.id)
        .filter(Visit.id == visit_id, Agent.manager_user_id == current_user.id)
        .first()
    )
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")

    result: dict = {"photos": {}, "recordings": {}}

    for subject, key in (
        ("agent", visit.agent_photo_key),
        ("borrower", visit.borrower_photo_key),
        ("object", visit.object_photo_key),
    ):
        if key:
            result["photos"][subject] = storage.presigned_download_url(key, expires_minutes=60)

    for recorder, key in (
        ("agent", visit.agent_recording_key),
        ("borrower", visit.borrower_recording_key),
    ):
        if key:
            result["recordings"][recorder] = storage.presigned_download_url(key, expires_minutes=60)

    if not result["photos"] and not result["recordings"]:
        raise HTTPException(status_code=404, detail="No media found for this visit")
    return result


# ---------------------------------------------------------------------------
# AI Morning Briefing — data-driven ops intelligence, cached 1 hour per manager
# ---------------------------------------------------------------------------

import time as _time
_briefing_cache: dict[str, dict] = {}


def _dpd_numeric(bucket: str) -> int:
    return {"CURRENT": 0, "BUCKET_1": 15, "BUCKET_2": 45, "BUCKET_3": 75, "NPA": 120}.get(bucket, 0)


@router.get("/ai/briefing")
def ai_briefing(current_user: ManagerOnly, db: DbSession, refresh: bool = False):
    from app.models.loan import Loan as LoanModel
    from app.models.customer import Customer as CustomerModel

    cache_key = current_user.id
    now = _time.time()
    if not refresh and cache_key in _briefing_cache:
        cached = _briefing_cache[cache_key]
        if cached["expires_at"] > now:
            return cached["data"]

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    start_of_day, end_of_day, eff_date = _effective_today(my_agent_ids, db)

    # ── Collection stats ─────────────────────────────────────────────────────
    amount_collected_today = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.agent_id.in_(my_agent_ids), Payment.payment_date >= start_of_day, Payment.payment_date <= end_of_day)
        .scalar() or 0.0
    )
    visited_case_ids_today = [
        row[0] for row in
        db.query(Visit.case_id)
        .filter(Visit.agent_id.in_(my_agent_ids), Visit.check_in_time >= start_of_day)
        .distinct().all()
    ]
    amount_target_today = (
        db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
        .filter(Case.id.in_(visited_case_ids_today))
        .scalar() or 0.0
    ) if visited_case_ids_today else 0.0
    _on_leave_today = agent_ids_on_leave(db, leave_today(), my_agent_ids)
    _agents_all = db.query(Agent).options(joinedload(Agent.user)).filter(Agent.id.in_(my_agent_ids)).all()
    agents_on_duty = sum(1 for a in _agents_all if effective_status(a, _on_leave_today) is AgentStatus.ON_DUTY)
    total_agents = len(my_agent_ids)

    # ── PTP risk classification ───────────────────────────────────────────────
    ptps_today = (
        db.query(PTP)
        .join(Case, PTP.case_id == Case.id)
        .filter(
            PTP.agent_id.in_(my_agent_ids),
            PTP.committed_date == eff_date,
            PTP.status == PTPStatus.ACTIVE,
        )
        .options(
            joinedload(PTP.case).joinedload(Case.loan),
            joinedload(PTP.case).joinedload(Case.customer),
        )
        .all()
    )

    ninety_days_ago = eff_date - timedelta(days=90)
    broken_counts: dict[str, int] = {}
    if ptps_today:
        ptp_case_ids = [p.case_id for p in ptps_today]
        broken_rows = (
            db.query(PTP.case_id, func.count(PTP.id))
            .filter(PTP.case_id.in_(ptp_case_ids), PTP.status == PTPStatus.BROKEN, PTP.committed_date >= ninety_days_ago)
            .group_by(PTP.case_id)
            .all()
        )
        broken_counts = {r[0]: r[1] for r in broken_rows}

    risk_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    high_risk_cases = []
    for ptp in ptps_today:
        c = ptp.case
        dpd = c.loan.dpd if c and c.loan else 0
        broken = broken_counts.get(ptp.case_id, 0)
        is_hostile = c.customer.is_hostile if c and c.customer else False
        if dpd >= 90 or broken >= 2 or is_hostile:
            risk = "HIGH"
        elif dpd >= 60 or broken == 1:
            risk = "MEDIUM"
        else:
            risk = "LOW"
        risk_counts[risk] += 1
        if risk == "HIGH" and len(high_risk_cases) < 5:
            high_risk_cases.append({
                "case_number": c.case_number if c else ptp.case_id[:8],
                "customer_name": c.customer.full_name if c and c.customer else "Unknown",
                "dpd": dpd,
                "dpd_bucket": c.loan.dpd_bucket if c and c.loan else "UNKNOWN",
                "committed_amount": float(ptp.committed_amount or 0),
                "broken_ptp_count": broken,
                "is_hostile": is_hostile,
                "risk": risk,
            })

    # ── Stalled agents (on duty, 0 visits today) ─────────────────────────────
    visited_agent_ids_today = {
        row[0] for row in
        db.query(Visit.agent_id)
        .filter(Visit.agent_id.in_(my_agent_ids), Visit.check_in_time >= start_of_day)
        .distinct().all()
    }
    # Someone on approved leave today is not "yet to start" — they are off.
    on_duty_rows = [
        (a.id, a.user.full_name) for a in _agents_all
        if effective_status(a, _on_leave_today) is AgentStatus.ON_DUTY
    ]
    stalled_agents = [{"id": r[0], "name": r[1]} for r in on_duty_rows if r[0] not in visited_agent_ids_today]

    # ── DPD portfolio breakdown (real data) ──────────────────────────────────
    bucket_rows = (
        db.query(
            LoanModel.dpd_bucket,
            func.count(Case.id).label("case_count"),
            func.sum(Case.target_amount).label("target"),
            func.sum(Case.collected_amount).label("collected"),
        )
        .join(Case, Case.loan_id == LoanModel.id)
        .filter(Case.agent_id.in_(my_agent_ids))
        .group_by(LoanModel.dpd_bucket)
        .all()
    )
    dpd_breakdown = sorted([
        {
            "bucket": r.dpd_bucket,
            "case_count": r.case_count,
            "target_lakhs": round(float(r.target or 0) / 100000, 2),
            "collected_lakhs": round(float(r.collected or 0) / 100000, 2),
            "collection_rate_pct": round(float(r.collected or 0) / float(r.target or 1) * 100, 1) if r.target else 0.0,
        }
        for r in bucket_rows
    ], key=lambda x: _dpd_numeric(x["bucket"]))

    # ── Pending actions (real counts) ─────────────────────────────────────────
    escalated_count = (
        db.query(func.count(Case.id))
        .filter(Case.agent_id.in_(my_agent_ids), Case.is_escalated == True)  # noqa: E712
        .scalar() or 0
    )
    pending_first_visit = (
        db.query(func.count(Case.id))
        .filter(Case.agent_id.in_(my_agent_ids), Case.status == CaseStatus.ASSIGNED, Case.visit_count == 0)
        .scalar() or 0
    )

    # ── Collection velocity projection ────────────────────────────────────────
    collection_pct_now = round(amount_collected_today / max(amount_target_today, 1) * 100, 1) if amount_target_today > 0 else 0.0
    # Project to EOD (9-hour work day, 9am-6pm IST)
    ist_hour = (datetime.now(timezone.utc).hour + 5) % 24
    work_elapsed = max(1, min(ist_hour, 18) - 9)
    projected_eod_pct = round((amount_collected_today / max(work_elapsed / 9, 0.01)) / max(amount_target_today, 1) * 100, 1) if amount_target_today > 0 else 0.0

    # ── LLM: headline + insight + recommended actions ──────────────────────────
    headline = f"Collection at {collection_pct_now}% pace · {risk_counts['HIGH']} HIGH-risk PTPs require immediate attention"
    key_insight = f"{agents_on_duty}/{total_agents} agents on duty. {len(stalled_agents)} agent(s) yet to start field visits today."
    recommended_actions: list[dict] = []

    _brief_llm = None
    try:
        _ctx = {
            "collection_pct_now": collection_pct_now,
            "projected_eod_pct": round(projected_eod_pct, 1),
            "collected_lakh": round(amount_collected_today / 100000, 1),
            "target_lakh": round(amount_target_today / 100000, 1),
            "agents_on_duty": agents_on_duty,
            "total_agents": total_agents,
            "stalled_count": len(stalled_agents),
            "stalled_names": [a["name"] for a in stalled_agents[:3]],
            "ptp_high": risk_counts["HIGH"],
            "ptp_medium": risk_counts["MEDIUM"],
            "ptp_low": risk_counts["LOW"],
            "total_ptps_today": len(ptps_today),
            "escalated_cases": escalated_count,
            "pending_first_visit": pending_first_visit,
        }
        _brief_llm = _llm.complete(
            f"Operational data: {_ctx}",
            purpose="briefing", json_mode=True, temperature=0.25, max_tokens=1200,
            # The staff names this blob carries; the seam restores them in the answer.
            names=_ctx["stalled_names"],
            system=(
                "You are a collections agency AI operations analyst. Return JSON with: "
                "headline (1 sentence, data-specific numbers), "
                "key_insight (2 sentences identifying the most critical performance pattern with numbers), "
                "recommended_actions (array of up to 3 objects: {action, impact: HIGH|MEDIUM, urgency: NOW|TODAY}). "
                "Prioritise decisions that directly increase collection rate and prevent PTP failures. Be specific."
            ),
        )
        if not _brief_llm.ai_generated:
            raise RuntimeError(_brief_llm.status)   # take the fallback below
        _parsed = _brief_llm.data
        headline = _parsed.get("headline", headline)
        key_insight = _parsed.get("key_insight", key_insight)
        recommended_actions = _parsed.get("recommended_actions", [])[:3]
    except Exception:
        if risk_counts["HIGH"] > 0:
            recommended_actions.append({"action": f"Call agents handling {risk_counts['HIGH']} HIGH-risk PTPs and verify commitment before noon", "impact": "HIGH", "urgency": "NOW"})
        if stalled_agents:
            names = ", ".join(a["name"] for a in stalled_agents[:2])
            recommended_actions.append({"action": f"Check-in with {len(stalled_agents)} stalled agent(s): {names}", "impact": "HIGH", "urgency": "NOW"})
        if collection_pct_now < 30 and amount_target_today > 0:
            recommended_actions.append({"action": "Collection pace below 30% — reallocate high-value cases from underperforming agents to top performers", "impact": "HIGH", "urgency": "TODAY"})

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Whether the model wrote this or the computed fallback did.
        "ai_generated": bool(_brief_llm and _brief_llm.ai_generated),
        "ai_status": _brief_llm.status if _brief_llm else "NOT_CONFIGURED",
        "headline": headline,
        "key_insight": key_insight,
        "recommended_actions": recommended_actions,
        "ptp_risk": {
            "high": risk_counts["HIGH"],
            "medium": risk_counts["MEDIUM"],
            "low": risk_counts["LOW"],
            "total": len(ptps_today),
            "high_risk_cases": high_risk_cases,
        },
        "collection_velocity": {
            "collected_lakh": round(amount_collected_today / 100000, 1),
            "target_lakh": round(amount_target_today / 100000, 1),
            "pace_pct": collection_pct_now,
            "projected_eod_pct": min(projected_eod_pct, 150.0),
        },
        "stalled_agents": stalled_agents,
        "dpd_breakdown": dpd_breakdown,
        "total_cases_in_portfolio": sum(b["case_count"] for b in dpd_breakdown),
        "pending_actions": {
            "ptps_due": len(ptps_today),
            "cases_pending_first_visit": pending_first_visit,
            "escalated_cases": escalated_count,
        },
    }

    _briefing_cache[cache_key] = {"data": data, "expires_at": now + 3600}
    return data


# ---------------------------------------------------------------------------
# GET /manager/agents/live
# ---------------------------------------------------------------------------
# Latest position for every agent this manager owns, for the live map.
#
# Declared ABOVE the /agents/{agent_id}/... routes on purpose: FastAPI matches
# in declaration order, so if this sat below them "live" would be swallowed as
# an agent_id and this endpoint would be unreachable.

@router.get("/agents/live")
def agents_live(current_user: ManagerOnly, db: DbSession):
    from app.services.location_service import LocationService

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    positions = LocationService(db).live_positions(my_agent_ids)
    return {
        "agents": positions,
        "sos_count": sum(1 for p in positions if p["sos_active"]),
        "tracked_count": sum(1 for p in positions if p["latitude"] is not None),
    }


# ---------------------------------------------------------------------------
# GET /manager/agents/{agent_id}/trail
# ---------------------------------------------------------------------------
# One agent's movement for one IST day. `sos_only=true` narrows it to the fixes
# captured while an SOS was active, which is the incident replay.

@router.get("/agents/{agent_id}/trail")
def agent_trail(
    agent_id: UUIDPath,
    current_user: ManagerOnly,
    db: DbSession,
    date: str | None = None,
    sos_only: bool = False,
):
    from app.services.location_service import LocationService

    # Ownership is checked before any location is read. An agent's movement
    # history is the most sensitive data this API serves.
    #
    # This comment used to cite PUT /agents/{agent_id}/status as the unscoped
    # counter-example. That endpoint has since been fixed, so the citation was
    # pointing at nothing — see _require_own_agent above for the shared form.
    agent = (
        db.query(Agent)
        .join(Agent.user)
        .filter(Agent.id == agent_id, Agent.manager_user_id == current_user.id)
        .options(joinedload(Agent.user))
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    result = LocationService(db).trail(agent_id, day=date, sos_only=sos_only)
    result["employee_code"] = agent.employee_code
    result["full_name"] = agent.user.full_name if agent.user else agent.employee_code
    result["sos_active"] = agent.sos_active
    return result


# ---------------------------------------------------------------------------
# GET /manager/agents/{agent_id}/ai-insight
# ---------------------------------------------------------------------------

@router.get("/agents/{agent_id}/ai-insight")
def agent_ai_insight(agent_id: UUIDPath, current_user: ManagerOnly, db: DbSession):
    from app.models.loan import Loan as LoanModel

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    if agent_id not in my_agent_ids:
        raise HTTPException(status_code=404, detail="Agent not found or not under your team")

    agent = db.query(Agent).join(User, Agent.user_id == User.id).filter(Agent.id == agent_id).first()
    start_of_day, _, eff_date = _effective_today(my_agent_ids, db)

    # Last 3 COMPLETE months of performance for this agent.
    #
    # Was `eff_date.replace(day=1) - timedelta(days=30 * i)`, which had two
    # faults. First, 30 days is not a month: from 15 March it produced
    # ['2025-12', '2026-01', '2026-03'] — February dropped and December pulled
    # in — and the de-dup below hid the collision by collapsing the list to two
    # entries, which still satisfied the `>= 2` check further down.
    # Second, the window ENDED on the current month, so the trend compared a
    # part-month against a whole one and read DECLINING for nearly every agent
    # for nearly every day of the month, snapping back only at month end.
    # Current-month figures are still reported below — they are just no longer
    # what the trend is measured from.
    months_3 = _complete_months_before(eff_date, 3)

    current_month = eff_date.strftime("%Y-%m")

    # Live, not AgentPerformance — the same source and the same definitions the
    # Agents page uses. This panel renders directly beneath that page's
    # collection-rate sparkline for the same agent, so reading a different table
    # here is how the badge and the chart end up disagreeing on screen.
    #
    # The whole team is fetched, not just this agent: the panel states the
    # agent's figures "vs team", and an average taken from a different source
    # than the value it is compared against is not a comparison.
    window = [*months_3, current_month]
    team_metrics = _live_monthly_metrics(db, my_agent_ids, window)
    agent_metrics = team_metrics.get(agent_id, {})

    def _blank() -> dict:
        return {"visits": 0, "collected": 0.0, "target": 0.0,
                "ptps_set": 0, "ptps_honored": 0, "rate_pct": 0.0,
                "visits_needing_promise": 0, "ptps_captured": 0,
                "ptp_capture_pct": 0.0}

    # Team averages for the current month. Mean of each agent's rate, which is
    # what the previous func.avg(collection_rate) computed — kept deliberately,
    # so the number a manager has been reading does not silently change meaning
    # from "average agent" to "team total".
    team_now = [team_metrics.get(aid, {}).get(current_month) or _blank() for aid in my_agent_ids]
    n_team = max(len(team_now), 1)
    team_collection_rate = round(sum(m["rate_pct"] for m in team_now) / n_team, 1)
    team_avg_visits = round(sum(m["visits"] for m in team_now) / n_team, 1)
    _team_set = sum(m["ptps_set"] for m in team_now)
    _team_hon = sum(m["ptps_honored"] for m in team_now)
    team_ptp_rate = round(_team_hon / _team_set * 100, 1) if _team_set > 0 else 0.0

    # Case mix by DPD bucket
    case_mix = (
        db.query(LoanModel.dpd_bucket, func.count(Case.id))
        .join(Case, Case.loan_id == LoanModel.id)
        .filter(Case.agent_id == agent_id)
        .group_by(LoanModel.dpd_bucket)
        .all()
    )
    case_mix_dict = {r[0]: r[1] for r in case_mix}

    visits_today = (
        db.query(func.count(Visit.id))
        .filter(Visit.agent_id == agent_id, Visit.check_in_time >= start_of_day)
        .scalar() or 0
    )

    # Build monthly trend
    monthly_data = []
    for m in months_3:
        p = agent_metrics.get(m) or _blank()
        ptp_rate = round(p["ptps_honored"] / p["ptps_set"] * 100, 1) if p["ptps_set"] > 0 else 0.0
        monthly_data.append({
            "month": m,
            "collection_rate_pct": p["rate_pct"],
            "visits": p["visits"],
            "ptp_rate_pct": ptp_rate,
            "collected": round(p["collected"], 2),
        })

    # Oldest complete month → newest complete month. Both endpoints now read the
    # same live figures over complete months, so this agrees with
    # collection_rate_delta_pts on GET /manager/agents by construction rather
    # than by coincidence. A month the agent visited nothing in has no rate to
    # compare, so it is skipped rather than treated as a 0% collapse.
    observed = [m for m, src in zip(monthly_data, months_3)
                if (agent_metrics.get(src) or _blank())["target"] > 0]
    if len(observed) >= 2:
        delta = observed[-1]["collection_rate_pct"] - observed[0]["collection_rate_pct"]
        trend = "IMPROVING" if delta > 5 else ("DECLINING" if delta < -5 else "STABLE")
    else:
        trend = "STABLE"

    now_m = agent_metrics.get(current_month) or _blank()
    agent_rate = now_m["rate_pct"]
    agent_ptp_rate = round(now_m["ptps_honored"] / now_m["ptps_set"] * 100, 1) if now_m["ptps_set"] > 0 else 0.0
    agent_visits = now_m["visits"]

    # Additional signal: visit-to-collection conversion (collections / visits = per-visit yield)
    current_collected = now_m["collected"]
    per_visit_yield = round(current_collected / max(agent_visits, 1), 0) if agent_visits > 0 else 0.0
    # Team per-visit yield — totals, not a mean of per-agent yields, so a
    # low-volume agent cannot swing it. Same live source as everything above.
    team_total_collected = sum(m["collected"] for m in team_now)
    team_total_visits_cm = sum(m["visits"] for m in team_now)
    team_per_visit_yield = round(float(team_total_collected) / max(team_total_visits_cm, 1), 0)

    # Active vs closed case ratio (portfolio health)
    active_cases = (
        db.query(func.count(Case.id))
        .filter(Case.agent_id == agent_id, Case.status.in_(["ASSIGNED", "IN_PROGRESS", "PTP_SET", "PARTIALLY_PAID", "ESCALATED"]))
        .scalar() or 0
    )
    resolved_cases = (
        db.query(func.count(Case.id))
        .filter(Case.agent_id == agent_id, Case.status.in_(["PAID", "CLOSED", "WRITTEN_OFF"]))
        .scalar() or 0
    )

    agent_name = agent.user.full_name if agent and agent.user else "Agent"
    insight_text = (
        f"{agent_name} has {agent_rate}% collection rate (team: {team_collection_rate}%) "
        f"and {agent_ptp_rate}% PTP honor rate (team: {team_ptp_rate}%) this month. "
        f"Per-visit yield: ₹{int(per_visit_yield):,} vs team avg ₹{int(team_per_visit_yield):,}."
    )
    recommended_action = "Monitor performance and review case portfolio."
    performance_signal = trend

    try:
        import json as _json  # noqa: F401  — still used further down
        _ctx = {
            "agent_name": agent_name,
            "tier": agent.tier if agent else "TIER_2",
            "territory": agent.territory if agent else "",
            "specialization": agent.specialization if agent else "",
            "trend_last_3_COMPLETE_months": monthly_data,
            "performance_trend_direction": trend,
            # Flagged explicitly: the model is handed both this and the complete
            # months above, and without the warning it reads the part-month as a
            # collapse and overrides performance_signal to DECLINING — the same
            # error the computed trend just stopped making.
            "current_month_INCOMPLETE": {
                "note": (
                    f"Month in progress — {eff_date.day} of "
                    f"{monthrange(eff_date.year, eff_date.month)[1]} days elapsed. "
                    "Figures are month-to-date and will be low. Do NOT read them as a decline."
                ),
                "collection_rate_pct": agent_rate,
                "ptp_honor_rate_pct": agent_ptp_rate,
                "total_visits": agent_visits,
                "visits_today": visits_today,
                "collected_amount": round(current_collected / 100000, 2),
                "per_visit_yield_rupees": int(per_visit_yield),
                "active_cases": active_cases,
                "resolved_cases": resolved_cases,
            },
            "team_averages": {
                "collection_rate_pct": team_collection_rate,
                "ptp_honor_rate_pct": team_ptp_rate,
                "visits_per_month": team_avg_visits,
                "per_visit_yield_rupees": int(team_per_visit_yield),
            },
            "case_portfolio_by_dpd": case_mix_dict,
        }
        _insight_llm = _llm.complete(
            f"Agent data: {_ctx}",
            purpose="agent_insight", json_mode=True, temperature=0.25, max_tokens=1200,
            names=[agent_name],
            system=(
                    "You are a collections operations analyst. Analyse a field agent's full performance profile. "
                    "Return JSON with exactly these keys: "
                    "performance_signal (IMPROVING/DECLINING/STABLE). Base this ONLY on "
                    "trend_last_3_COMPLETE_months. The current month is still in progress, so its "
                    "month-to-date totals are always lower than a finished month — never treat that "
                    "as a decline. Default to the supplied performance_trend_direction unless the "
                    "complete months clearly contradict it. "
                    "insight_text (3 sentences covering: 1) collection rate & trend WHY, "
                    "2) visit productivity and per-visit yield vs team, "
                    "3) PTP discipline and case portfolio health — use specific numbers), "
                    "recommended_action (1 concrete, specific action the manager should take THIS WEEK "
                    "to improve this agent's output — e.g. coaching, case reallocation, territory change, "
                    "shadowing a top performer, reducing NPA case load). "
                "Be analytically precise, not generic."
            ),
        )
        if not _insight_llm.ai_generated:
            raise RuntimeError(_insight_llm.status)
        _parsed = _insight_llm.data
        performance_signal = _parsed.get("performance_signal", trend)
        insight_text = _parsed.get("insight_text", insight_text)
        recommended_action = _parsed.get("recommended_action", recommended_action)
    except Exception:
        pass

    return {
        "agent_id": agent_id,
        "ai_generated": bool(locals().get("_insight_llm") and _insight_llm.ai_generated),
        "ai_status": _insight_llm.status if locals().get("_insight_llm") else "NOT_CONFIGURED",
        "performance_signal": performance_signal,
        "insight_text": insight_text,
        "recommended_action": recommended_action,
        "monthly_data": monthly_data,
        "current_month": {
            "collection_rate_pct": agent_rate,
            "ptp_rate_pct": agent_ptp_rate,
            "visits": agent_visits,
            "visits_today": visits_today,
            "per_visit_yield": int(per_visit_yield),
            "active_cases": active_cases,
            "resolved_cases": resolved_cases,
        },
        "team_avg": {
            "collection_rate_pct": team_collection_rate,
            "ptp_rate_pct": team_ptp_rate,
            "visits_per_month": team_avg_visits,
            "per_visit_yield": int(team_per_visit_yield),
        },
        "case_mix_by_dpd": case_mix_dict,
    }


# ---------------------------------------------------------------------------
# GET /manager/agents/{agent_id}/reallocation-plan — rule-based, no LLM
# ---------------------------------------------------------------------------

@router.get("/agents/{agent_id}/reallocation-plan")
def reallocation_plan(agent_id: UUIDPath, current_user: ManagerOnly, db: DbSession):
    from app.models.loan import Loan as LoanModel
    from app.models.customer import Customer as CustomerModel

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    if agent_id not in my_agent_ids:
        raise HTTPException(status_code=404, detail="Agent not found or not under your team")

    source_agent = db.query(Agent).join(User, Agent.user_id == User.id).filter(Agent.id == agent_id).first()

    active_statuses = [CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS, CaseStatus.PTP_SET, CaseStatus.PARTIALLY_PAID]
    source_cases = (
        db.query(Case)
        .join(LoanModel, Case.loan_id == LoanModel.id)
        .join(CustomerModel, Case.customer_id == CustomerModel.id)
        .filter(Case.agent_id == agent_id, Case.status.in_(active_statuses))
        .options(joinedload(Case.loan), joinedload(Case.customer))
        .order_by(Case.target_amount.desc())
        .all()
    )

    other_agent_ids = [aid for aid in my_agent_ids if aid != agent_id]
    if not other_agent_ids:
        return {
            "from_agent": {"id": agent_id, "name": source_agent.user.full_name if source_agent and source_agent.user else "Unknown"},
            "total_cases": len(source_cases),
            "suggested_reallocations": [],
            "unallocatable_cases": [{"case_number": c.case_number, "customer_name": c.customer.full_name if c.customer else "Unknown", "target_amount": float(c.target_amount or 0), "reason": "No other agents available"} for c in source_cases],
            "summary": {"can_reallocate": 0, "cannot_reallocate": len(source_cases), "agents_receiving": 0},
        }

    other_agents = (
        db.query(Agent)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.id.in_(other_agent_ids))
        .all()
    )
    case_count_rows = (
        db.query(Case.agent_id, func.count(Case.id))
        .filter(Case.agent_id.in_(other_agent_ids), Case.status.in_(active_statuses))
        .group_by(Case.agent_id)
        .all()
    )
    case_counts = {r[0]: r[1] for r in case_count_rows}

    agent_info = {
        ag.id: {
            "id": ag.id,
            "name": ag.user.full_name if ag.user else "Unknown",
            "territory": ag.territory,
            "tier": ag.tier,
            "specialization": ag.specialization,
            "languages": ag.languages_spoken or [],
            "available_slots": max(0, (ag.max_cases_per_day or 15) - case_counts.get(ag.id, 0)),
            "ranking_score": float(ag.ranking_score or 0),
        }
        for ag in other_agents
    }
    remaining_capacity = {aid: info["available_slots"] for aid, info in agent_info.items()}

    source_territory = source_agent.territory if source_agent else ""
    suggested: list[dict] = []
    unallocatable: list[dict] = []

    for case in source_cases:
        loan = case.loan
        customer = case.customer

        # A do-not-contact customer is not a capacity problem to be solved by
        # moving the case to someone else — it must not be visited by anyone.
        blocked = _elig.case_block_reason(customer)
        if blocked is not None:
            unallocatable.append({
                "case_id": case.id,
                "case_number": case.case_number,
                "customer_name": customer.full_name if customer else None,
                "reason": blocked,
            })
            continue

        best_id, best_score, best_reasons = None, -1, []

        for ag in other_agents:
            if remaining_capacity.get(ag.id, 0) <= 0:
                continue
            # Same hard rules the nightly allocator applies. Without this a
            # manager covering an absence could hand a female-only customer to
            # a male agent — the reallocation path used to score on territory,
            # language, tier and capacity alone and never checked eligibility.
            if _elig.agent_block_reason(ag, customer) is not None:
                continue
            info = agent_info[ag.id]
            score, reasons = 0, []

            if info["territory"] == source_territory:
                score += 50; reasons.append("Same territory")
            elif info["territory"].split("-")[0] == source_territory.split("-")[0]:
                score += 20; reasons.append("Adjacent zone")

            if customer and customer.language_preference in info["languages"]:
                score += 20; reasons.append(f"{customer.language_preference} speaker")

            dpd = loan.dpd if loan else 0
            if dpd >= 90 and ag.specialization == "NPA":
                score += 15; reasons.append("NPA specialist")
            elif dpd >= 60 and ag.specialization in ("HIGH_BUCKET", "NPA"):
                score += 10; reasons.append("High-bucket specialist")

            if case.priority in ("CRITICAL", "HIGH") and info["tier"] == "TIER_1":
                score += 10; reasons.append("Tier 1 for priority case")

            score += min(remaining_capacity[ag.id] * 2, 20)
            score += min(info["ranking_score"] / 10, 10)

            if score > best_score:
                best_score, best_id, best_reasons = score, ag.id, reasons

        if best_id:
            info = agent_info[best_id]
            remaining_capacity[best_id] -= 1
            suggested.append({
                "case_id": case.id,
                "case_number": case.case_number,
                "customer_name": customer.full_name if customer else "Unknown",
                "dpd_bucket": loan.dpd_bucket if loan else "UNKNOWN",
                "dpd": loan.dpd if loan else 0,
                "target_amount": float(case.target_amount or 0),
                "priority": case.priority,
                "to_agent_id": best_id,
                "to_agent_name": info["name"],
                "to_agent_tier": info["tier"],
                "to_agent_available_slots": info["available_slots"],
                "match_score": best_score,
                "match_reason": " · ".join(best_reasons) if best_reasons else "Best available capacity",
            })
        else:
            unallocatable.append({
                "case_number": case.case_number,
                "customer_name": customer.full_name if customer else "Unknown",
                "target_amount": float(case.target_amount or 0),
                "reason": "All suitable agents at capacity",
            })

    return {
        "from_agent": {
            "id": agent_id,
            "name": source_agent.user.full_name if source_agent and source_agent.user else "Unknown",
            "territory": source_agent.territory if source_agent else "",
            "tier": source_agent.tier if source_agent else "",
        },
        "total_cases": len(source_cases),
        "suggested_reallocations": suggested,
        "unallocatable_cases": unallocatable,
        "summary": {
            "can_reallocate": len(suggested),
            "cannot_reallocate": len(unallocatable),
            "agents_receiving": len({r["to_agent_id"] for r in suggested}),
        },
    }


# ---------------------------------------------------------------------------
# Agent status toggle (ON_DUTY ↔ OFF_DUTY) — no new table, just Agent.status
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BM


class _StatusBody(_BM):
    status: str  # "ON_DUTY" or "OFF_DUTY"


@router.put("/agents/{agent_id}/status")
def update_agent_status(
    agent_id: UUIDPath,
    body: _StatusBody,
    current_user: ManagerOnly,
    db: DbSession,
):
    """Toggle an agent between ON_DUTY and OFF_DUTY. Manager only."""
    if body.status not in ("ON_DUTY", "OFF_DUTY"):
        raise HTTPException(status_code=422, detail="status must be ON_DUTY or OFF_DUTY")

    agent = (
        db.query(Agent)
        .join(Agent.user)
        .filter(Agent.id == agent_id, Agent.manager_user_id == current_user.id)
        .options(joinedload(Agent.user))
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # A duty toggle cannot override an approved leave: the leave is the
    # record, and the day's leave beat already released the agent's cases.
    # Revoke the leave (POST /leave-requests/{id}/revoke) to bring them back.
    if agent.id in agent_ids_on_leave(db, leave_today(), [agent.id]):
        raise HTTPException(
            status_code=409,
            detail=f"{agent.user.full_name if agent.user else 'Agent'} is on approved leave today; "
                   "revoke the leave to change their duty status.",
        )

    agent.status = AgentStatus[body.status]
    db.commit()
    db.refresh(agent)

    return {
        "agent_id": agent_id,
        "agent_name": agent.user.full_name if agent.user else "Unknown",
        "new_status": agent.status.value if hasattr(agent.status, "value") else str(agent.status),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# SOS acknowledgement — the real action behind the dashboard's "Respond"
# button, which previously just showed a toast claiming "emergency services
# notified" with nothing actually sent (see changelog.md, 2026-07-15).
# ---------------------------------------------------------------------------

@router.post("/agents/{agent_id}/sos/acknowledge")
def acknowledge_agent_sos(
    agent_id: UUIDPath,
    current_user: ManagerOnly,
    db: DbSession,
):
    """Manager acknowledges an agent's active SOS — sends the agent a real
    SMS/WhatsApp confirming their manager has seen it and is responding.

    Deliberately does not claim to contact police/ambulance ("emergency
    services") — this app has no such integration, and the previous copy
    promising one was itself part of the problem being fixed here.
    """
    agent = (
        db.query(Agent)
        .options(joinedload(Agent.user))
        .filter(Agent.id == agent_id, Agent.manager_user_id == current_user.id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.sos_active:
        raise HTTPException(status_code=400, detail="This agent has no active SOS")

    if agent.user and agent.user.phone:
        e164 = "+" + NotificationService.normalize_phone(agent.user.phone)
        brand = brand_for(db, agent=agent)
        sms_body = (
            f"Your manager {current_user.full_name} has acknowledged your SOS and is "
            f"responding. Stay safe. - {brand.agency_name or brand.bank_name}"
        )
        wa_body = (
            f"*SOS Acknowledged*\n\n"
            f"Your manager *{current_user.full_name}* has seen your SOS alert and is responding.\n"
            f"Stay where you are if it's safe to do so."
        )
        NotificationService.send_twilio(e164, sms_body, wa_body, db=db, agent_id=agent.id)

    return {
        "acknowledged": True,
        "agent_id": agent.id,
        "agent_name": agent.user.full_name if agent.user else "Unknown",
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Agent availability calendar (manager view, derived from Beat records)
# ---------------------------------------------------------------------------

@router.get("/agents/{agent_id}/availability-calendar")
def manager_get_agent_availability_calendar(
    agent_id: UUIDPath,
    current_user: ManagerOnly,
    db: DbSession,
):
    """Manager view of any agent's 6-month duty calendar derived from beats."""
    from collections import defaultdict

    agent = (
        db.query(Agent)
        .options(joinedload(Agent.user))
        .filter(Agent.id == agent_id, Agent.manager_user_id == current_user.id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    today = date.today()
    six_months_ago = today - timedelta(days=180)

    beats = (
        db.query(Beat.beat_date, Beat.status, Beat.total_cases, Beat.is_leave_day, Beat.leave_type)
        .filter(Beat.agent_id == agent_id, Beat.beat_date >= six_months_ago)
        .all()
    )
    beat_map = {
        b.beat_date: {
            "beat_status": b.status.value if hasattr(b.status, "value") else str(b.status),
            "cases": b.total_cases or 0,
            "leave": bool(b.is_leave_day), "leave_type": b.leave_type,
        }
        for b in beats
    }

    calendar_days: list[dict] = []
    d = six_months_ago
    while d <= today:
        if d.weekday() != 6:  # exclude Sundays
            info = beat_map.get(d)
            calendar_days.append({
                "date": d.isoformat(),
                "day_of_week": d.strftime("%a"),
                "status": "ON_LEAVE" if (info and info.get("leave")) else "ON_DUTY" if info else "OFF_DUTY",
                "leave_type": info.get("leave_type") if info and info.get("leave") else None,
                "beat_status": info["beat_status"] if info else None,
                "cases": info["cases"] if info else 0,
            })
        d += timedelta(days=1)

    on_duty = sum(1 for c in calendar_days if c["status"] == "ON_DUTY")
    total = len(calendar_days)

    monthly: dict = defaultdict(lambda: {"on_duty": 0, "off_duty": 0, "total_cases": 0})
    for c in calendar_days:
        m = c["date"][:7]
        if c["status"] == "ON_DUTY":
            monthly[m]["on_duty"] += 1
            monthly[m]["total_cases"] += c["cases"]
        else:
            monthly[m]["off_duty"] += 1

    monthly_summary = [
        {
            "month": m,
            "on_duty": v["on_duty"],
            "off_duty": v["off_duty"],
            "total_cases": v["total_cases"],
            "attendance_pct": round(v["on_duty"] / max(v["on_duty"] + v["off_duty"], 1) * 100, 1),
        }
        for m, v in sorted(monthly.items())
    ]

    return {
        "agent_id": agent_id,
        "agent_name": agent.user.full_name if agent.user else "Unknown",
        "current_status": agent.status.value if hasattr(agent.status, "value") else str(agent.status),
        "calendar": calendar_days,
        "summary": {
            "total_working_days": total,
            "on_duty_days": on_duty,
            "off_duty_days": total - on_duty,
            "attendance_rate_pct": round(on_duty / max(total, 1) * 100, 1),
        },
        "monthly_summary": monthly_summary,
    }


# ---------------------------------------------------------------------------
# Per-agent DPD breakdown (for analytics panel)
# ---------------------------------------------------------------------------

@router.get("/agents/{agent_id}/dpd-breakdown")
def manager_get_agent_dpd_breakdown(
    agent_id: UUIDPath,
    current_user: ManagerOnly,
    db: DbSession,
    month: Optional[str] = None,  # YYYY-MM — if provided, sums payments made in that month
):
    """DPD collection breakdown for a specific agent.
    Without month: all-time portfolio totals.
    With month: only payments collected in that calendar month per DPD bucket."""
    # Was unscoped: any manager could read any other manager's agent's
    # portfolio and collections by guessing an id.
    _require_own_agent(db, current_user, agent_id)
    bucket_order = ["BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]

    if month:
        yr, mo = month.split("-")
        month_start = datetime(int(yr), int(mo), 1)
        # First day of next month as exclusive upper bound
        if int(mo) == 12:
            month_end = datetime(int(yr) + 1, 1, 1)
        else:
            month_end = datetime(int(yr), int(mo) + 1, 1)

        rows = (
            db.query(
                Loan.dpd_bucket,
                func.count(func.distinct(Case.id)).label("case_count"),
                func.sum(Case.target_amount).label("target_amount"),
                func.sum(Payment.amount).label("collected_amount"),
            )
            .join(Case, Case.loan_id == Loan.id)
            .join(Payment, Payment.case_id == Case.id)
            .filter(
                Case.agent_id == agent_id,
                Payment.agent_id == agent_id,
                Payment.payment_date >= month_start,
                Payment.payment_date < month_end,
                Payment.status != "REJECTED",
            )
            .group_by(Loan.dpd_bucket)
            .all()
        )
    else:
        rows = (
            db.query(
                Loan.dpd_bucket,
                func.count(Case.id).label("case_count"),
                func.sum(Case.target_amount).label("target_amount"),
                func.sum(Case.collected_amount).label("collected_amount"),
            )
            .join(Case, Case.loan_id == Loan.id)
            .filter(Case.agent_id == agent_id)
            .group_by(Loan.dpd_bucket)
            .all()
        )

    result = []
    for r in rows:
        bucket = r.dpd_bucket.value if hasattr(r.dpd_bucket, "value") else str(r.dpd_bucket)
        target = float(r.target_amount or 0)
        collected = float(r.collected_amount or 0)
        result.append({
            "bucket": bucket,
            "case_count": r.case_count,
            "target_lakhs": round(target / 100000, 2),
            "collected_lakhs": round(collected / 100000, 2),
            "collection_rate_pct": round(collected / max(target, 1) * 100, 1),
        })

    result.sort(key=lambda x: bucket_order.index(x["bucket"]) if x["bucket"] in bucket_order else 99)
    return result


# ---------------------------------------------------------------------------
# AI Monthly Performance Report (agency-level or per-agent, 30-50 words)
# ---------------------------------------------------------------------------

@router.get("/ai/monthly-report")
def get_monthly_report(
    month: str,
    current_user: ManagerOnly,
    db: DbSession,
    agent_id: UUIDQuery = None,
):
    """Generate a 60-90 word eagle-view AI performance brief with DPD breakdown,
    agent spread, and month-over-month trend for the agency head."""
    import openai as _openai
    from datetime import date as _date2

    # Compute previous month
    yr, mo = month.split("-")
    prev_dt = _date2(int(yr), int(mo), 1) - timedelta(days=1)
    prev_month = prev_dt.strftime("%Y-%m")

    # Manager-owned agent IDs
    my_agent_ids = [
        r[0] for r in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    if agent_id:
        if agent_id not in my_agent_ids:
            raise HTTPException(status_code=404, detail="Agent not found")
        my_agent_ids = [agent_id]

    # DPD portfolio snapshot (current state of all cases)
    bucket_order = ["BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]
    bucket_labels = {"BUCKET_1": "1–30 DPD", "BUCKET_2": "31–60 DPD", "BUCKET_3": "61–90 DPD", "NPA": "NPA 90+"}
    dpd_rows = (
        db.query(
            Loan.dpd_bucket,
            func.count(Case.id).label("cases"),
            func.sum(Case.target_amount).label("target"),
            func.sum(Case.collected_amount).label("collected"),
        )
        .join(Case, Case.loan_id == Loan.id)
        .filter(Case.agent_id.in_(my_agent_ids))
        .group_by(Loan.dpd_bucket)
        .all()
    )
    dpd_map = {}
    for r in dpd_rows:
        bkt = r.dpd_bucket.value if hasattr(r.dpd_bucket, "value") else str(r.dpd_bucket)
        t = float(r.target or 0)
        c = float(r.collected or 0)
        dpd_map[bkt] = {
            "cases": r.cases,
            "rate": round(c / max(t, 1) * 100, 0),
        }

    dpd_lines = " | ".join(
        f"{bucket_labels.get(b, b)}: {dpd_map[b]['cases']} cases @ {dpd_map[b]['rate']:.0f}%"
        for b in bucket_order if b in dpd_map
    )

    # Every staff name this report's prompt embeds, whichever branch built it.
    prompt_names: list[str] = []
    if agent_id:
        # ── Per-agent report ──────────────────────────────────────────────
        agent_obj = (
            db.query(Agent).options(joinedload(Agent.user)).filter(Agent.id == agent_id).first()
        )
        agent_name = agent_obj.user.full_name if agent_obj and agent_obj.user else "Agent"
        tier = getattr(agent_obj, "tier", "?") if agent_obj else "?"
        territory = getattr(agent_obj, "territory", "?") if agent_obj else "?"
        scope = agent_name
        prompt_names.append(agent_name)

        row = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id == agent_id, AgentPerformance.month == month_start(month))
            .first()
        )
        prev_row = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id == agent_id, AgentPerformance.month == month_start(prev_month))
            .first()
        )

        # Team averages for the month
        team_rows = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == month_start(month))
            .all()
        )
        n_team = len(team_rows) or 1
        team_avg_rate = sum(float(r.collection_rate or 0) for r in team_rows) / n_team * 100
        team_avg_visits = sum(int(r.total_visits or 0) for r in team_rows) / n_team
        team_ptp_set = sum(int(r.ptps_set or 0) for r in team_rows)
        team_ptp_honored = sum(int(r.ptps_honored or 0) for r in team_rows)
        team_ptp_rate = team_ptp_honored / max(team_ptp_set, 1) * 100
        team_total_collected = sum(float(r.total_collected or 0) for r in team_rows)
        team_total_visits = sum(int(r.total_visits or 0) for r in team_rows)
        team_yield = team_total_collected / max(team_total_visits, 1) / 1000  # in K

        # Agent DPD case mix
        agent_dpd_rows = (
            db.query(
                Loan.dpd_bucket,
                func.count(Case.id).label("cases"),
                func.sum(Case.target_amount).label("target"),
                func.sum(Case.collected_amount).label("collected"),
            )
            .join(Case, Case.loan_id == Loan.id)
            .filter(Case.agent_id == agent_id)
            .group_by(Loan.dpd_bucket)
            .all()
        )
        agent_dpd_text = " | ".join(
            "{}: {} cases {}%".format(
                bucket_labels.get(
                    r.dpd_bucket.value if hasattr(r.dpd_bucket, "value") else str(r.dpd_bucket),
                    str(r.dpd_bucket)
                ),
                r.cases,
                round(float(r.collected or 0) / max(float(r.target or 1), 1) * 100, 0),
            )
            for r in agent_dpd_rows
        )

        if not row:
            fallback = f"No data for {agent_name} in {month}."
            prompt = fallback
            scope_stats = fallback
        else:
            rate = round(float(row.collection_rate or 0) * 100, 1)
            collected = float(row.total_collected or 0) / 100000
            visits = int(row.total_visits or 0)
            ptp_set = int(row.ptps_set or 0)
            ptp_honored = int(row.ptps_honored or 0)
            ptp_rate = round(ptp_honored / max(ptp_set, 1) * 100, 1)
            yield_k = float(row.total_collected or 0) / max(visits, 1) / 1000

            prev_rate = round(float(prev_row.collection_rate or 0) * 100, 1) if prev_row else None
            prev_collected = float(prev_row.total_collected or 0) / 100000 if prev_row else None
            mom_dir = (
                "↑ improving" if prev_rate and rate > prev_rate
                else "↓ declining" if prev_rate and rate < prev_rate
                else "→ flat"
            )
            trend_line = (
                f"{prev_rate}% → {rate}% ({mom_dir}) | ₹{prev_collected:.1f}L → ₹{collected:.1f}L"
                if prev_row else "No prior month data."
            )

            # Rank this agent among all agents for the month
            all_rates = sorted(
                (float(r.collection_rate or 0) * 100 for r in team_rows),
                reverse=True
            )
            rank_pos = next((i + 1 for i, v in enumerate(all_rates) if v <= rate + 0.01), len(all_rates))
            rank_label = f"Ranked #{rank_pos} of {n_team} agents this month"

            # Achievement flags
            achievements = []
            agent_yield_rank = sorted(
                (float(r.total_collected or 0) / max(int(r.total_visits or 1), 1) for r in team_rows),
                reverse=True
            )
            if visits > 0:
                yield_pos = next((i + 1 for i, v in enumerate(agent_yield_rank) if v <= yield_k * 1000 + 0.01), n_team)
                if yield_pos == 1:
                    achievements.append("Best yield/visit in team")
                elif yield_pos <= 3:
                    achievements.append(f"Top-3 yield/visit (#{yield_pos})")
            if ptp_rate >= 70:
                achievements.append(f"High PTP discipline ({ptp_rate}%)")
            if rate >= team_avg_rate + 15:
                achievements.append(f"Outperforming team by {rate - team_avg_rate:.0f}%")
            achievement_str = "; ".join(achievements) if achievements else "No standout achievements this month"

            rate_delta = round(rate - team_avg_rate, 1)
            rate_vs = f"{'+' if rate_delta >= 0 else ''}{rate_delta}% vs team avg ({team_avg_rate:.0f}%)"

            scope_stats = (
                f"Agent: {agent_name} | {tier} | {territory} | Month: {month}\n"
                f"Team Rank: {rank_label}\n"
                f"Collections: ₹{collected:.1f}L at {rate}% ({rate_vs})\n"
                f"Visits: {visits} (team avg {team_avg_visits:.0f}) | Yield/visit: ₹{yield_k:.1f}K (team ₹{team_yield:.1f}K)\n"
                f"PTP: {ptp_honored}/{ptp_set} = {ptp_rate}% (team {team_ptp_rate:.0f}%)\n"
                f"MoM Trend: {trend_line}\n"
                f"DPD Case Mix: {agent_dpd_text}\n"
                f"Highlights: {achievement_str}"
            )

            prompt = (
                "You are a senior collection analytics advisor writing for the agency manager. "
                "Write a 60–95 word performance assessment using exactly 5 plain bullet points (start each with a dash '-'). "
                "No stars, no bold, no markdown, no emojis, no numbers. Every bullet must be on its own line. "
                "Use the same format every time:\n"
                "- Verdict: rank position and collection rate vs team (cite exact %).\n"
                "- Strength: what this agent excels at with numbers (yield/visit, PTP, visits, or DPD bucket).\n"
                "- Weakness: exact gap vs team — name the metric and the number.\n"
                "- Trajectory: improving or declining, with exact MoM numbers.\n"
                "- Action: one concrete recommendation for the manager to act on this week.\n\n"
                + scope_stats
            )

    else:
        # ── Agency-level report ───────────────────────────────────────────
        scope = "Agency Overall"

        rows = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == month_start(month))
            .options(joinedload(AgentPerformance.agent).joinedload(Agent.user))
            .all()
        )
        prev_rows = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == month_start(prev_month))
            .all()
        )

        if not rows:
            fallback = f"No data for agency in {month}."
            prompt = fallback
            scope_stats = fallback
        else:
            total_collected = sum(float(r.total_collected or 0) for r in rows)
            total_visits = sum(int(r.total_visits or 0) for r in rows)
            avg_rate = sum(float(r.collection_rate or 0) for r in rows) / len(rows) * 100
            ptps_set = sum(int(r.ptps_set or 0) for r in rows)
            ptps_honored = sum(int(r.ptps_honored or 0) for r in rows)
            ptp_rate = ptps_honored / max(ptps_set, 1) * 100
            yield_k = total_collected / max(total_visits, 1) / 1000
            visits_per_agent = total_visits / len(rows)

            # Agent spread — sort by collection rate
            agent_rates = []
            for r in rows:
                name = "?"
                if r.agent and r.agent.user:
                    name = r.agent.user.full_name.split()[0]
                agent_rates.append((name, round(float(r.collection_rate or 0) * 100, 0)))
            prompt_names.extend(n for n, _ in agent_rates if n != "?")
            agent_rates.sort(key=lambda x: x[1], reverse=True)
            on_target = sum(1 for _, rt in agent_rates if rt >= 50)
            top3 = ", ".join(f"{n} {rt:.0f}%" for n, rt in agent_rates[:3])
            bot3 = ", ".join(f"{n} {rt:.0f}%" for n, rt in agent_rates[-3:])

            # MoM trend
            if prev_rows:
                prev_collected = sum(float(r.total_collected or 0) for r in prev_rows)
                prev_avg_rate = sum(float(r.collection_rate or 0) for r in prev_rows) / len(prev_rows) * 100
                mom_direction = "↑ improving" if avg_rate > prev_avg_rate else "↓ declining"
                mom_line = (
                    f"vs {prev_month}: ₹{prev_collected/100000:.1f}L → ₹{total_collected/100000:.1f}L | "
                    f"{prev_avg_rate:.0f}% → {avg_rate:.0f}% rate ({mom_direction})"
                )
            else:
                mom_line = "No prior month data."

            scope_stats = (
                f"Agency | {month} | {len(rows)} active agents\n"
                f"Collections: ₹{total_collected/100000:.1f}L at {avg_rate:.0f}% avg rate | "
                f"Visits: {total_visits} ({visits_per_agent:.0f}/agent) | Yield/visit: ₹{yield_k:.1f}K\n"
                f"PTP: {ptps_honored}/{ptps_set} = {ptp_rate:.0f}% conversion\n"
                f"Agent spread: {on_target}/{len(rows)} on target (≥50%) | Top: {top3} | Laggards: {bot3}\n"
                f"DPD portfolio: {dpd_lines}\n"
                f"Trend: {mom_line}"
            )

            prompt = (
                "You are a senior collection analytics advisor writing for the agency head. "
                "Write a 60–90 word performance brief using exactly 4 plain bullet points (start each with a dash '-'). "
                "No stars, no bold, no markdown, no emojis, no numbers. Every bullet must be on its own line. "
                "Use the same format every time:\n"
                "- Verdict: overall collections in ₹ and % rate vs prior month.\n"
                "- Strength: name the specific agent, DPD bucket, or metric that is performing best with exact numbers.\n"
                "- Weakness: name the specific gap or risk — agent name or metric and exact number.\n"
                "- Action: one decisive recommendation for the agency head to act on this week.\n\n"
                + scope_stats
            )

    report_text = scope_stats  # rich fallback when the model cannot answer
    _report_llm = _llm.complete(
        prompt, purpose="monthly_report", max_tokens=900, temperature=0.3,
        names=prompt_names,
    )
    if _report_llm.ai_generated and _report_llm.text:
        report_text = _report_llm.text

    return {
        "month": month, "scope": scope, "report_text": report_text,
        "ai_generated": _report_llm.ai_generated,
        "ai_status": _report_llm.status,
        "ai_failure_reason": _report_llm.failure_reason,
        # Returned so the page stops printing a hardcoded model name. It had
        # said "GPT-4o-mini" since long after that stopped being true.
        "ai_model": _report_llm.model or None,
        "ai_provider": _report_llm.provider or None,
    }


# ---------------------------------------------------------------------------
# Promise outcomes by month — how promises ENDED, by the month they fell due.
# ---------------------------------------------------------------------------

# Bucket -> the PTP statuses it holds. One place, so the card, the Promises
# card's kept rate and this endpoint cannot drift on what "kept" means:
# kept_rate = honored / (honored + broken), the two terminal answers, exactly
# as /dashboard's ptp_health computes it.
_PTP_OUTCOME_BUCKETS: dict[str, tuple[PTPStatus, ...]] = {
    "honored": (PTPStatus.HONORED,),
    "partly": (PTPStatus.PARTIALLY_HONORED,),
    "broken": (PTPStatus.BROKEN, PTPStatus.EXPIRED),
    "rescheduled": (PTPStatus.RESCHEDULED,),
    "open": (PTPStatus.ACTIVE,),
}


@router.get("/analytics/ptp-outcomes")
def get_ptp_outcomes(
    current_user: ManagerOnly,
    db: DbSession,
    months: int = 6,
    agent_id: UUIDQuery = None,
):
    """Promises grouped by the MONTH THEY FELL DUE (committed_date), split by
    how they ended: honored / partly / broken / rescheduled, plus those still
    open, with the kept rate per month.

    2026-09-18. Until the lifecycle job existed (2026-09-17) a promise never
    ended unless a payment honoured it, so this chart would have shown one
    green sliver over a sea of "open". Now that promises resolve a day after
    their grace day, the month-by-month mix is the first place the manager can
    see whether promise QUALITY is moving, which the lifetime kept rate on the
    overview cannot show.

    Keyed on committed_date, not created_at — the same choice the monthly
    PTP-conversion metric made on 2026-08-27 and for the same reason: the
    month a promise was DUE is the month its outcome belongs to, and
    created_at is a row-insert timestamp. The current month is always
    incomplete (its open promises have not fallen due), so `open` is returned
    as its own bucket for the chart to draw as pending, never folded into the
    rate. `months` is clamped to 1..24; `agent_id` must be one of this
    manager's agents (404 otherwise, like every per-agent route here).
    """
    months = max(1, min(int(months), 24))
    my_agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()]
    if agent_id:
        _require_own_agent(db, current_user, agent_id)
        scope_ids = [agent_id]
    else:
        scope_ids = my_agent_ids
    _, _, eff_date = _effective_today(my_agent_ids, db)
    # The window ends at the effective month and reaches `months` back, but
    # promises DUE after the effective month (next month's) are reported too
    # in a trailing bucket, because they are exactly what "open" means for a
    # manager looking forward.
    window = _recent_months(eff_date, months)
    if not window:
        return {"months": [], "window": [], "agent_id": agent_id}
    first_month = window[0]
    first = date(int(first_month[:4]), int(first_month[5:7]), 1)

    status_of = {st: b for b, sts in _PTP_OUTCOME_BUCKETS.items() for st in sts}
    month_of = func.to_char(PTP.committed_date, "YYYY-MM")
    rows = (
        db.query(month_of, PTP.status, func.count(PTP.id),
                 func.coalesce(func.sum(PTP.committed_amount), 0.0),
                 func.coalesce(func.sum(PTP.actual_paid_amount), 0.0))
        .filter(PTP.agent_id.in_(scope_ids) if scope_ids else sa_false(),
                PTP.committed_date >= first)
        .group_by(month_of, PTP.status)
        .all()
    )
    by_month: dict[str, dict] = {}

    def _blank(m: str) -> dict:
        return {"month": m, "total": 0, "promised_amount": 0.0, "paid_amount": 0.0,
                **{b: 0 for b in _PTP_OUTCOME_BUCKETS}, "kept_rate_pct": None, "is_current": m == eff_date.strftime("%Y-%m"),
                "is_future": m > eff_date.strftime("%Y-%m")}

    for m, st, n, promised, paid in rows:
        key = str(m)[:7]
        bucket = status_of.get(st if isinstance(st, PTPStatus) else PTPStatus(str(st)))
        if bucket is None:
            continue
        row = by_month.setdefault(key, _blank(key))
        row[bucket] += int(n or 0)
        row["total"] += int(n or 0)
        row["promised_amount"] = round(row["promised_amount"] + float(promised or 0.0), 2)
        row["paid_amount"] = round(row["paid_amount"] + float(paid or 0.0), 2)
    for m in window:
        by_month.setdefault(m, _blank(m))
    out = []
    for m in sorted(by_month):
        r = by_month[m]
        decided = r["honored"] + r["broken"]
        r["kept_rate_pct"] = round(r["honored"] / decided * 100, 1) if decided else None
        out.append(r)
    return {"months": out, "window": window, "agent_id": agent_id,
            "effective_month": eff_date.strftime("%Y-%m"),
            "definition": "kept_rate_pct = honored / (honored + broken); open promises are never in the rate"}


# ─── Device binding — the manager's reset (2026-09-24, coordinator audit gate 3) ─
# auth_service's header promised this action ("the manager's reset device
# binding action (G01) unbinds") and no route existed, so an agent who lost a
# phone was locked out until someone edited the database. It unbinds every
# bound device, revokes every live login session of the agent (reason
# DEVICE_RESET) so a stolen phone's refresh token dies with the binding, and
# writes a DEVICE_RESET audit row. 404 for an agent that is not yours.

@router.post("/agents/{agent_id}/reset-device")
def reset_agent_device(agent_id: UUIDPath, current_user: ManagerOnly, db: DbSession):
    from app.core.audit import write_audit
    from app.models.agent import AgentDevice
    from app.models.audit_log import AuditAction
    from app.services.auth_service import revoke_user_sessions

    agent = _require_own_agent(db, current_user, agent_id)
    now = datetime.now(timezone.utc)
    devices = (db.query(AgentDevice)
               .filter(AgentDevice.agent_id == agent.id, AgentDevice.is_bound.is_(True)).all())
    for d in devices:
        d.is_bound = False
        d.unbound_at = now
        d.unbound_by = current_user.id
        d.unbind_reason = "Manager reset"
    revoked = revoke_user_sessions(db, agent.user_id, "DEVICE_RESET", by=current_user.id)
    db.commit()
    write_audit(db, action=AuditAction.DEVICE_RESET, user_id=current_user.id, entity_type="agent",
                entity_id=agent.id, details={"devices_unbound": len(devices), "sessions_revoked": revoked})
    return {"agent_id": agent.id, "devices_unbound": len(devices), "sessions_revoked": revoked,
            "reset_at": now.isoformat()}

# ---------------------------------------------------------------------------
# Leave requests — the manager's side (2026-09-21).
# ---------------------------------------------------------------------------
# List, approve, reject, revoke, and record leave directly ("mark"). Every row
# is scoped on LeaveRequest.manager_user_id, which the service copies from
# Agent.manager_user_id at write time; `mark` goes through _require_own_agent.

class _LeaveDecisionBody(_BM):
    note: Optional[str] = None


class _MarkLeaveBody(_BM):
    from_date: date
    to_date: date
    leave_type: str      # SICK_LEAVE | CASUAL_LEAVE | EARNED_LEAVE | ABSENT
    reason: Optional[str] = None


def _leave_names(db, requests) -> dict[str, str]:
    ids = {r.agent_id for r in requests}
    if not ids:
        return {}
    rows = db.query(Agent.id, User.full_name).join(User, Agent.user_id == User.id).filter(Agent.id.in_(ids)).all()
    return {a: n for a, n in rows}


@router.get("/leave-requests")
def list_leave_requests(current_user: ManagerOnly, db: DbSession, status: Optional[str] = None):
    """This manager's agents' leave requests, pending first. `status` narrows
    to one of REQUESTED / APPROVED / REJECTED / CANCELLED."""
    from app.services.leave_service import LeaveService, serialize
    my_agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()]
    rows = LeaveService(db).list_for_manager(current_user.id, status)
    rows = [r for r in rows if r.agent_id in set(my_agent_ids)]
    names = _leave_names(db, rows)
    pending = sum(1 for r in rows if r.status.value == "REQUESTED")
    return {"requests": [serialize(r, names.get(r.agent_id)) for r in rows], "pending": pending}


@router.post("/leave-requests/{request_id}/approve")
def approve_leave_request(request_id: UUIDPath, body: _LeaveDecisionBody, current_user: ManagerOnly, db: DbSession):
    from app.services.leave_service import LeaveService, serialize
    out = LeaveService(db).approve(current_user.id, request_id, body.note)
    r = out["request"]
    return {**serialize(r, _leave_names(db, [r]).get(r.agent_id)), "cases_released_to_pool": out["cases_released"]}


@router.post("/leave-requests/{request_id}/reject")
def reject_leave_request(request_id: UUIDPath, body: _LeaveDecisionBody, current_user: ManagerOnly, db: DbSession):
    from app.services.leave_service import LeaveService, serialize
    r = LeaveService(db).reject(current_user.id, request_id, body.note)
    return serialize(r, _leave_names(db, [r]).get(r.agent_id))


@router.post("/leave-requests/{request_id}/revoke")
def revoke_leave_request(request_id: UUIDPath, body: _LeaveDecisionBody, current_user: ManagerOnly, db: DbSession):
    from app.services.leave_service import LeaveService, serialize
    r = LeaveService(db).revoke(current_user.id, request_id, body.note)
    return serialize(r, _leave_names(db, [r]).get(r.agent_id))


@router.post("/agents/{agent_id}/leave", status_code=201)
def mark_agent_leave(agent_id: UUIDPath, body: _MarkLeaveBody, current_user: ManagerOnly, db: DbSession):
    """Record leave for one of this manager's agents, approved in one step.
    ABSENT is the manager's word for a no-show and may be back-dated."""
    from app.models.leave_request import LeaveType
    from app.services.leave_service import LeaveService, serialize
    agent = _require_own_agent(db, current_user, agent_id)
    try:
        lt = LeaveType(body.leave_type)
    except ValueError:
        raise HTTPException(status_code=422, detail="leave_type must be SICK_LEAVE, CASUAL_LEAVE, EARNED_LEAVE or ABSENT")
    out = LeaveService(db).mark(current_user.id, agent, from_date=body.from_date, to_date=body.to_date, leave_type=lt, reason=body.reason)
    r = out["request"]
    return {**serialize(r, agent.user.full_name if agent.user else None), "cases_released_to_pool": out["cases_released"]}


# ─── Smart Nightly Case Allocation Endpoints ─────────────────────────────────

@router.get("/allocation/latest")
def get_latest_allocation_plan(
    current_user: ManagerOnly,
    db: DbSession,
    plan_date: Optional[str] = None,
):
    """Fetch the latest planned case allocation run and explainable decisions for tomorrow."""
    from app.services.planner_service import PlannerService, get_target_plan_date
    from app.models.allocation_run import AllocationRun
    from app.models.allocation_decision import AllocationDecision
    from app.models.beat import Beat

    target_d = date.fromisoformat(plan_date) if plan_date else get_target_plan_date()
    planner = PlannerService(db, manager_user_id=current_user.id)
    run = planner.get_latest_plan(plan_date=target_d)

    # A failed nightly run is reported alongside the plan, never as one. Before
    # 2026-09-03 a crash in the 20:00 task wrote nothing at all, so the manager
    # saw the same 'no plan generated yet' as on a quiet night and had no way to
    # tell 'nothing to do' from 'the job died'. get_last_failure() returns None
    # once a later successful run exists, so a manager who re-planned by hand is
    # not chased by a resolved error.
    failure = planner.get_last_failure(plan_date=target_d)
    failure_payload = None
    if failure is not None:
        meta = failure.summary_metadata or {}
        failure_payload = {
            "run_id": failure.id,
            "failed_at": failure.created_at.isoformat() if failure.created_at else None,
            "error_type": meta.get("error_type"),
            "error": meta.get("error"),
            "trigger": meta.get("trigger"),
        }

    if not run:
        return {
            "has_plan": False,
            "target_date": target_d.isoformat(),
            "message": (
                f"The {target_d.isoformat()} allocation run failed and no plan "
                f"was created. Re-plan to try again."
            ) if failure_payload else (
                f"No allocation plan generated yet for {target_d.isoformat()}."
            ),
            "run": None,
            "last_failure": failure_payload,
        }

    # Fetch beats for this run
    beats = (
        db.query(Beat)
        .options(joinedload(Beat.agent).joinedload(Agent.user))
        .filter(Beat.allocation_run_id == run.id)
        .all()
    )

    beat_list = []
    for b in beats:
        agent_name = b.agent.user.full_name if b.agent and b.agent.user else "Agent"
        agent_code = b.agent.employee_code if b.agent else ""
        beat_list.append({
            "beat_id": b.id,
            "agent_id": b.agent_id,
            "agent_name": agent_name,
            "agent_code": agent_code,
            "beat_number": b.beat_number,
            "total_cases": b.total_cases,
            "estimated_distance_km": b.estimated_distance_km,
            "estimated_duration_minutes": b.estimated_duration_minutes,
            "total_target_amount": b.total_target_amount,
            "status": b.status.value,
        })

    # Fetch all decisions for this run: ALLOCATED first, DEFERRED second, BLOCKED at bottom
    decisions = (
        db.query(AllocationDecision)
        .options(
            joinedload(AllocationDecision.case),
            joinedload(AllocationDecision.allocated_agent).joinedload(Agent.user),
        )
        .filter(AllocationDecision.run_id == run.id)
        .all()
    )

    outcome_order = {
        "ALLOCATED": 1,
        "DEFERRED": 2,
        "DEFERRED_ROUTE_INFEASIBLE": 2,
        "BLOCKED": 3,
    }
    decisions.sort(key=lambda d: (outcome_order.get(str(d.outcome), 2), -(float(d.visit_priority_score or 0))))

    # One query for every prediction these decisions point at, rather than one
    # per decision inside the loop below. The run holds ~900 decisions and this
    # endpoint is on the manager's landing page.
    from app.models.model_prediction import ModelPrediction

    _pred_ids = [d.model_prediction_id for d in decisions if d.model_prediction_id]
    predictions_by_id = {}
    if _pred_ids:
        predictions_by_id = {
            p.id: p for p in db.query(ModelPrediction)
            .filter(ModelPrediction.id.in_(_pred_ids)).all()
        }

    # ── what the plan is worth, beside what it is forecast to bring in ──────
    # 2026-09-10. The card showed `expected_recovery_total` alone, and a manager
    # comparing it with the dashboard's "Today's Collections" saw Rs 10.3L
    # against Rs 71.2L and reasonably asked why the plan had written off 86% of
    # the book. It had not: the two figures answer different questions, and
    # neither card said which.
    #
    # BOTH DENOMINATORS ARE RETURNED, because they are not interchangeable:
    #   target      sum(Case.target_amount) — the LIFETIME figure the dashboard
    #               uses. It includes money banked in earlier months, so a single
    #               day can never collect it; see the note on amount_target_today.
    #   collectable sum(target_amount - collected_amount) — what is actually
    #               still owed, and the exact base the allocator multiplies by
    #               `prob_recovery_ml` to produce expected_recovery_total.
    #
    # Only `collectable` divides into the expected figure to give the model's own
    # recovery rate. Dividing by `target` gives a different, smaller number
    # corresponding to nothing the model computed — the same trap the decision
    # panel fell into on 2026-09-09, when it explained a rupee figure with a rate
    # that had not produced it.
    #
    # ALLOCATED ONLY: a deferred or blocked case is not on tomorrow's plan, so
    # its balance is not part of what tomorrow's plan is worth.
    #
    # Summed here rather than stored on AllocationRun — the Case rows are already
    # loaded for the rows below, so this costs no extra query, needs no
    # migration, and works on plans built before this code existed.
    allocated_target_total = 0.0
    allocated_collectable_total = 0.0
    # 2026-09-16. The same balance, per agent, so the beat cards can show what
    # each agent is going after in the units the KPI above them uses. Beat
    # carries `total_target_amount` — the LIFETIME target, summed by the planner
    # — and fifteen cards of that summed to ~Rs 71L under a tile reading
    # Rs 63.8L, with nothing on screen explaining the gap (money already banked).
    # Summed from the decision rows already loaded, keyed on the agent the case
    # actually went to, so it survives an exploration swap.
    collectable_by_agent: dict[str, float] = {}

    decision_list = []
    for d in decisions:
        case_num = d.case.case_number if d.case else ""
        target_amt = float(d.case.target_amount or 0) if d.case else 0.0
        collectable_amt = (
            max(0.0, target_amt - float(d.case.collected_amount or 0)) if d.case else 0.0
        )
        if str(d.outcome) == "ALLOCATED" and d.case:
            allocated_target_total += target_amt
            allocated_collectable_total += collectable_amt
            if d.allocated_agent_id:
                collectable_by_agent[d.allocated_agent_id] = (
                    collectable_by_agent.get(d.allocated_agent_id, 0.0) + collectable_amt
                )
        agent_name = (
            d.allocated_agent.user.full_name
            if d.allocated_agent and d.allocated_agent.user
            else (d.allocated_agent.employee_code if d.allocated_agent else "")
        )
        # ── the ML block ────────────────────────────────────────────────────
        # 2026-09-09. The response carried ML-DERIVED numbers — the assignment
        # and the rupee figure both come from the model — while exposing nothing
        # that said so. A client could render a model-driven decision with no
        # way to know it was one, and no way to trace it back to the score.
        #
        # Read from `score_breakdown`, which the allocator already writes, plus
        # the lineage column. Nothing is recomputed and nothing is duplicated
        # that the caller could derive: `expected_recovery_inr` is the
        # allocator's own `expected_case_inr`, surfaced under a name a client
        # can read without knowing the allocator's internals.
        #
        # `model_version` comes off the PREDICTION ROW, never a constant: a
        # hardcoded version would keep reporting 1.1.0 through a rollback.
        _bd = d.score_breakdown or {}
        _pred = predictions_by_id.get(d.model_prediction_id)
        decision_list.append({
            "decision_id": d.id,
            "case_id": d.case_id,
            "case_number": case_num,
            "target_amount": target_amt,
            # What is still owed on the case — target less collected — the base
            # the allocator multiplies. The decision panel prints this as the
            # case's recovery figure (2026-09-16, product direction), so it is
            # sent rather than left for the client to derive from two fields.
            "collectable_amount": round(collectable_amt, 2),
            "outcome": d.outcome,
            "allocated_agent_id": d.allocated_agent_id,
            "allocated_agent_name": agent_name,
            "visit_priority_score": d.visit_priority_score,
            "fit_score": d.fit_score,
            "reason": d.reason,
            "score_breakdown": d.score_breakdown,
            "ml": {
                # Did the model actually drive this decision, or was it computed
                # and shadowed? The allocator records exactly this.
                "used_for_decision": bool(_bd.get("ml_used_for_decision")),
                # The probability the objective multiplied by. `None` when the
                # model did not drive it — the caller must not silently fall
                # back to the shadow value.
                "probability_used": _bd.get("prob_recovery_ml"),
                # Borrower-side P(recover) before the agent adjustment.
                "borrower_p_recover": _bd.get("ml_borrower_p_recover"),
                # The shadow figure, named so nobody mistakes it for the live
                # one. Kept because the promotion is only readable beside it.
                "shadow_prob_recovery": _bd.get("prob_recovery"),
                "value_transform": _bd.get("value_transform"),
                "expected_recovery_inr": _bd.get("expected_case_inr"),
                "prediction_id": d.model_prediction_id,
                "model_name": _pred.model_name if _pred else None,
                "model_version": _pred.model_version if _pred else None,
                "feature_coverage": _pred.feature_coverage if _pred else None,
            },
        })

    for _b in beat_list:
        _b["total_collectable_amount"] = round(collectable_by_agent.get(_b["agent_id"], 0.0), 2)

    return {
        "has_plan": True,
        # Carried on the success path too. A plan can exist AND the most recent
        # attempt have failed — a manager who re-plans by hand after a failed
        # nightly still wants to know the nightly is broken, because tomorrow it
        # will fail again. get_last_failure() suppresses it once a successful run
        # is newer than the failure, so this is only ever a live problem.
        "last_failure": failure_payload,
        "run_id": run.id,
        "plan_date": run.plan_date.isoformat(),
        "strategy": run.strategy,
        "status": run.status,
        "total_cases_evaluated": run.total_cases_evaluated,
        "total_cases_allocated": run.total_cases_allocated,
        "total_cases_deferred": run.total_cases_deferred,
        "total_cases_blocked": run.total_cases_blocked,
        "total_agents_planned": run.total_agents_planned,
        "expected_recovery_total": run.expected_recovery_total,
        # The two bases for the figure above. `expected_recovery_total /
        # allocated_collectable_total` is the model's own mean recovery rate;
        # dividing by the target is not.
        "allocated_target_total": round(allocated_target_total, 2),
        "allocated_collectable_total": round(allocated_collectable_total, 2),
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "beats": beat_list,
        "decisions": decision_list,
    }


@router.post("/allocation/plan")
def create_or_simulate_allocation_plan(
    current_user: ManagerOnly,
    db: DbSession,
    req: Optional[dict] = None,
):
    """Trigger on-demand next-day planning, simulation, or replan."""
    from app.services.planner_service import (
        PlanInProgressError,
        PlannerService,
        get_target_plan_date,
    )

    req = req or {}
    strategy = req.get("strategy", "SMART")
    objective = req.get("objective", "BALANCED")
    plan_date_str = req.get("plan_date")
    simulate = bool(req.get("simulate", False))
    force_replan = bool(req.get("force_replan", True))

    target_d = date.fromisoformat(plan_date_str) if plan_date_str else get_target_plan_date()
    planner = PlannerService(db, manager_user_id=current_user.id)

    try:
        run = planner.plan_next_day(
            plan_date=target_d,
            strategy=strategy,
            objective=objective,
            simulate=simulate,
            force_replan=force_replan,
        )
    except PlanInProgressError as e:
        # 409, not 500. Two planning runs for the same manager and date used to
        # collide on the UNIQUE (agent_id, beat_date) index and surface as an
        # opaque "Failed to generate plan" with the run rolled back. The request
        # was valid; the timing was not, and the caller can simply retry.
        raise HTTPException(status_code=409, detail=str(e))
    except IntegrityError:
        # The advisory lock covers Postgres. This is the belt to its braces: if
        # a collision still lands, say so in a way a person can act on rather
        # than returning a database error.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A conflicting plan was written while this one was being "
                   "generated. Nothing was changed — please try again.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "run_id": run.id,
        "plan_date": run.plan_date.isoformat(),
        "strategy": run.strategy,
        "objective": objective,
        "status": run.status,
        "total_cases_evaluated": run.total_cases_evaluated,
        "total_cases_allocated": run.total_cases_allocated,
        "total_cases_deferred": run.total_cases_deferred,
        "total_cases_blocked": run.total_cases_blocked,
        "total_agents_planned": run.total_agents_planned,
        "expected_recovery_total": run.expected_recovery_total,
        "is_simulated": simulate,
    }


@router.get("/allocation/settings")
def get_allocation_settings(current_user: ManagerOnly, db: DbSession):
    """Retrieve active allocation policy and objective settings."""
    from app.models.allocation_setting import AllocationSetting
    setting = db.query(AllocationSetting).filter(AllocationSetting.manager_user_id == current_user.id).first()
    if not setting:
        return {
            "objective": "BALANCED",
            "max_territory_radius_km": 16.0,
            "max_daily_stops_per_agent": 12,
        }
    return {
        "objective": setting.objective,
        "max_territory_radius_km": setting.max_territory_radius_km,
        "max_daily_stops_per_agent": setting.max_daily_stops_per_agent,
    }


@router.post("/allocation/settings")
def update_allocation_settings(req: dict, current_user: ManagerOnly, db: DbSession):
    """Update active allocation policy settings."""
    from app.models.allocation_setting import AllocationSetting
    import uuid

    objective = req.get("objective", "BALANCED")
    radius = float(req.get("max_territory_radius_km", 16.0))
    max_stops = int(req.get("max_daily_stops_per_agent", 12))

    setting = db.query(AllocationSetting).filter(AllocationSetting.manager_user_id == current_user.id).first()
    if not setting:
        setting = AllocationSetting(
            id=str(uuid.uuid4()),
            manager_user_id=current_user.id,
            objective=objective,
            max_territory_radius_km=radius,
            max_daily_stops_per_agent=max_stops,
            custom_weights={},
        )
        db.add(setting)
    else:
        setting.objective = objective
        setting.max_territory_radius_km = radius
        setting.max_daily_stops_per_agent = max_stops

    db.commit()
    return {
        "success": True,
        "objective": setting.objective,
        "max_territory_radius_km": setting.max_territory_radius_km,
        "max_daily_stops_per_agent": setting.max_daily_stops_per_agent,
    }


@router.post("/allocation/rollback")
def rollback_allocation_plan(
    current_user: ManagerOnly,
    db: DbSession,
    req: dict,
):
    """Roll back an untouched future PLANNED allocation."""
    from app.services.planner_service import PlannerService

    run_id = req.get("run_id")
    if not run_id:
        raise HTTPException(status_code=422, detail="run_id is required.")

    planner = PlannerService(db, manager_user_id=current_user.id)
    try:
        success = planner.rollback_plan(run_id=run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "success": success,
        "run_id": run_id,
        "message": "Allocation plan successfully rolled back.",
    }


@router.get("/allocation/export-decisions")
def export_allocation_decisions_csv(
    run_id: UUIDQueryRequired,
    current_user: ManagerOnly,
    db: DbSession,
):
    """Export the explainable allocation decisions for a specific run as CSV.

    Ownership is enforced inside export_decisions_csv, not here — see the note
    there on why the structural tenancy sweep could not catch this one.
    """
    from fastapi.responses import Response
    from app.services.planner_service import PlannerService

    planner = PlannerService(db, manager_user_id=current_user.id)
    try:
        csv_text = planner.export_decisions_csv(run_id=run_id)
    except ValueError:
        # 404 rather than 403 — a 403 would confirm the run id exists.
        # No audit row on this path: nothing was exported, and logging a
        # DATA_EXPORT for a refused request would make the trail read as though
        # another agency's run had been handed over.
        raise HTTPException(status_code=404, detail="Allocation run not found")

    # Written only after the ownership check inside export_decisions_csv has
    # passed — see the 2026-09-10 note on the audit-log export above.
    db.add(AuditLog(
        created_at=datetime.now(timezone.utc), user_id=current_user.id,
        action=AuditAction.DATA_EXPORT,
        entity_type="AllocationRun", entity_id=run_id,
        details={"format": "csv", "rows": max(0, len(csv_text.splitlines()) - 1),
                 "endpoint": "/manager/allocation/export-decisions"},
        success=True))
    db.commit()

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=allocation_decisions_{run_id}.csv"},
    )


# ---------------------------------------------------------------------------
# Manager reassignment — the ONE sanctioned way to move an owned case
# ---------------------------------------------------------------------------
# Added 2026-09-11 with sticky case ownership. Until then a case could change
# agent three ways and none of them asked anybody: the nightly Hungarian
# re-solve (35 of 214 allocated cases in one measured night), the 10%
# exploration swap, and any agent who recorded a visit or payment on it. The
# first two now hold an owned case in place; the third is deliberately left
# as it was — a separate product decision, see visit_service.py:112.
#
# So this endpoint is where a case moves on purpose — an EXISTING owner to a
# new one. It is not how a case gets its first agent: that is the nightly
# allocator's job, and an UNASSIGNED case is refused here with a sentence
# saying so. Three things follow:
#
#   * a REASON is mandatory and whitespace does not count. The audit trail is
#     the product here; a reassignment nobody can explain later is exactly the
#     silent churn the change removed.
#   * the incoming agent is re-checked against the SAME hard gates the nightly
#     run applies — case_bar() and pair_bar() from global_allocator, not a
#     restatement — so a manager cannot hand a borrower to an agent the
#     safety, territory or PTP-fatigue rules exclude. Ownership is the one
#     gate NOT applied, because a manager moving a case is the override.
#   * it writes AuditAction.CASE_ASSIGNED, which had been declared since the
#     first schema and written by nothing.
#
# What it does NOT do, on purpose: it does not touch today's Beat. The move
# takes effect at the next nightly plan; if the case is on the outgoing
# agent's beat right now it stays there for the rest of the day. Rewriting a
# planned route mid-day is its own change with its own failure modes.

from pydantic import BaseModel as _ReassignBase, field_validator as _reassign_validator


class _ReassignBody(_ReassignBase):
    new_agent_id: UUIDStr
    reason: str

    @_reassign_validator("reason")
    @classmethod
    def _reason_must_say_something(cls, v: str) -> str:
        # Rejected at the schema boundary so an empty reason is a 422 with a
        # field name, not a 400 with prose — and so no handler can forget.
        if v is None or not v.strip():
            raise ValueError("reason is required and may not be blank")
        return v.strip()


def _latest_reassignments(db, case_ids: list[str]) -> dict[str, dict]:
    """case_id -> the most recent manager reassignment, or absent.

    One batched query for the whole page. Read from the audit trail rather
    than a new column, because the trail already holds from/to/reason/who/when
    and a second copy on Case would be one more thing to keep in step.
    """
    if not case_ids:
        return {}
    rows = (
        db.query(AuditLog, User.full_name)
        .outerjoin(User, User.id == AuditLog.user_id)
        .filter(AuditLog.action == AuditAction.CASE_ASSIGNED,
                AuditLog.entity_type == "Case",
                AuditLog.entity_id.in_(case_ids))
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    out: dict[str, dict] = {}
    for log, who in rows:
        if log.entity_id in out:
            continue                      # newest first; keep only the newest
        d = log.details or {}
        out[log.entity_id] = {
            "reason": d.get("reason"),
            "from_agent_id": (log.old_values or {}).get("agent_id"),
            "from_agent_name": d.get("from_agent_name"),
            "to_agent_id": (log.new_values or {}).get("agent_id"),
            "to_agent_name": d.get("to_agent_name"),
            "by": who,
            "at": log.created_at.isoformat() if log.created_at else None,
        }
    return out


@router.post("/cases/{case_id}/reassign")
def reassign_case(
    case_id: UUIDPath,
    body: _ReassignBody,
    current_user: ManagerOnly,
    db: DbSession,
):
    from app.models.case import RESOLVED_STATUSES
    from app.services.global_allocator import (
        BAR_MESSAGES, DEFAULT_TERRITORY_RADIUS_KM, GlobalAllocator,
        case_bar, pair_bar)
    from app.services.planner_service import PlannerService

    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    # The case must be inside this manager's pool — held by one of their agents,
    # or unassigned. 404 rather than 403 so the id cannot be used to probe
    # another agency.
    #
    # Unassigned cases are looked up ON PURPOSE, and then refused below with a
    # sentence rather than falling into this 404. They are visible to every
    # manager through GET /cases/unallocated, so there is nothing to hide, and
    # "not found" would send somebody hunting for a scoping bug when the real
    # answer is "not yet — wait for tonight".
    case = (
        db.query(Case)
        .options(joinedload(Case.customer))
        .filter(Case.id == case_id,
                or_(Case.agent_id.in_(my_agent_ids), Case.agent_id.is_(None)))
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # PRODUCT INVARIANT, 2026-09-11: an UNASSIGNED case is a NEW case. Its
    # lifecycle is UNASSIGNED -> nightly allocator -> ASSIGNED + owner ->
    # manager may REASSIGN. This endpoint moves ownership that already exists;
    # it is not a first-assignment mechanism, and the first version of it was
    # one by accident — the lookup above was copied from the planner's pool
    # filter, which rightly includes unassigned cases because the planner is
    # what assigns them. Nothing in the product could reach that state (the
    # case list and detail both filter on agent_id IN my agents, so the
    # Reassign button cannot be opened for an unowned case), but a hand-made
    # request could, and it left a case with an agent and a status of
    # UNASSIGNED. The tempting fix — set status ASSIGNED here — would have
    # quietly turned reassignment into manual first assignment. Refused
    # instead. The status is NOT touched by this endpoint on any path.
    if case.agent_id is None:
        raise HTTPException(
            status_code=409,
            detail=("This case has no agent yet — tonight's plan will assign it. "
                    "Only an assigned case can be reassigned."))

    if case.status in RESOLVED_STATUSES or case.status == CaseStatus.PAID:
        raise HTTPException(status_code=409,
                            detail="This case is resolved and cannot be reassigned.")

    incoming = _require_own_agent(db, current_user, body.new_agent_id)
    if case.agent_id == incoming.id:
        raise HTTPException(status_code=409,
                            detail="The case is already assigned to this agent.")

    # ── The same gates the nightly run applies, in the same order ──────────
    blocked = case_bar(case.customer)
    if blocked is not None:
        raise HTTPException(status_code=409, detail=BAR_MESSAGES[blocked])

    cust = case.customer
    cust_lat = cust.latitude if cust.latitude is not None else GlobalAllocator.FALLBACK_LAT
    cust_lon = cust.longitude if cust.longitude is not None else GlobalAllocator.FALLBACK_LON
    base_lat = incoming.base_latitude if incoming.base_latitude is not None else GlobalAllocator.FALLBACK_LAT
    base_lon = incoming.base_longitude if incoming.base_longitude is not None else GlobalAllocator.FALLBACK_LON
    dist_km = GlobalAllocator.haversine_km(base_lat, base_lon, cust_lat, cust_lon)
    fatigued = PlannerService(db, manager_user_id=current_user.id) \
        ._ptp_fatigue_map([case.id]).get(case.id, set())
    barred = pair_bar(case, cust, incoming, dist_km=dist_km,
                      territory_radius_km=DEFAULT_TERRITORY_RADIUS_KM,
                      fatigued_agent_ids=fatigued, enforce_ownership=False)
    if barred is not None:
        raise HTTPException(status_code=409, detail=BAR_MESSAGES[barred])

    # ── Move it, and say so ─────────────────────────────────────────────────
    from_id = case.agent_id
    names = dict(
        db.query(Agent.id, User.full_name).join(User, User.id == Agent.user_id)
        .filter(Agent.id.in_([x for x in (from_id, incoming.id) if x])).all()
    )
    now = datetime.now(timezone.utc)
    case.agent_id = incoming.id
    db.add(AuditLog(
        created_at=now, user_id=current_user.id,
        action=AuditAction.CASE_ASSIGNED,
        entity_type="Case", entity_id=case.id,
        old_values={"agent_id": from_id},
        new_values={"agent_id": incoming.id},
        details={"reason": body.reason,
                 "from_agent_id": from_id,
                 "from_agent_name": names.get(from_id),
                 "to_agent_id": incoming.id,
                 "to_agent_name": names.get(incoming.id),
                 "source": "manager_reassign",
                 "distance_km": round(dist_km, 1)},
        success=True))
    db.commit()

    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "from_agent_id": from_id,
        "from_agent_name": names.get(from_id),
        "to_agent_id": incoming.id,
        "to_agent_name": names.get(incoming.id),
        "reason": body.reason,
        "reassigned_at": now.isoformat(),
        "takes_effect": "next nightly plan; today's beat is unchanged",
    }
