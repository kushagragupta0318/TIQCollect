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

from datetime import datetime, date, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.core.dependencies import DbSession, ManagerOnly
from app.core.config import settings
from app.models.agent import Agent, AgentStatus, AgentPerformance
from app.core import storage
from app.models.beat import Beat
from app.models.case import Case, CaseStatus
from app.models.loan import DPDBucket, Loan
from app.models.payment import Payment
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
        today_collected = (
            db.query(func.coalesce(func.sum(Payment.amount), 0.0))
            .filter(Payment.agent_id == agent.id, Payment.payment_date >= start_of_day)
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

    # Enrich with real PTP rate + collection rate from AgentPerformance (source of truth)
    current_month_str = eff_date.strftime("%Y-%m")
    perf_snap = (
        db.query(AgentPerformance)
        .filter(AgentPerformance.agent_id.in_(agent_ids), AgentPerformance.month == current_month_str)
        .all()
    )
    perf_by_agent = {p.agent_id: p for p in perf_snap}
    for item in result:
        p = perf_by_agent.get(item["id"])
        ptps_set = p.ptps_set or 0 if p else 0
        ptps_honored = p.ptps_honored or 0 if p else 0
        item["ptp_rate_pct"] = round(ptps_honored / max(ptps_set, 1) * 100, 1) if ptps_set > 0 else 0.0
        item["collection_rate_pct"] = round(float(p.collection_rate or 0) * 100, 1) if p else 0.0
        # Overwrite monthly visits/collections/ptps with the snapshot (more reliable than Agent model counters)
        if p:
            item["current_month_visits"] = p.total_visits or 0
            item["current_month_collections"] = float(p.total_collected or 0)
            item["current_month_ptps_set"] = p.ptps_set or 0
            item["current_month_ptps_honored"] = p.ptps_honored or 0

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

    # Monthly collection trend from AgentPerformance snapshots
    perf_rows = (
        db.query(
            AgentPerformance.month,
            func.sum(AgentPerformance.total_collected).label("collected"),
            func.sum(AgentPerformance.total_visits).label("visits"),
            func.avg(AgentPerformance.collection_rate).label("avg_rate"),
        )
        .filter(
            AgentPerformance.agent_id.in_(my_agent_ids),
            AgentPerformance.month.in_(months_ordered),
        )
        .group_by(AgentPerformance.month)
        .order_by(AgentPerformance.month)
        .all()
    )
    perf_by_month = {r.month: r for r in perf_rows}

    monthly_trend = []
    for m in months_ordered:
        r = perf_by_month.get(m)
        # Estimate target as collected / avg_rate (or collected * 1.4 as fallback)
        collected = float(r.collected or 0) if r else 0.0
        avg_rate = float(r.avg_rate or 0) if r else 0.0
        target = round(collected / avg_rate, 2) if avg_rate > 0 else round(collected * 1.4, 2)
        monthly_trend.append({
            "month": m,
            "collected_lakhs": round(collected / 100000, 2),
            "target_lakhs": round(target / 100000, 2),
            "total_visits": int(r.visits or 0) if r else 0,
            "collection_rate_pct": round(avg_rate * 100, 1) if r else 0.0,
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

    # Agent leaderboard for current month
    current_month = today.strftime("%Y-%m")
    leaderboard_rows = (
        db.query(
            AgentPerformance.agent_id,
            AgentPerformance.total_collected,
            AgentPerformance.total_visits,
            AgentPerformance.ptps_set,
            AgentPerformance.ptps_honored,
            AgentPerformance.collection_rate,
            AgentPerformance.ranking_score,
            AgentPerformance.tier,
        )
        .filter(
            AgentPerformance.agent_id.in_(my_agent_ids),
            AgentPerformance.month == current_month,
        )
        .order_by(AgentPerformance.ranking_score.desc())
        .limit(10)
        .all()
    )
    # Get agent names for leaderboard
    lb_agent_ids = [r.agent_id for r in leaderboard_rows]
    lb_name_map: dict[str, str] = {}
    if lb_agent_ids:
        name_rows = (
            db.query(Agent.id, User.full_name)
            .join(User, Agent.user_id == User.id)
            .filter(Agent.id.in_(lb_agent_ids))
            .all()
        )
        lb_name_map = {r[0]: r[1] for r in name_rows}

    leaderboard = [
        {
            "agent_id": r.agent_id,
            "agent_name": lb_name_map.get(r.agent_id, "Unknown"),
            "total_collected": float(r.total_collected or 0),
            "total_visits": r.total_visits or 0,
            "ptps_set": r.ptps_set or 0,
            "ptps_honored": r.ptps_honored or 0,
            "collection_rate_pct": round(float(r.collection_rate or 0) * 100, 1),
            "ranking_score": round(float(r.ranking_score or 0), 1),
            "tier": r.tier,
        }
        for r in leaderboard_rows
    ]

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
    total_ptps = (
        db.query(func.count(AgentPerformance.id))
        .filter(AgentPerformance.agent_id.in_(my_agent_ids))
        .scalar() or 0
    )
    total_ptps_set = (
        db.query(func.sum(AgentPerformance.ptps_set))
        .filter(AgentPerformance.agent_id.in_(my_agent_ids))
        .scalar() or 0
    )
    total_ptps_honored = (
        db.query(func.sum(AgentPerformance.ptps_honored))
        .filter(AgentPerformance.agent_id.in_(my_agent_ids))
        .scalar() or 0
    )

    # Current month visits per agent (avg)
    cm_visits = (
        db.query(func.sum(AgentPerformance.total_visits))
        .filter(
            AgentPerformance.agent_id.in_(my_agent_ids),
            AgentPerformance.month == current_month,
        )
        .scalar() or 0
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

    try:
        import openai as _oai, os as _os, json as _json
        _client = _oai.OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
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
        _resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0.25,
            max_tokens=500,
            messages=[
                {"role": "system", "content": (
                    "You are a collections agency AI operations analyst. Return JSON with: "
                    "headline (1 sentence, data-specific numbers), "
                    "key_insight (2 sentences identifying the most critical performance pattern with numbers), "
                    "recommended_actions (array of up to 3 objects: {action, impact: HIGH|MEDIUM, urgency: NOW|TODAY}). "
                    "Prioritise decisions that directly increase collection rate and prevent PTP failures. Be specific."
                )},
                {"role": "user", "content": f"Operational data: {_ctx}"},
            ],
        )
        _parsed = _json.loads(_resp.choices[0].message.content)
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

    # Last 3 months of performance for this agent
    months_3: list[str] = []
    for i in range(2, -1, -1):
        m = eff_date.replace(day=1) - timedelta(days=30 * i)
        months_3.append(m.strftime("%Y-%m"))
    seen_m: set[str] = set()
    months_3 = [m for m in months_3 if not (m in seen_m or seen_m.add(m))]  # type: ignore[func-returns-value]

    perf_rows = (
        db.query(AgentPerformance)
        .filter(AgentPerformance.agent_id == agent_id, AgentPerformance.month.in_(months_3))
        .order_by(AgentPerformance.month)
        .all()
    )

    # Team averages for current month
    current_month = eff_date.strftime("%Y-%m")
    team_avg = (
        db.query(
            func.avg(AgentPerformance.collection_rate),
            func.avg(AgentPerformance.total_visits),
            func.avg(AgentPerformance.ptps_honored),
            func.avg(AgentPerformance.ptps_set),
        )
        .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == current_month)
        .first()
    )
    team_collection_rate = round(float(team_avg[0] or 0) * 100, 1) if team_avg else 0.0
    team_avg_visits = round(float(team_avg[1] or 0), 1) if team_avg else 0.0
    team_ptp_rate = round(float(team_avg[2] or 0) / max(float(team_avg[3] or 1), 1) * 100, 1) if team_avg else 0.0

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
        p = next((r for r in perf_rows if r.month == m), None)
        ptp_rate = round(float(p.ptps_honored or 0) / max(float(p.ptps_set or 1), 1) * 100, 1) if p else 0.0
        monthly_data.append({
            "month": m,
            "collection_rate_pct": round(float(p.collection_rate or 0) * 100, 1) if p else 0.0,
            "visits": p.total_visits or 0 if p else 0,
            "ptp_rate_pct": ptp_rate,
            "collected": float(p.total_collected or 0) if p else 0.0,
        })

    if len(monthly_data) >= 2:
        delta = monthly_data[-1]["collection_rate_pct"] - monthly_data[0]["collection_rate_pct"]
        trend = "IMPROVING" if delta > 5 else ("DECLINING" if delta < -5 else "STABLE")
    else:
        trend = "STABLE"

    current_p = next((r for r in perf_rows if r.month == current_month), None)
    agent_rate = round(float(current_p.collection_rate or 0) * 100, 1) if current_p else 0.0
    agent_ptp_rate = round(float(current_p.ptps_honored or 0) / max(float(current_p.ptps_set or 1), 1) * 100, 1) if current_p else 0.0
    agent_visits = current_p.total_visits or 0 if current_p else 0

    # Additional signal: visit-to-collection conversion (collections / visits = per-visit yield)
    current_collected = float(current_p.total_collected or 0) if current_p else 0.0
    per_visit_yield = round(current_collected / max(agent_visits, 1), 0) if agent_visits > 0 else 0.0
    # Team per-visit yield
    team_total_collected = (
        db.query(func.sum(AgentPerformance.total_collected))
        .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == current_month)
        .scalar() or 0.0
    )
    team_total_visits_cm = (
        db.query(func.sum(AgentPerformance.total_visits))
        .filter(AgentPerformance.agent_id.in_(my_agent_ids), AgentPerformance.month == current_month)
        .scalar() or 0
    )
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
        import openai as _oai, os as _os, json as _json
        _client = _oai.OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
        _ctx = {
            "agent_name": agent_name,
            "tier": agent.tier if agent else "TIER_2",
            "territory": agent.territory if agent else "",
            "specialization": agent.specialization if agent else "",
            "monthly_trend_last_3_months": monthly_data,
            "performance_trend_direction": trend,
            "current_month": {
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
        _resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0.25,
            max_tokens=450,
            messages=[
                {"role": "system", "content": (
                    "You are a collections operations analyst. Analyse a field agent's full performance profile. "
                    "Return JSON with exactly these keys: "
                    "performance_signal (IMPROVING/DECLINING/STABLE based on 3-month trend), "
                    "insight_text (3 sentences covering: 1) collection rate & trend WHY, "
                    "2) visit productivity and per-visit yield vs team, "
                    "3) PTP discipline and case portfolio health — use specific numbers), "
                    "recommended_action (1 concrete, specific action the manager should take THIS WEEK "
                    "to improve this agent's output — e.g. coaching, case reallocation, territory change, "
                    "shadowing a top performer, reducing NPA case load). "
                    "Be analytically precise, not generic."
                )},
                {"role": "user", "content": f"Agent data: {_ctx}"},
            ],
        )
        _parsed = _json.loads(_resp.choices[0].message.content)
        performance_signal = _parsed.get("performance_signal", trend)
        insight_text = _parsed.get("insight_text", insight_text)
        recommended_action = _parsed.get("recommended_action", recommended_action)
    except Exception:
        pass

    return {
        "agent_id": agent_id,
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
        best_id, best_score, best_reasons = None, -1, []

        for ag in other_agents:
            if remaining_capacity.get(ag.id, 0) <= 0:
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

    _oai_key = settings.OPENAI_API_KEY
    report_text = scope_stats  # rich fallback if no key
    if _oai_key:
        try:
            client = _openai.OpenAI(api_key=_oai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            report_text = (resp.choices[0].message.content or "").strip()
        except Exception:
            pass

    return {"month": month, "scope": scope, "report_text": report_text}
