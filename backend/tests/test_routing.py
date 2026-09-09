"""Tests for core/routing.py — the module had NONE until 2026-09-08.

That absence is why a defect as large as "the planner throws away the OSRM
matrix it just paid for and recomputes the distance as crow-flies" survived for
months: nothing asserted that the numbers a route reports are the numbers the
solver used.

NO NETWORK. Every test points _OSRM_BASE at a closed local port, so the OSRM
path fails immediately and the Haversine fallback runs. That is deliberate — the
fallback is the branch that executes in CI, in tests, and on any night the demo
server is rate-limiting, so it is the branch most worth pinning.
"""
from __future__ import annotations

import pytest

from app.core import routing
from app.core.routing import (
    SOURCE_HAVERSINE, RouteResult, TravelMatrix, Vehicle, avg_visit_seconds,
    fetch_osrm_table, optimize_route, plan_fleet, plan_route, solve_tsp,
)

# A tight cluster in NCR, roughly 1-6 km apart.
BASE = (28.4595, 77.0266)          # Gurugram
STOPS = [
    (28.4700, 77.0300),
    (28.4820, 77.0510),
    (28.5355, 77.3910),            # Noida — the far outlier
    (28.4650, 77.0350),
]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Make every OSRM call fail instantly, so the Haversine fallback runs.

    Raising from httpx.get rather than pointing at a closed port: a refused
    connection still costs ~2.5s per call on Windows, which across this file is
    minutes of waiting for a code path that is meant to be the fast one.
    """
    def _refuse(*a, **kw):
        raise ConnectionError("OSRM unreachable (test fixture)")

    monkeypatch.setattr(routing.httpx, "get", _refuse)


# ---------------------------------------------------------------------------
# Matrix and honesty about provenance
# ---------------------------------------------------------------------------

def test_fallback_is_used_and_declared_when_osrm_is_unreachable():
    """The old fetch_osrm_matrix fell back silently, so nobody could tell how
    often OSRM was actually answering."""
    m = fetch_osrm_table([BASE] + STOPS)
    assert isinstance(m, TravelMatrix)
    assert m.source == SOURCE_HAVERSINE
    assert m.is_road_data is False
    assert len(m) == 5
    assert m.durations[0][0] == 0 and m.distances[0][0] == 0
    assert m.durations[0][1] > 0 and m.distances[0][1] > 0


def test_matrix_carries_distances_not_only_durations():
    """The whole reason callers invented their own crow-flies distance."""
    m = fetch_osrm_table([BASE, STOPS[0]])
    assert m.distances[0][1] > 0
    # Gurugram -> the near stop is a couple of km; sanity-bound it rather than
    # pinning an exact number the detour factor would make brittle.
    assert 500 < m.distances[0][1] < 8_000


def test_matrix_is_symmetric_under_the_fallback():
    m = fetch_osrm_table([BASE] + STOPS)
    for i in range(len(m)):
        for j in range(len(m)):
            assert m.durations[i][j] == m.durations[j][i]


# ---------------------------------------------------------------------------
# Single-vehicle planning
# ---------------------------------------------------------------------------

def test_plan_route_visits_every_stop_exactly_once():
    r = plan_route(STOPS, *BASE)
    assert sorted(r.order) == list(range(len(STOPS)))


def test_plan_route_legs_reconcile_with_the_reported_totals():
    """THE DEFECT THIS FILE EXISTS FOR. plan_route must report the distance and
    duration its own legs add up to — not a separately recomputed estimate."""
    r = plan_route(STOPS, *BASE)
    assert len(r.legs) == len(STOPS)
    assert sum(l.seconds for l in r.legs) == r.travel_seconds
    assert sum(l.metres for l in r.legs) == r.metres
    assert r.service_seconds == avg_visit_seconds() * len(STOPS)
    assert r.total_seconds == r.travel_seconds + r.service_seconds
    assert r.km == round(r.metres / 1000.0, 2)


def test_plan_route_legs_chain_from_the_depot_through_the_order():
    r = plan_route(STOPS, *BASE)
    assert r.legs[0].from_stop is None, "first leg must leave the depot"
    assert r.legs[0].to_stop == r.order[0]
    for prev, leg in zip(r.order, r.legs[1:]):
        assert leg.from_stop == prev
    assert [l.to_stop for l in r.legs] == r.order


def test_plan_route_is_deterministic():
    """A plan that differs between two identical runs cannot be audited."""
    a, b = plan_route(STOPS, *BASE), plan_route(STOPS, *BASE)
    assert a.order == b.order
    assert a.travel_seconds == b.travel_seconds and a.metres == b.metres


def test_plan_route_prefers_near_stops_before_the_far_outlier():
    """Index 2 is Noida, ~30 km from a Gurugram base; it should not be first."""
    r = plan_route(STOPS, *BASE)
    assert r.order[0] != 2


def test_optimize_route_agrees_with_plan_route():
    """The thin wrapper must not drift from the function it wraps — that is how
    two copies of one rule start disagreeing."""
    assert optimize_route(STOPS, *BASE) == plan_route(STOPS, *BASE).order


def test_empty_and_single_stop_are_handled():
    assert plan_route([], *BASE).order == []
    single = plan_route([STOPS[0]], *BASE)
    assert single.order == [0]
    assert single.legs and single.legs[0].seconds > 0
    assert optimize_route([], *BASE) == []
    assert optimize_route([STOPS[0]], *BASE) == [0]


def test_forced_next_is_visited_first_and_still_reconciles():
    r = plan_route(STOPS, *BASE, forced_next=2)
    assert r.order[0] == 2
    assert sorted(r.order) == list(range(len(STOPS)))
    assert sum(l.seconds for l in r.legs) == r.travel_seconds
    assert r.legs[0].from_stop is None and r.legs[0].to_stop == 2


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------

def test_time_windows_are_applied_when_satisfiable():
    # Force stop 3 early and stop 0 late; the order must respect that.
    windows = [(7_200, 14_400), None, None, (0, 3_600)]
    r = plan_route(STOPS, *BASE, time_windows=windows)
    assert r.order.index(3) < r.order.index(0)


def test_an_impossible_window_falls_back_to_an_unwindowed_route():
    """Windows are a soft feature: an infeasible set must still yield a route,
    because an agent with no beat is worse than an agent with an imperfect one."""
    impossible = [(0, 1)] * len(STOPS)          # every stop within one second
    r = plan_route(STOPS, *BASE, time_windows=impossible)
    assert sorted(r.order) == list(range(len(STOPS)))


def test_solve_tsp_returns_depot_first_and_every_node():
    m = fetch_osrm_table([BASE] + STOPS)
    route = solve_tsp(m.durations, depot=0)
    assert route[0] == 0
    assert sorted(route) == list(range(len(STOPS) + 1))


# ---------------------------------------------------------------------------
# The fleet solver
# ---------------------------------------------------------------------------

def _vehicles(n=2, capacity=10):
    return [Vehicle(key=i, start=BASE, capacity=capacity) for i in range(n)]


def test_fleet_assigns_every_stop_when_capacity_allows():
    f = plan_fleet(STOPS, _vehicles())
    assert f.feasible
    served = sorted(s for p in f.plans.values() for s in p.order)
    assert served == list(range(len(STOPS)))
    assert f.dropped == []


def test_fleet_never_assigns_one_stop_to_two_agents():
    f = plan_fleet(STOPS, _vehicles(n=3))
    served = [s for p in f.plans.values() for s in p.order]
    assert len(served) == len(set(served))


def test_fleet_respects_per_vehicle_capacity():
    """Two agents, one stop each allowed, four stops: two must be dropped."""
    f = plan_fleet(STOPS, _vehicles(n=2, capacity=1))
    for plan in f.plans.values():
        assert len(plan.order) <= 1
    assert len(f.dropped) == 2


def test_fleet_drops_the_cheapest_stops_first_when_it_must():
    """PRIZE-COLLECTING. With capacity for two of four, the solver should keep
    the stops carrying the largest drop penalty — which is how 'route by
    expected recovery' is expressed without any ML."""
    penalties = [100, 100, 9_000_000, 9_000_000]
    f = plan_fleet(STOPS, _vehicles(n=1, capacity=2), drop_penalties=penalties)
    kept = sorted(s for p in f.plans.values() for s in p.order)
    assert kept == [2, 3], f"kept the wrong stops: {kept}"
    assert sorted(f.dropped) == [0, 1]


def test_fleet_legs_reconcile_with_totals():
    f = plan_fleet(STOPS, _vehicles())
    for plan in f.plans.values():
        if not plan.order:
            continue
        assert sum(l.seconds for l in plan.legs) == plan.travel_seconds
        assert sum(l.metres for l in plan.legs) == plan.metres
        assert [l.to_stop for l in plan.legs] == plan.order


def test_fleet_is_deterministic():
    a = plan_fleet(STOPS, _vehicles())
    b = plan_fleet(STOPS, _vehicles())
    assert {k: v.order for k, v in a.plans.items()} == {k: v.order for k, v in b.plans.items()}
    assert a.dropped == b.dropped


def test_fleet_honours_a_vehicle_shift_window():
    """A shift too short for any travel must leave the day empty rather than
    silently producing a plan the agent cannot legally execute."""
    tiny = [Vehicle(key=0, start=BASE, capacity=10,
                    shift_start_s=8 * 3600, shift_end_s=8 * 3600 + 30)]
    f = plan_fleet(STOPS, tiny)
    assert all(len(p.order) == 0 for p in f.plans.values())
    assert sorted(f.dropped) == list(range(len(STOPS)))


def test_fleet_with_no_stops_or_no_vehicles_is_empty_not_an_error():
    assert plan_fleet([], _vehicles()).plans[0].order == []
    assert plan_fleet(STOPS, []).plans == {}


def test_fleet_reports_its_source():
    assert plan_fleet(STOPS, _vehicles()).source == SOURCE_HAVERSINE


# ---------------------------------------------------------------------------
# The service-time constant
# ---------------------------------------------------------------------------

def test_service_time_has_exactly_one_definition():
    """There were FOUR numbers for how long a visit takes — 15 in settings and
    30, 20 and 25 hardcoded in planner_service.py. Everything reads this now."""
    from app.core.config import settings

    assert avg_visit_seconds() == settings.AVG_VISIT_DURATION_MINUTES * 60


def test_planner_does_not_reintroduce_its_own_service_constants():
    """A textual guard, in the spirit of the manager-router tenancy sweep: it
    cannot prove correctness, but it fails loudly if someone hardcodes minutes
    per stop back into the planner."""
    import re
    from pathlib import Path

    src = Path("app/services/planner_service.py").read_text(encoding="utf-8")
    # Strip comments so the changelog explaining the old numbers doesn't trip it.
    code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    offenders = re.findall(r"len\(\s*\w+\s*\)\s*\*\s*(?:20|25|30)\b", code)
    assert not offenders, f"per-stop minute constants are back: {offenders}"


# ---------------------------------------------------------------------------
# Eligibility — the compliance constraint on the fleet solver
# ---------------------------------------------------------------------------

def test_fleet_honours_allowed_vehicles():
    """THE GATE THAT MAKES plan_fleet SAFE ON REAL CASES. The allocator applies
    five hard gates before scoring — DNC, hostility, female-agent requirement,
    territory radius and PTP fatigue. A fleet solver free to move any stop to any
    agent would undo every one of them and report a shorter route for doing it."""
    vehicles = [Vehicle(key=0, start=BASE, capacity=10),
                Vehicle(key=1, start=BASE, capacity=10)]
    # Stops 0 and 1 may only be served by vehicle 1.
    allowed = [[1], [1], None, None]
    f = plan_fleet(STOPS, vehicles, allowed_vehicles=allowed)
    assert 0 not in f.plans[0].order and 1 not in f.plans[0].order
    assert 0 in f.plans[1].order and 1 in f.plans[1].order


def test_fleet_drops_a_stop_no_vehicle_may_serve():
    """An empty allowed set means nobody is permitted — a staffing problem, not
    a capacity one. It must be dropped, not quietly assigned anyway."""
    f = plan_fleet(STOPS, _vehicles(n=2), allowed_vehicles=[[], None, None, None])
    assert 0 in f.dropped
    assert all(0 not in p.order for p in f.plans.values())
