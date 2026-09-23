"""Command Centre field-ops contract adapter.

Command Centre (the bank-facing collections platform) renders its Field
Operations page from four endpoints it proxies server-side through
`/api/field-ops/*` — see FIELD_OPS_INTEGRATION.md in the Command Centre repo.
Those four are a *fixed external contract*: key names, casing, enum strings,
ISO-8601 UTC timestamps and plain-rupee amounts all have to match exactly,
because Command Centre's proxy passes our JSON straight through to its UI.

This module is the translation layer between that contract and TIQCollect's own
domain model. It deliberately does not reuse the `/api/v1/manager/*` response
shapes: those stay free to evolve alongside TIQCollect's own frontend, while
these four are pinned by an external consumer. Nothing else in the app should
import from here.

Mounted directly on the app rather than under the `/api/v1` router, because the
contract fixes the paths at `/api/field-ops/*`.

Scope is agency-wide. The manager endpoints filter to `manager_user_id` because
a manager sees their own team; Command Centre is the bank looking at the whole
agency, so nothing here filters by manager.

Three mappings are lossy or derived rather than direct, and are documented at
their definitions below:
  * agent *duty* status (ON_DUTY/OFF_DUTY/...) is a different axis from the
    contract's *movement* status (In Transit/At Location/...), so the latter is
    derived from live visit + geo-verification state, not mapped;
  * TIQCollect's 11 visit outcomes collapse into the contract's 6;
  * `zone` is the city an agent's territory sits in, not the territory itself.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import case as sa_case, func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.dependencies import DbSession
from app.models.agent import Agent, AgentStatus
from app.models.beat import Beat
from app.models.case import Case, CaseStatus
from app.models.loan import Loan
from app.models.payment import Payment, PaymentStatus
from app.models.visit import Visit, VisitOutcome
from app.services.leave_service import agent_ids_on_leave, effective_status


def _verify_command_centre(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Optional shared-secret gate on the whole contract surface.

    Command Centre's proxy sends no auth header by default, so this stays off
    unless FIELD_OPS_REQUIRE_API_KEY is set. That keeps a local Command Centre
    working out of the box while making the production posture a .env change
    rather than a code change — these endpoints expose live agent GPS and
    collections figures, so they should not be open on a public deployment.
    """
    if not settings.FIELD_OPS_REQUIRE_API_KEY:
        return
    if not x_api_key or x_api_key != settings.COMMAND_CENTRE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )


router = APIRouter(
    prefix="/api/field-ops",
    tags=["command-centre-contract"],
    dependencies=[Depends(_verify_command_centre)],
)


# ─── Contract enum translation ───────────────────────────────────────────────

#: TIQCollect records 11 visit outcomes; the contract allows 6. Every collapse
#: below loses information, and three lose it in ways worth naming:
#:   PART_PAID_PTP  → money did change hands, so "Payment Collected" is the
#:                    truthful half; the PTP for the remainder is unrepresentable.
#:   BROKEN_PTP     → a customer who promised and did not pay. "Refused" is the
#:                    least-wrong of six bad fits, not a real equivalent.
#:   DECEASED       → no contract value comes close; "Not Home" at least keeps
#:   REVISIT          it out of the payment/refusal buckets, same for an
#:                    incomplete visit needing a revisit.
#: Consumers needing the real outcome should read /api/v1, not this adapter.
_OUTCOME_TO_CONTRACT: dict[VisitOutcome, str] = {
    VisitOutcome.PAID_FULL:     "Payment Collected",
    VisitOutcome.PART_PAID:     "Payment Collected",
    VisitOutcome.PART_PAID_PTP: "Payment Collected",
    VisitOutcome.PTP:           "PTP Obtained",
    VisitOutcome.RTP:           "Refused",
    VisitOutcome.BROKEN_PTP:    "Refused",
    VisitOutcome.DISPUTE:       "Disputed",
    VisitOutcome.NOT_AVAILABLE: "Not Home",
    VisitOutcome.ADDRESS_ISSUE: "Address Not Found",
    VisitOutcome.DECEASED:      "Not Home",
    VisitOutcome.REVISIT:       "Not Home",
}

#: Payments that never became real money — excluded from every rupee figure here.
_DEAD_PAYMENT_STATES = (PaymentStatus.REJECTED, PaymentStatus.REVERSED)

#: Case states that still represent an account someone is expected to work.
_OPEN_CASE_STATES = (
    CaseStatus.ASSIGNED,
    CaseStatus.IN_PROGRESS,
    CaseStatus.PTP_SET,
    CaseStatus.PARTIALLY_PAID,
    CaseStatus.ESCALATED,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _zone_of(territory: str | None) -> str:
    """The contract's `zone` for an agent's territory.

    `Agent.territory` is a specific patch — "Sector 44, Gurugram", "Saket, New
    Delhi" — and is effectively unique per agent, so grouping coverage by it
    would put one agent in every zone and turn Command Centre's coverage chart
    into one bar per agent. The trailing city is the coarser unit the chart
    actually wants, and several agents share one. Territories without a city
    suffix pass through whole rather than being dropped.
    """
    if not territory:
        return "Unassigned"
    return territory.rsplit(",", 1)[-1].strip() or territory.strip()


def _iso_z(dt: datetime | None) -> str | None:
    """Render as `2026-07-28T09:20:00Z`, the exact form the contract specifies.

    Naive datetimes are treated as UTC — the engine sets `timezone='UTC'` per
    connection and every timestamp column here is timezone-aware, so this only
    guards against a driver handing back a naive value.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _effective_window(db: Session) -> tuple[datetime, datetime, date]:
    """`(start, end, date)` of the most recent day that has beat data.

    Mirrors `manager.py::_effective_today`, but agency-wide rather than scoped
    to one manager's agents. Anchoring to the latest beat date instead of the
    wall clock is what keeps this populated on a seeded or demo database, where
    the literal today usually has no rows at all.
    """
    latest: date | None = (
        db.query(func.max(Beat.beat_date))
        .filter(Beat.beat_date <= date.today())
        .scalar()
    )
    eff_date = latest or date.today()
    start = datetime.combine(eff_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(eff_date, datetime.max.time()).replace(tzinfo=timezone.utc)
    return start, end, eff_date


def _activity_label(dt: datetime | None, reference: datetime) -> str:
    """Human phrasing for `last_activity_at`, as the contract's examples show."""
    if dt is None:
        return "No activity"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    minutes = max(int((reference - dt).total_seconds() // 60), 0)
    if minutes < 5:
        return "Just now"
    if minutes < 60:
        return f"{minutes} mins ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    return "1 day ago" if days == 1 else f"{days} days ago"


def _build_agent_rows(db: Session) -> tuple[list[dict], dict]:
    """Contract-shaped agent rows, plus the day totals `/summary` needs.

    `/summary` and `/agents` are both built from this so the headline tiles can
    never disagree with the table underneath them. Every per-agent figure comes
    from a grouped query rather than a per-agent one — this endpoint backs live
    dashboard tiles and the Command Centre proxy gives up after 10 seconds.
    """
    start, end, eff_date = _effective_window(db)

    agents = (
        db.query(Agent)
        .options(joinedload(Agent.user))
        .order_by(Agent.employee_code)
        .all()
    )

    # One pass over today's visits: how many, how many broke the geo-fence, how
    # many are still open (checked in, not yet checked out), and the latest one.
    visit_rows = (
        db.query(
            Visit.agent_id,
            func.count(Visit.id),
            func.count(func.distinct(Visit.case_id)),
            func.sum(sa_case((Visit.geo_verified.is_(False), 1), else_=0)),
            func.sum(sa_case((Visit.check_out_time.is_(None), 1), else_=0)),
            func.max(Visit.check_in_time),
        )
        .filter(Visit.check_in_time >= start, Visit.check_in_time <= end)
        .group_by(Visit.agent_id)
        .all()
    )
    visits_by_agent = {
        row[0]: {
            "count": row[1] or 0,
            "cases": row[2] or 0,
            "deviations": int(row[3] or 0),
            "open": int(row[4] or 0),
            "last_at": row[5],
        }
        for row in visit_rows
    }

    collected_by_agent = dict(
        db.query(Payment.agent_id, func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(
            Payment.payment_date >= start,
            Payment.payment_date <= end,
            Payment.status.notin_(_DEAD_PAYMENT_STATES),
        )
        .group_by(Payment.agent_id)
        .all()
    )

    # Labels read relative to the day the rest of this payload describes. On a
    # live system that is the wall clock; on a seeded one it is the seeded day,
    # so "10 mins ago" stays coherent with the visit counts beside it. The
    # `last_activity_at` timestamp itself is always the real, unshifted value.
    now = datetime.now(timezone.utc)
    reference = now if eff_date == now.date() else end

    rows: list[dict] = []
    totals = {"visits": 0, "targets": 0, "deviations": 0, "collected": 0.0}
    # Approved leave for the day counts as Absent even before the nightly
    # status sync has run (services/leave_service.py, 2026-09-22).
    on_leave_today = agent_ids_on_leave(db, eff_date, [a.id for a in agents])

    for agent in agents:
        v = visits_by_agent.get(agent.id)
        # Distinct accounts reached, not visit records written: the target below
        # is a per-day *case* allowance, so counting raw visits would let a
        # single account revisited twice read as two visits against it.
        visits_completed = v["cases"] if v else 0
        deviations = v["deviations"] if v else 0
        has_open_visit = bool(v and v["open"])
        last_at = v["last_at"] if v else None

        # Standing daily capacity, not the day's planned beat. Beats are the
        # more precise notion of "expected today", but they only exist for
        # agents the route optimizer has run for — mixing the two would measure
        # some agents against a route and the rest against capacity, in the
        # same column. One consistent basis is worth more than a sharper one
        # applied unevenly.
        visits_target = agent.max_cases_per_day
        collected = float(collected_by_agent.get(agent.id) or 0.0)

        # Duty status answers "is this agent working today"; the contract's
        # status answers "where are they right now". Only the first is stored,
        # so the second is derived. A geo-fence breach outranks being on site:
        # it is the exception the dashboard exists to surface.
        if effective_status(agent, on_leave_today) is not AgentStatus.ON_DUTY:
            contract_status = "Absent"
        elif deviations:
            contract_status = "Deviation"
        elif has_open_visit:
            contract_status = "At Location"
        else:
            contract_status = "In Transit"

        has_location = (
            agent.last_known_latitude is not None
            and agent.last_known_longitude is not None
        )

        rows.append({
            "agent_id": agent.employee_code,
            "name": agent.user.full_name,
            "status": contract_status,
            "zone": _zone_of(agent.territory),
            "last_activity_at": _iso_z(last_at),
            "last_activity_label": _activity_label(last_at, reference),
            "visits_completed": visits_completed,
            "visits_target": visits_target,
            "ptp_collected_today": round(collected, 2),
            "current_location": (
                {"lat": agent.last_known_latitude, "lng": agent.last_known_longitude}
                if has_location and contract_status != "Absent"
                else None
            ),
        })

        totals["visits"] += visits_completed
        totals["targets"] += visits_target
        totals["deviations"] += deviations
        totals["collected"] += collected

    return rows, totals


# ─── Contract endpoints ──────────────────────────────────────────────────────

@router.get("/summary")
def summary(db: DbSession) -> dict:
    """Headline KPIs for Command Centre's Field Operations tiles."""
    rows, totals = _build_agent_rows(db)

    return {
        "active_agents": sum(1 for r in rows if r["status"] != "Absent"),
        "total_agents": len(rows),
        # Counted per breaching *visit*, not per agent — an agent who broke the
        # fence three times today is three deviations. This is the literal
        # reading of the field name, and differs from the reference stub, which
        # counts agents currently in a Deviation state.
        "geofence_deviations": totals["deviations"],
        "visit_target_pct": (
            round(totals["visits"] / totals["targets"] * 100, 1)
            if totals["targets"] else 0.0
        ),
        # Money actually collected in the field today. TIQCollect tracks PTPs
        # (promises) separately from payments (cash); the contract has one field
        # and Command Centre labels it as a collected amount, so payments are
        # the honest source. Rejected and reversed payments are excluded.
        "ptp_collected_today": round(totals["collected"], 2),
    }


@router.get("/agents")
def agents(db: DbSession) -> list[dict]:
    """Live roster of field agents."""
    rows, _ = _build_agent_rows(db)
    return rows


@router.get("/coverage")
def coverage(db: DbSession) -> list[dict]:
    """Visit coverage by zone, for Command Centre's coverage bar chart.

    Coverage here is "how much of the open book has been reached at least
    once", so both sides of the ratio count the same population of accounts —
    an agent's open caseload — rather than setting an all-time assigned figure
    against a single day's visits. `Case.visit_count` is maintained as visits
    are recorded, so the visited side needs no join back to `visits`.
    """
    zone_rows = (
        db.query(
            Agent.territory,
            func.count(Case.id),
            func.sum(sa_case((Case.visit_count > 0, 1), else_=0)),
        )
        .join(Case, Case.agent_id == Agent.id)
        .filter(Case.status.in_(_OPEN_CASE_STATES))
        .group_by(Agent.territory)
        .all()
    )

    # Territories collapse into cities, so several rows can share one zone.
    by_zone: dict[str, dict] = {}
    for territory, assigned, visited in zone_rows:
        zone_name = _zone_of(territory)
        zone = by_zone.setdefault(
            zone_name, {"zone": zone_name, "assigned": 0, "visited": 0}
        )
        zone["assigned"] += int(assigned or 0)
        zone["visited"] += int(visited or 0)

    return [
        {
            "zone": z["zone"],
            "pct_covered": (
                round(z["visited"] / z["assigned"] * 100, 1) if z["assigned"] else 0.0
            ),
            "accounts_assigned": z["assigned"],
            "accounts_visited": z["visited"],
        }
        for z in sorted(by_zone.values(), key=lambda z: z["zone"])
    ]


@router.get("/visits")
def visits(db: DbSession, account_id: str | None = None) -> list[dict]:
    """Visit history, by Command Centre account id.

    `account_id` is Command Centre's loan identifier, which maps to
    `Loan.loan_account_number` here. An unknown account returns `[]` rather than
    a 404, as the contract requires.

    Note this only resolves for loans whose account number is shared with
    Command Centre — TIQCollect's seeded demo loans use their own numbering, so
    on a seeded database this correctly returns `[]` for Command Centre's ids.
    Loans arriving through `scripts/ingest_daily.py` carry the bank's real
    account numbers and join as intended.
    """
    query = (
        db.query(Visit, Loan.loan_account_number, Agent.employee_code)
        .join(Case, Visit.case_id == Case.id)
        .join(Loan, Case.loan_id == Loan.id)
        .join(Agent, Visit.agent_id == Agent.id)
    )
    if account_id:
        query = query.filter(Loan.loan_account_number == account_id)

    results = query.order_by(Visit.check_in_time.desc()).limit(50).all()
    if not results:
        return []

    # Amounts come from the payment booked against each visit, not from the
    # case total, so a visit that collected nothing reports 0 rather than
    # inheriting an earlier visit's collection.
    visit_ids = [visit.id for visit, _, _ in results]
    collected_by_visit = dict(
        db.query(Payment.visit_id, func.coalesce(func.sum(Payment.amount), 0.0))
        .filter(
            Payment.visit_id.in_(visit_ids),
            Payment.status.notin_(_DEAD_PAYMENT_STATES),
        )
        .group_by(Payment.visit_id)
        .all()
    )

    return [
        {
            "visit_id": visit.id,
            "account_id": loan_account_number,
            "agent_id": employee_code,
            "visited_at": _iso_z(visit.check_in_time),
            "outcome": _OUTCOME_TO_CONTRACT.get(visit.outcome, "Not Home"),
            "amount_collected": round(float(collected_by_visit.get(visit.id) or 0.0), 2),
            # The agent's own words first; the generated summary is a fallback
            # so the field is rarely empty for Command Centre's drawers.
            "notes": visit.notes or visit.ai_visit_note or "",
        }
        for visit, loan_account_number, employee_code in results
    ]
