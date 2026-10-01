"""Beats for the generated agencies (lane L6, 2026-10-01): one
`planning.beats` row per (agent, day) that has real visits or an approved
leave day, so Field Operations' two remaining metrics (planned-vs-actual
km, route adherence — mv_field_activity_daily's planned_stops / planned_km /
actual_km, filled from `planning.beats` by that view's own `bt` CTE) have
real data for all 9 generated agencies, not just Aravalli.

THE ONE DESIGN CHOICE THAT NEEDS SAYING OUT LOUD, owner-approved via
tiqcollect-f8: there was never an independent plan to recover — the
generator builds each day's outcome directly, no solver ever ran. So
"planned" here is a nearest-neighbour ordering of the SAME cases the agent
actually visited that day, computed from each customer's registered address,
starting from the agent's base location (workforce.agents.base_latitude/
longitude) — a plan, not a fact, exactly the way the real nightly solver's
output is also a plan, just a fancier one. "Actual" is the real, chronological
check-in sequence — real GPS, real timestamps, nothing invented.

BECAUSE OF THAT, THE PLANNED SET == THE VISITED SET, ALWAYS. This metric can
show route-order efficiency (did the agent visit in a geographically sensible
sequence) — it can NEVER show plan completion ("planned 10, visited 8"),
because there were never planned-but-unvisited cases. Any copy or KPI label
reading this data must say "route efficiency against an optimal ordering of
the day's cases," never "planned beats completed" or "adherence to a
morning plan" — the honest answer to "planned by whom, when" is "a post-hoc
efficient ordering of what happened," not a committed plan.

Distance, both planned and actual: Haversine x 1.15 (planner_service.py's
own documented road-distance correction, reused as a constant — not a live
OSRM call; route_source is stamped "haversine", honestly, not "osrm").
Duration: estimated from the same km/25 + avg_visit_seconds()-per-stop
formula the Beat model's own comment documents; actual duration is the real
day's span, first check-in to last check-out (or check-in + the one
definition of a visit's length, when a visit was never checked out).

SCOPE LIMIT, stated rather than silently narrowed: a beat is created only
for a day with a real visit or an approved leave record — not "every working
day across 6 months" the way the live product's comment describes. A
zero-visit, non-leave day (no case was due) carries no beat, same as it
would carry no field_activity_daily row either; adding one would be a
0-everything record with no information in it.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.core.geo import IST
from app.core.routing import avg_visit_seconds
from app.demo import roster as R
from app.demo.world import T, insert

ROAD_FACTOR = 1.15   # planner_service.py: "Haversine x 1.15", the documented road-distance correction
KM_PER_MINUTE = 25 / 60


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi, dlambda = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def _nearest_neighbour_km(start: tuple[float, float], stops: list[tuple[float, float]]) -> float:
    """Total straight-line distance of a greedy nearest-neighbour tour from
    `start` through every point in `stops`, each visited once. Tiny per-day
    stop counts (single digits), so O(n^2) is not a performance concern."""
    remaining = list(stops)
    here, total = start, 0.0
    while remaining:
        nxt = min(remaining, key=lambda p: _haversine_km(here, p))
        total += _haversine_km(here, nxt)
        remaining.remove(nxt)
        here = nxt
    return total


@dataclass
class BeatsResult:
    agent_days: int
    leave_days: int


def generate_beats(conn: Connection, *, bank_key: str = "GIRIVAN") -> BeatsResult:
    bank_id = R.BANK["id"] if bank_key == "GIRIVAN" else R.KUMAON_BANK["id"]
    generated_agency_ids = {a.id for a in R.AGENCIES if a.key != "ARAVALLI" and a.bank_key == bank_key}
    if not generated_agency_ids:
        return BeatsResult(0, 0)

    already = conn.execute(sa.select(sa.func.count()).select_from(T["beats"])
                           .where(T["beats"].c.agency_id.in_(generated_agency_ids))).scalar_one()
    if already:
        raise RuntimeError(f"beats already exist for {bank_key}'s generated agencies; refusing to build twice")

    v, c, cu, l, a = (T["visits"], T["cases"], T["customers"], T["loans"], T["agents"])
    rows = conn.execute(
        sa.select(v.c.agent_id, v.c.agency_id, v.c.check_in_time, v.c.check_out_time,
                 v.c.check_in_latitude, v.c.check_in_longitude, l.c.emi_amount,
                 cu.c.latitude.label("cust_lat"), cu.c.longitude.label("cust_lon"),
                 a.c.base_latitude, a.c.base_longitude, a.c.employee_code)
        .select_from(v.join(c, c.c.id == v.c.case_id).join(l, l.c.id == c.c.loan_id)
                    .join(cu, cu.c.id == c.c.customer_id).join(a, a.c.id == v.c.agent_id))
        .where(v.c.agency_id.in_(generated_agency_ids))
    ).fetchall()

    by_agent_day: dict[tuple[str, date], list] = defaultdict(list)
    base_of: dict[str, tuple[float, float]] = {}
    employee_code_of: dict[str, str] = {}
    agency_of: dict[str, str] = {}
    for r in rows:
        d = r.check_in_time.astimezone(IST).date()
        by_agent_day[(r.agent_id, d)].append(r)
        base_of[r.agent_id] = (r.base_latitude, r.base_longitude)
        employee_code_of[r.agent_id] = r.employee_code
        agency_of[r.agent_id] = r.agency_id

    beats = []
    for (agent_id, d), day_rows in by_agent_day.items():
        day_rows.sort(key=lambda r: r.check_in_time)
        actual_pts = [(r.check_in_latitude, r.check_in_longitude) for r in day_rows]
        start = base_of[agent_id]
        actual_km = (_haversine_km(start, actual_pts[0])
                    + sum(_haversine_km(actual_pts[i], actual_pts[i + 1]) for i in range(len(actual_pts) - 1))
                    ) * ROAD_FACTOR
        last_end = day_rows[-1].check_out_time or (day_rows[-1].check_in_time
                                                    + timedelta(seconds=avg_visit_seconds()))
        actual_minutes = int((last_end - day_rows[0].check_in_time).total_seconds() / 60)

        cust_pts = [(r.cust_lat, r.cust_lon) for r in day_rows]
        planned_km = _nearest_neighbour_km(start, cust_pts) * ROAD_FACTOR
        planned_minutes = int(planned_km / KM_PER_MINUTE + avg_visit_seconds() / 60 * len(day_rows))

        beats.append({
            "id": R.new_id("beat", f"{agent_id}:{d.isoformat()}"),
            "bank_id": bank_id, "agency_id": agency_of[agent_id], "agent_id": agent_id,
            "beat_date": d, "beat_number": f"BEAT-{d:%Y%m%d}-{employee_code_of[agent_id]}",
            "ordered_case_ids": [], "total_cases": len(day_rows),
            "estimated_distance_km": round(planned_km, 2), "estimated_duration_minutes": planned_minutes,
            "total_target_amount": round(sum(float(r.emi_amount or 0) for r in day_rows), 2),
            "status": "COMPLETED", "cases_completed": len(day_rows), "amount_collected": 0.0,
            "route_source": "haversine", "actual_distance_km": round(actual_km, 2),
            "actual_duration_minutes": actual_minutes, "is_ml_generated": True, "is_leave_day": False,
        })

    lr = T["leave_requests"]
    leave_rows = conn.execute(
        sa.select(lr.c.agent_id, lr.c.from_date, lr.c.to_date, lr.c.leave_type, a.c.employee_code,
                 a.c.agency_id)
        .select_from(lr.join(a, a.c.id == lr.c.agent_id))
        .where(a.c.agency_id.in_(generated_agency_ids), lr.c.status == "APPROVED")
    ).fetchall()
    leave_beats = []
    for r in leave_rows:
        d = r.from_date
        while d <= r.to_date:
            if (r.agent_id, d) not in by_agent_day:
                leave_beats.append({
                    "id": R.new_id("beat", f"{r.agent_id}:{d.isoformat()}"),
                    "bank_id": bank_id, "agency_id": r.agency_id, "agent_id": r.agent_id,
                    "beat_date": d, "beat_number": f"BEAT-{d:%Y%m%d}-{r.employee_code}",
                    "ordered_case_ids": [], "total_cases": 0, "estimated_distance_km": 0.0,
                    "estimated_duration_minutes": 0, "total_target_amount": 0.0,
                    "status": "CANCELLED", "cases_completed": 0, "amount_collected": 0.0,
                    # Explicit 0, not left NULL: on leave, the agent travelled
                    # nowhere — that is a known fact, not an unmeasured one.
                    "route_source": "haversine", "actual_distance_km": 0.0, "actual_duration_minutes": 0,
                    "is_ml_generated": True, "is_leave_day": True, "leave_type": r.leave_type,
                })
            d += timedelta(days=1)

    insert(conn, "beats", beats + leave_beats)
    return BeatsResult(agent_days=len(beats), leave_days=len(leave_beats))
