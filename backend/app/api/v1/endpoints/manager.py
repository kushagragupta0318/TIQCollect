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
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import func
# Aliased: `Case` in this module is the SQLAlchemy model for a collections
# case, so importing the SQL CASE construct under its own name would read as
# the model with a typo.
from sqlalchemy import case as sa_case
from sqlalchemy.orm import joinedload

from app.core.dependencies import DbSession, ManagerOnly
from app.core.config import settings
from app.core import llm as _llm
from app.ml import eligibility as _elig
from app.models.agent import Agent, AgentStatus, AgentPerformance
from app.core import storage
from app.models.beat import Beat
from app.models.case import Case, CaseStatus
from app.models.loan import DPDBucket, Loan
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.user import User
from app.models.visit import Visit
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/manager", tags=["manager"])


def _effective_today(agent_ids: list[str], db) -> tuple[datetime, datetime, date]:
    """Return (start_of_day, end_of_day, eff_date) scoped to the most recent
    beat date across the given agents.  Falls back to date.today() when no
    beats exist yet (fresh install without a seed).
    """
    row = (
        db.query(func.max(Beat.beat_date))
        .filter(Beat.agent_id.in_(agent_ids))
        .scalar()
    )
    eff_date: date = row if row else date.today()
    start = datetime.combine(eff_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end   = datetime.combine(eff_date, datetime.max.time()).replace(tzinfo=timezone.utc)
    return start, end, eff_date


def _format_case(case: Case, agent_name_map: dict | None = None, visited_today_ids: set[str] | None = None) -> dict:
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
    agents_on_duty = (
        db.query(func.count(Agent.id))
        .filter(Agent.id.in_(my_agent_ids), Agent.status == AgentStatus.ON_DUTY)
        .scalar() or 0
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
    # Distinct cases visited today
    visited_case_ids = [
        row[0] for row in
        db.query(Visit.case_id)
        .filter(Visit.agent_id.in_(my_agent_ids), Visit.check_in_time >= start_of_day)
        .distinct()
        .all()
    ]
    cases_today = len(visited_case_ids)
    amount_target_today = (
        db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
        .filter(Case.id.in_(visited_case_ids))
        .scalar() or 0.0
    ) if visited_case_ids else 0.0

    # Return as percentage (0-100) so the frontend doesn't need to multiply
    collection_rate_pct = (
        round(amount_collected_today / amount_target_today * 100, 1) if amount_target_today > 0 else 0.0
    )

    return {
        "total_agents": total_agents,
        "agents_on_duty": agents_on_duty,
        "total_cases": total_cases,
        "cases_assigned": cases_assigned,
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
# GET /manager/agents
# ---------------------------------------------------------------------------

# How many complete months the collection-rate sparkline covers. Five keeps the
# mark readable at ~60px wide; the seed carries six months of history, of which
# the newest is the in-progress month and is deliberately excluded.
_TREND_MONTHS = 5


def _complete_months_before(anchor: date, count: int) -> list[str]:
    """The `count` complete months immediately before `anchor`'s own month.

    Oldest first, as "YYYY-MM" to match AgentPerformance.month. `anchor`'s month
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
      ptps_honored  PTPs RAISED in that month that are now honoured. Scoping to
                    the month matters: the snapshot task counts every honoured
                    PTP the agent has ever set against a single month's total,
                    which climbs forever and can exceed ptps_set.

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
                   "ptps_set": 0, "ptps_honored": 0} for mo in months}
        for aid in agent_ids
    }

    def _month_of(col):
        # Postgres-only, like the enum types and psycopg2 driver this app
        # already depends on. Matches AgentPerformance.month's "YYYY-MM" form.
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

    # 2. Visit count
    visit_month = _month_of(Visit.check_in_time)
    for aid, mo, n in (
        db.query(Visit.agent_id, visit_month, func.count(Visit.id))
        .filter(Visit.agent_id.in_(agent_ids), Visit.check_in_time >= window_start)
        .group_by(Visit.agent_id, visit_month)
        .all()
    ):
        if mo in wanted and aid in out:
            out[aid][mo]["visits"] = int(n or 0)

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

    # 4. PTPs raised in the month, and how many of those are now honoured.
    ptp_month = _month_of(PTP.created_at)
    for aid, mo, n_set, n_hon in (
        db.query(
            PTP.agent_id,
            ptp_month,
            func.count(PTP.id),
            func.count(sa_case((PTP.status == PTPStatus.HONORED, PTP.id))),
        )
        .filter(PTP.agent_id.in_(agent_ids), PTP.created_at >= window_start)
        .group_by(PTP.agent_id, ptp_month)
        .all()
    ):
        if mo in wanted and aid in out:
            out[aid][mo]["ptps_set"] = int(n_set or 0)
            out[aid][mo]["ptps_honored"] = int(n_hon or 0)

    # Derive the rate once, here, so no caller can reinvent it differently.
    for per_month in out.values():
        for stats in per_month.values():
            stats["rate_pct"] = (
                round(stats["collected"] / stats["target"] * 100, 1)
                if stats["target"] > 0 else 0.0
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
    start_of_day, _, eff_date = _effective_today(agent_ids, db)
    eff_date_str = eff_date.isoformat()

    result = []
    for agent in agents:
        cases_today = (
            db.query(func.count(Case.id))
            .filter(Case.agent_id == agent.id, Case.allocation_date == eff_date_str)
            .scalar() or 0
        )
        # VERIFIED only, matching the monthly figures below. Without the filter
        # this counted PENDING_VERIFICATION too — money the borrower has not yet
        # confirmed by OTP — so "collected today" could exceed the month's
        # collected total, which is drawn from verified payments alone.
        today_collected = (
            db.query(func.coalesce(func.sum(Payment.amount), 0.0))
            .filter(Payment.agent_id == agent.id,
                    Payment.payment_date >= start_of_day,
                    Payment.status == PaymentStatus.VERIFIED)
            .scalar() or 0.0
        )
        agent_visited_ids = [
            row[0] for row in
            db.query(Visit.case_id)
            .filter(Visit.agent_id == agent.id, Visit.check_in_time >= start_of_day)
            .distinct()
            .all()
        ]
        today_target = (
            db.query(func.coalesce(func.sum(Case.target_amount), 0.0))
            .filter(Case.id.in_(agent_visited_ids))
            .scalar() or 0.0
        ) if agent_visited_ids else 0.0
        result.append({
            "id": agent.id,
            "user_id": agent.user_id,
            "employee_code": agent.employee_code,
            "id_card_number": agent.id_card_number,
            "full_name": agent.user.full_name,
            "date_of_birth": agent.user.date_of_birth,
            "territory": agent.territory,
            "tier": agent.tier,
            "status": agent.status,
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

@router.get("/cases")
def list_cases(
    current_user: ManagerOnly,
    db: DbSession,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    agent_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    my_agent_ids = [
        a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()
    ]
    q = (db.query(Case)
         .filter(Case.agent_id.in_(my_agent_ids))
         .options(joinedload(Case.customer), joinedload(Case.loan)))

    if status:
        q = q.filter(Case.status == status)
    if priority:
        q = q.filter(Case.priority == priority)
    if agent_id:
        q = q.filter(Case.agent_id == agent_id)
    if date_from:
        q = q.filter(Case.allocation_date >= date_from)
    if date_to:
        q = q.filter(Case.allocation_date <= date_to)

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
    if page_case_ids and settings.DEMO_VISITED_BY_ALLOCATION_DATE:
        # Demo seam: match each visit against its own case's allocation_date —
        # the value the list shows in its Date column — instead of the wall
        # clock. Seeded activity is stamped with the day the seed ran, so by
        # the day of the demo nothing matches "today" and every chip vanishes.
        #
        # Compared in Python rather than SQL: allocation_date is a plain
        # "YYYY-MM-DD" string column, so a date_trunc/cast comparison would be
        # dialect-specific for no gain over a page's worth of rows.
        allocation_of = {c.id: c.allocation_date for c in cases}
        visited_today_ids = {
            case_id for case_id, check_in in
            db.query(Visit.case_id, Visit.check_in_time)
            .filter(Visit.case_id.in_(page_case_ids))
            .all()
            if check_in and allocation_of.get(case_id) == check_in.date().isoformat()
        }
    elif page_case_ids:
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

    return {
        "total": total,
        "cases": [_format_case(c, agent_name_map, visited_today_ids) for c in cases],
    }


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
    on_duty = [a for a in my_agents if a.status == AgentStatus.ON_DUTY]

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
            # Deliberately does not say WHY an agent is ineligible. The only
            # current reason is the customer's female-agent requirement, and
            # agent gender is not a manager's to see — surfacing it here would
            # let the person whose workload the rule blocks work around it.
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
def get_case_detail(case_id: str, current_user: ManagerOnly, db: DbSession):
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

    base = _format_case(case, {case.agent_id: agent_name} if agent_name else None)

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


    from app.services.media_service import MediaService

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
    month_list: list[str] = []
    for i in range(months - 1, -1, -1):
        d = eff_today_perf.replace(day=1) - timedelta(days=30 * i)
        month_list.append(d.strftime("%Y-%m"))
    # dedupe
    seen: set[str] = set()
    month_list = [m for m in month_list if not (m in seen or seen.add(m))]  # type: ignore[func-returns-value]

    my_agents = (
        db.query(Agent, User.full_name)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.manager_user_id == current_user.id)
        .order_by(Agent.ranking_score.desc())
        .all()
    )

    perf_rows = (
        db.query(
            AgentPerformance.agent_id,
            AgentPerformance.month,
            AgentPerformance.total_collected,
            AgentPerformance.total_visits,
            AgentPerformance.ptps_set,
            AgentPerformance.ptps_honored,
            AgentPerformance.collection_rate,
            AgentPerformance.ranking_score,
            AgentPerformance.tier,
        )
        .filter(
            AgentPerformance.agent_id.in_([a.Agent.id for a in my_agents]),
            AgentPerformance.month.in_(month_list),
        )
        .all()
    )

    # Index by (agent_id, month)
    perf_idx: dict[tuple, dict] = {}
    for r in perf_rows:
        perf_idx[(r.agent_id, r.month)] = {
            "collected": float(r.total_collected or 0),
            "visits": r.total_visits or 0,
            "ptps_set": r.ptps_set or 0,
            "ptps_honored": r.ptps_honored or 0,
            "collection_rate": float(r.collection_rate or 0),
            "ranking_score": float(r.ranking_score or 0),
            "tier": r.tier,
        }

    # Total target per agent (all-time from cases)
    target_rows = (
        db.query(Case.agent_id, func.sum(Case.target_amount), func.sum(Case.collected_amount))
        .filter(Case.agent_id.in_([a.Agent.id for a in my_agents]))
        .group_by(Case.agent_id)
        .all()
    )
    agent_totals = {r[0]: {"target": float(r[1] or 0), "collected": float(r[2] or 0)} for r in target_rows}

    result = []
    for agent, full_name in my_agents:
        monthly = []
        for m in month_list:
            p = perf_idx.get((agent.id, m), {})
            rate_val = p.get("collection_rate", 0)
            collected_val = p.get("collected", 0.0)
            target_val = round(collected_val / rate_val) if rate_val > 0 else 0
            monthly.append({
                "month": m,
                "collected": collected_val,
                "target": target_val,
                "visits": p.get("visits", 0),
                "ptps_set": p.get("ptps_set", 0),
                "ptps_honored": p.get("ptps_honored", 0),
                "collection_rate_pct": round(rate_val * 100, 1),
            })

        totals = agent_totals.get(agent.id, {"target": 0.0, "collected": 0.0})
        result.append({
            "agent_id": agent.id,
            "agent_name": full_name,
            "employee_code": agent.employee_code,
            "tier": agent.tier,
            "territory": agent.territory,
            "ranking_score": round(agent.ranking_score, 1),
            "status": agent.status,
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
    visit_id: str
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
# GET /manager/compliance
# ---------------------------------------------------------------------------

@router.get("/compliance")
def compliance_metrics(current_user: ManagerOnly, db: DbSession):
    start_of_month = date.today().replace(day=1)
    start_of_month_dt = datetime.combine(start_of_month, datetime.min.time()).replace(tzinfo=timezone.utc)

    total_visits = (
        db.query(func.count(Visit.id)).filter(Visit.check_in_time >= start_of_month_dt).scalar() or 0
    )
    out_of_hours = (
        db.query(func.count(Visit.id))
        .filter(Visit.check_in_time >= start_of_month_dt, Visit.within_contact_hours == False)  # noqa: E712
        .scalar() or 0
    )
    geo_violations = (
        db.query(func.count(Visit.id))
        .filter(Visit.check_in_time >= start_of_month_dt, Visit.geo_verified == False)  # noqa: E712
        .scalar() or 0
    )
    sos_active = (
        db.query(func.count(Agent.id)).filter(Agent.sos_active == True).scalar() or 0  # noqa: E712
    )

    return {
        "month": date.today().strftime("%Y-%m"),
        "total_visits": total_visits,
        "out_of_hours_visits": out_of_hours,
        "geo_violations": geo_violations,
        "sos_active_count": sos_active,
        "compliance_rate": round(1 - (out_of_hours / total_visits), 4) if total_visits > 0 else 1.0,
        "geo_verification_rate": round(1 - (geo_violations / total_visits), 4) if total_visits > 0 else 1.0,
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

    # 6-month window
    months: list[str] = []
    for i in range(5, -1, -1):
        m = (today.replace(day=1) - timedelta(days=30 * i))
        months.append(m.strftime("%Y-%m"))
    # deduplicate preserving order
    seen: set[str] = set()
    months_ordered: list[str] = []
    for m in months:
        if m not in seen:
            seen.add(m)
            months_ordered.append(m)
    months_ordered.sort()

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
        monthly_trend.append({
            "month": m,
            "collected_lakhs": round(collected / 100000, 2),
            "target_lakhs": round(target / 100000, 2),
            "total_visits": int(visits),
            # Team rate is collected/target over the whole team, not the mean of
            # per-agent rates: an agent who visited one small case must not swing
            # the team line as hard as one who worked forty.
            "collection_rate_pct": round(collected / target * 100, 1) if target > 0 else 0.0,
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
              "ptps_honored": 0, "rate_pct": 0.0})
        a = agent_meta.get(aid)
        leaderboard_all.append({
            "agent_id": aid,
            "agent_name": lb_name_map.get(aid, "Unknown"),
            "total_collected": round(m["collected"], 2),
            "total_visits": int(m["visits"]),
            "ptps_set": int(m["ptps_set"]),
            "ptps_honored": int(m["ptps_honored"]),
            "collection_rate_pct": m["rate_pct"],
            "ranking_score": round(float(a.ranking_score or 0), 1) if a else 0.0,
            "tier": a.tier if a else "TIER_3",
        })
    # Collected first, rate as the tie-break: two agents on the same rupees are
    # not equal if one needed twice the target to get there.
    leaderboard_all.sort(key=lambda r: (-r["total_collected"], -r["collection_rate_pct"]))
    leaderboard = leaderboard_all[:10]

    # Off-duty summary derived from Agent.status (no new table)
    off_duty_count = (
        db.query(func.count(Agent.id))
        .filter(Agent.id.in_(my_agent_ids), Agent.status == AgentStatus.OFF_DUTY)
        .scalar() or 0
    )
    on_duty_count = len(my_agent_ids) - off_duty_count
    leave_summary: dict = {"ON_DUTY": on_duty_count, "OFF_DUTY": off_duty_count}
    total_leave_days = off_duty_count  # proxy: agents currently off

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
    total_ptps_set = (
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids))
        .scalar() or 0
    ) if my_agent_ids else 0
    total_ptps_honored = (
        db.query(func.count(PTP.id))
        .filter(PTP.agent_id.in_(my_agent_ids), PTP.status == PTPStatus.HONORED)
        .scalar() or 0
    ) if my_agent_ids else 0

    # Current month visits per agent (avg) — from the live month already
    # computed above, so it cannot drift from the trend chart's last point.
    cm_visits = sum(
        (team_metrics.get(a, {}).get(current_month, {}) or {}).get("visits", 0)
        for a in my_agent_ids
    )
    n_agents = len(my_agent_ids) or 1

    return {
        "monthly_trend": monthly_trend,
        "dpd_breakdown": dpd_breakdown,
        "leaderboard": leaderboard,
        "leave_summary": {
            "total_leave_days_30d": total_leave_days,
            "by_type": leave_summary,
        },
        "kpis": {
            "overall_collection_rate_pct": round(float(overall_collected) / float(overall_target) * 100, 1) if overall_target else 0.0,
            "total_collected_lakhs": round(float(overall_collected) / 100000, 2),
            "total_target_lakhs": round(float(overall_target) / 100000, 2),
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

@router.get("/analytics/dpd-breakdown")
def get_team_dpd_breakdown(
    current_user: ManagerOnly,
    db: DbSession,
    month: Optional[str] = None,  # YYYY-MM — if provided, sums payments in that month
):
    """DPD collection breakdown across all team cases.
    Without month: all-time portfolio totals (Case.collected_amount).
    With month: only payments collected in that calendar month."""
    bucket_order = ["BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]

    my_agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.manager_user_id == current_user.id).all()]

    if month:
        yr, mo = month.split("-")
        month_start = datetime(int(yr), int(mo), 1)
        month_end = datetime(int(yr) + 1, 1, 1) if int(mo) == 12 else datetime(int(yr), int(mo) + 1, 1)

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
                Case.agent_id.in_(my_agent_ids),
                Payment.agent_id.in_(my_agent_ids),
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
            .filter(Case.agent_id.in_(my_agent_ids))
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
# GET /manager/visits/{visit_id}/media-urls
# Returns short-lived pre-signed URLs for photos and recordings on a visit.
# ---------------------------------------------------------------------------

@router.get("/visits/{visit_id}/media-urls")
def get_visit_media_urls(visit_id: str, current_user: ManagerOnly, db: DbSession):
    visit = db.query(Visit).filter(Visit.id == visit_id).first()
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
    agents_on_duty = (
        db.query(func.count(Agent.id))
        .filter(Agent.id.in_(my_agent_ids), Agent.status == AgentStatus.ON_DUTY)
        .scalar() or 0
    )
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
    on_duty_rows = (
        db.query(Agent.id, User.full_name)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.id.in_(my_agent_ids), Agent.status == AgentStatus.ON_DUTY)
        .all()
    )
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
    agent_id: str,
    current_user: ManagerOnly,
    db: DbSession,
    date: str | None = None,
    sos_only: bool = False,
):
    from app.services.location_service import LocationService

    # Ownership is checked before any location is read. An agent's movement
    # history is the most sensitive data this API serves — see the unscoped
    # PUT /agents/{agent_id}/status for the pattern this deliberately avoids.
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
def agent_ai_insight(agent_id: str, current_user: ManagerOnly, db: DbSession):
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
                "ptps_set": 0, "ptps_honored": 0, "rate_pct": 0.0}

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
def reallocation_plan(agent_id: str, current_user: ManagerOnly, db: DbSession):
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
    agent_id: str,
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
        .filter(Agent.id == agent_id)
        .options(joinedload(Agent.user))
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

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
    agent_id: str,
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
        sms_body = (
            f"Your manager {current_user.full_name} has acknowledged your SOS and is "
            f"responding. Stay safe. - ABC Bank"
        )
        wa_body = (
            f"\U0001f6a8 *SOS Acknowledged*\n\n"
            f"Your manager *{current_user.full_name}* has seen your SOS alert and is responding.\n"
            f"Stay where you are if it's safe to do so."
        )
        NotificationService.send_twilio(e164, sms_body, wa_body)

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
    agent_id: str,
    current_user: ManagerOnly,
    db: DbSession,
):
    """Manager view of any agent's 6-month duty calendar derived from beats."""
    from collections import defaultdict

    agent = (
        db.query(Agent)
        .options(joinedload(Agent.user))
        .filter(Agent.id == agent_id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    today = date.today()
    six_months_ago = today - timedelta(days=180)

    beats = (
        db.query(Beat.beat_date, Beat.status, Beat.total_cases)
        .filter(Beat.agent_id == agent_id, Beat.beat_date >= six_months_ago)
        .all()
    )
    beat_map = {
        b.beat_date: {
            "beat_status": b.status.value if hasattr(b.status, "value") else str(b.status),
            "cases": b.total_cases or 0,
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
                "status": "ON_DUTY" if info else "OFF_DUTY",
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
    agent_id: str,
    current_user: ManagerOnly,
    db: DbSession,
    month: Optional[str] = None,  # YYYY-MM — if provided, sums payments made in that month
):
    """DPD collection breakdown for a specific agent.
    Without month: all-time portfolio totals.
    With month: only payments collected in that calendar month per DPD bucket."""
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
    agent_id: Optional[str] = None,
):
    """Generate a 60-90 word eagle-view AI performance brief with DPD breakdown,
    agent spread, and month-over-month trend for the agency head."""
    import openai as _openai
    from datetime import date as _date2

    # Compute previous month
    yr, mo = month.split("-")
    prev_dt = _date2(int(yr), int(mo), 1) - timedelta(days=1)
    prev_month = prev_dt.strftime("%Y-%m")

    # All agent IDs
    my_agent_ids = [r[0] for r in db.query(Agent.id).all()]

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

    if agent_id:
        # ── Per-agent report ──────────────────────────────────────────────
        agent_obj = (
            db.query(Agent).options(joinedload(Agent.user)).filter(Agent.id == agent_id).first()
        )
        agent_name = agent_obj.user.full_name if agent_obj and agent_obj.user else "Agent"
        tier = getattr(agent_obj, "tier", "?") if agent_obj else "?"
        territory = getattr(agent_obj, "territory", "?") if agent_obj else "?"
        scope = agent_name

        row = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id == agent_id, AgentPerformance.month == month)
            .first()
        )
        prev_row = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id == agent_id, AgentPerformance.month == prev_month)
            .first()
        )

        # Team averages for the month
        team_rows = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == month)
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
            .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == month)
            .options(joinedload(AgentPerformance.agent).joinedload(Agent.user))
            .all()
        )
        prev_rows = (
            db.query(AgentPerformance)
            .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == prev_month)
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
