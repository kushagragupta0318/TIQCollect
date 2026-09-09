# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — VRPTW + urgent-override support added: solve_tsp() gained an
#   optional time_windows param (real OR-Tools time-dimension constraint);
#   optimize_route() gained time_windows/forced_next/matrix_provider params —
#   forced_next pulls a stop out and forces it immediately after the depot (used
#   when a customer's window is too tight to be a safe soft constraint).
#   matrix_provider is the seam for swapping OSRM for a learned travel-time
#   model later with zero solver changes. _OSRM_BASE now reads
#   settings.OSRM_BASE_URL instead of a hardcoded string.
#
# 2026-09-08 — THE OPTIMISER'S OWN ANSWER IS NOW RETURNED, and a fleet solver
#   added. Four defects, all of which came from this module giving away less
#   than it computed:
#
#   1. optimize_route() returned a bare list of indices. It had just paid for an
#      N x N OSRM table and thrown every number in it away, so
#      planner_service.py:517-527 recomputed the distance as Haversine x 1.15
#      and the duration as km/25 + 20 min per stop. EVERY BEAT ETA SHOWN TO AN
#      AGENT OR A MANAGER WAS CROW-FLIES, even on days OSRM answered perfectly.
#      plan_route() now returns a RouteResult carrying the per-leg seconds and
#      metres the solver actually used.
#
#   2. Only /table was ever called, never /route, so NO ROAD GEOMETRY EXISTED
#      ANYWHERE in the product. Any map could only join stops with straight
#      lines. fetch_osrm_route() returns the encoded polyline and the per-leg
#      figures that go with it.
#
#   3. The fallback was silent. fetch_osrm_matrix() dropped to Haversine on any
#      failure and told nobody, so there was no way to know how often OSRM was
#      even answering. Every result now carries `source`, and it is persisted.
#
#   4. One vehicle at a time. Each agent's day was solved as an independent TSP
#      over cases another stage had already assigned, so the sequencing could
#      never say "this case routes better on someone else's day". plan_fleet()
#      is a real multi-vehicle CVRPTW: per-vehicle depots, capacity, time
#      windows, and drop penalties proportional to what the case is worth.
#
#   DETERMINISM IS PRESERVED AND IT COST SOMETHING. OR-Tools' usual advice for a
#   fleet problem is GUIDED_LOCAL_SEARCH under a wall-clock limit. A wall-clock
#   limit makes the plan depend on how fast the machine was that night, so two
#   identical books produce two different plans and the audit trail stops
#   meaning anything. Search here is bounded by solution_limit — a count of
#   improvements, not seconds — which is reproducible on any hardware. See
#   _search_params.
# ───────────────────────────────────────────────────────────────────────────
"""
Route optimization for field collection beats.

    matrix = fetch_osrm_table(coords)          # durations + distances + source
    result = plan_route(stops, lat, lon)       # one agent's day, sequenced
    fleet  = plan_fleet(stops, agents)         # every agent at once (CVRPTW)

Single-vehicle pipeline
-----------------------
1. fetch_osrm_table()  — real road matrix from OSRM Table API (OSM, free)
2. solve_tsp()         — near-optimal TSP via OR-Tools, optional time windows
3. plan_route()        — orchestrator; returns a RouteResult, never bare indices

The start point (agent's home or current GPS) is the depot, node 0. OSRM uses
lon,lat order; every public interface here uses lat,lon.

TWO MATRIX FUNCTIONS, ON PURPOSE
--------------------------------
`fetch_osrm_matrix` returns a plain list[list[int]] of durations and is the
`matrix_provider` seam: write a function with that signature and pass it to swap
OSRM for a learned travel-time model, with no solver change. That contract is
referenced by the ML plan and is deliberately unchanged.

`fetch_osrm_table` is the richer one — durations, distances and which provider
answered. Use it when you intend to keep the numbers.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Defaults to OSRM's public demo server (no API key, OpenStreetMap data) —
# override OSRM_BASE_URL in .env to point at a self-hosted instance or paid
# provider in production; nothing else in this module needs to change.
_OSRM_BASE = settings.OSRM_BASE_URL
_OSRM_TIMEOUT = 8.0
_BIG = 999_999          # sentinel for unreachable/null pairs

MatrixProvider = Callable[[Sequence[tuple[float, float]]], list[list[int]]]

_TIME_WINDOW_HORIZON = 24 * 3_600
_TIME_WINDOW_SLACK = 24 * 3_600

SOURCE_OSRM = "osrm"
SOURCE_HAVERSINE = "haversine"

# Average urban speed used by the Haversine fallback, and the detour factor that
# turns a straight line into a plausible road distance. Both are fallbacks, not
# estimates anyone should rely on — that is what `source` on every result is for.
_FALLBACK_SPEED_KMH = 25.0
_ROAD_DETOUR_FACTOR = 1.15


def avg_visit_seconds() -> int:
    """The ONE definition of how long a visit takes.

    There were four numbers for this: 15 (settings.AVG_VISIT_DURATION_MINUTES,
    used by the VRPTW time dimension), and 30, 20 and 25 minutes per stop
    hardcoded at three separate points in planner_service.py — so the route was
    made feasible against 15 minutes a visit and then reported to the agent as
    though it were 20, having fallen back to 30 or 25 if anything went wrong.
    Callers import this instead.
    """
    return settings.AVG_VISIT_DURATION_MINUTES * 60


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TravelMatrix:
    """An N x N road matrix, plus an honest statement of where it came from."""

    durations: list[list[int]]          # seconds
    distances: list[list[int]]          # metres
    source: str                         # SOURCE_OSRM | SOURCE_HAVERSINE

    @property
    def is_road_data(self) -> bool:
        return self.source == SOURCE_OSRM

    def __len__(self) -> int:
        return len(self.durations)


@dataclass(frozen=True)
class RouteLeg:
    """One hop. from_stop is None for the leg out of the depot."""

    from_stop: int | None
    to_stop: int
    seconds: int
    metres: int


@dataclass
class RouteResult:
    """One agent's sequenced day, with the numbers the solver actually used."""

    order: list[int]                            # indices into the caller's stops
    legs: list[RouteLeg] = field(default_factory=list)
    travel_seconds: int = 0
    service_seconds: int = 0
    metres: int = 0
    source: str = SOURCE_HAVERSINE
    geometry: str | None = None                 # encoded polyline, OSRM /route
    dropped: list[int] = field(default_factory=list)

    @property
    def total_seconds(self) -> int:
        return self.travel_seconds + self.service_seconds

    @property
    def km(self) -> float:
        return round(self.metres / 1000.0, 2)

    @property
    def minutes(self) -> int:
        return int(round(self.total_seconds / 60.0))


@dataclass
class FleetResult:
    """A whole team's day. `plans` is keyed by the caller's vehicle index."""

    plans: dict[int, RouteResult] = field(default_factory=dict)
    dropped: list[int] = field(default_factory=list)
    source: str = SOURCE_HAVERSINE
    solver: str = "ortools_cvrptw"
    feasible: bool = True


# ---------------------------------------------------------------------------
# Travel-time matrix
# ---------------------------------------------------------------------------

def _haversine_pair(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[int, int]:
    """(seconds, metres) between two points, straight line at a fallback speed."""
    R = 6_371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)) * _ROAD_DETOUR_FACTOR
    return int(dist_km / _FALLBACK_SPEED_KMH * 3_600), int(dist_km * 1000)


def _haversine_table(coords: Sequence[tuple[float, float]]) -> TravelMatrix:
    n = len(coords)
    dur = [[0] * n for _ in range(n)]
    dist = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            s, m = _haversine_pair(*coords[i], *coords[j])
            dur[i][j], dist[i][j] = s, m
    return TravelMatrix(dur, dist, SOURCE_HAVERSINE)


def fetch_osrm_table(coords: Sequence[tuple[float, float]]) -> TravelMatrix:
    """Durations AND distances from OSRM's Table API, or a Haversine fallback.

    Asks for both annotations in one call. The previous version requested only
    durations, which is why every caller that wanted a distance had to invent
    one — and they invented it as crow-flies.
    """
    n = len(coords)
    if n == 0:
        return TravelMatrix([], [], SOURCE_HAVERSINE)

    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = (f"{_OSRM_BASE}/table/v1/driving/{coord_str}"
           f"?annotations=duration,distance")
    try:
        resp = httpx.get(url, timeout=_OSRM_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok":
                raw_d = data.get("durations") or []
                raw_m = data.get("distances") or []
                dur = [[int(raw_d[i][j]) if raw_d[i][j] is not None else _BIG
                        for j in range(n)] for i in range(n)]
                if raw_m:
                    dist = [[int(raw_m[i][j]) if raw_m[i][j] is not None else _BIG
                             for j in range(n)] for i in range(n)]
                else:
                    # Some OSRM builds serve durations only. Derive metres from
                    # the fallback speed rather than reporting zero distance,
                    # and say so by keeping source=osrm for the times.
                    dist = [[int(dur[i][j] / 3600 * _FALLBACK_SPEED_KMH * 1000)
                             if dur[i][j] < _BIG else _BIG
                             for j in range(n)] for i in range(n)]
                return TravelMatrix(dur, dist, SOURCE_OSRM)
            logger.warning("osrm.table.bad_code code=%s", data.get("code"))
        else:
            logger.warning("osrm.table.http_error status=%s", resp.status_code)
    except Exception as exc:
        logger.warning("osrm.table.failed error=%s; using Haversine", exc)

    return _haversine_table(coords)


def fetch_osrm_matrix(coords: Sequence[tuple[float, float]]) -> list[list[int]]:
    """Duration-only matrix. THIS IS THE `matrix_provider` SEAM — see module doc.

    Kept at exactly this signature so a learned travel-time model can be dropped
    in without touching the solver, which is what the ML plan's M1 depends on.
    """
    return fetch_osrm_table(coords).durations


def fetch_osrm_route(coords: Sequence[tuple[float, float]]) -> tuple[str | None, list[RouteLeg], str]:
    """Road geometry for an ordered list of points: (polyline, legs, source).

    This is the call that never existed. Without it the product holds no road
    shape at all, so a map can only draw straight lines between stops — which is
    why BeatMapPage had a decorative SVG and a Google Maps deep-link instead of
    a route.
    """
    if len(coords) < 2:
        return None, [], SOURCE_HAVERSINE

    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = (f"{_OSRM_BASE}/route/v1/driving/{coord_str}"
           f"?overview=full&geometries=polyline&steps=false")
    try:
        resp = httpx.get(url, timeout=_OSRM_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                legs = [
                    RouteLeg(from_stop=i - 1 if i > 0 else None, to_stop=i,
                             seconds=int(leg.get("duration", 0)),
                             metres=int(leg.get("distance", 0)))
                    for i, leg in enumerate(route.get("legs", []))
                ]
                return route.get("geometry"), legs, SOURCE_OSRM
            logger.warning("osrm.route.bad_code code=%s", data.get("code"))
        else:
            logger.warning("osrm.route.http_error status=%s", resp.status_code)
    except Exception as exc:
        logger.warning("osrm.route.failed error=%s", exc)

    legs = []
    for i in range(1, len(coords)):
        s, m = _haversine_pair(*coords[i - 1], *coords[i])
        legs.append(RouteLeg(from_stop=i - 2 if i > 1 else None, to_stop=i - 1,
                             seconds=s, metres=m))
    return None, legs, SOURCE_HAVERSINE


# Backwards-compatible aliases used by existing call sites.
def _haversine_seconds(lat1: float, lon1: float, lat2: float, lon2: float,
                       avg_speed_kmh: float = _FALLBACK_SPEED_KMH) -> int:
    return _haversine_pair(lat1, lon1, lat2, lon2)[0]


def _haversine_matrix(coords: Sequence[tuple[float, float]]) -> list[list[int]]:
    return _haversine_table(coords).durations


# ---------------------------------------------------------------------------
# Solver parameters — determinism lives here
# ---------------------------------------------------------------------------

def _search_params(solution_limit: int | None = None, use_metaheuristic: bool = False):
    """OR-Tools search parameters, with reproducibility as the priority.

    NO WALL-CLOCK LIMIT, ANYWHERE. `time_limit` is the usual OR-Tools advice for
    a fleet problem and it is wrong for this one: the nightly plan would then
    depend on how loaded the machine was at 20:00, so the same book could
    produce two different allocations on two nights and every AllocationDecision
    row explaining "why this case went to this agent" would be unreproducible.

    NO GUIDED LOCAL SEARCH EITHER, and this was learned the hard way. GLS was
    tried first, bounded by `solution_limit` on the reasoning that a count of
    improvements is hardware-independent where seconds are not. It is — but the
    limit is a CEILING, not a guarantee: on a 4-stop, 2-vehicle instance GLS
    never found 40 improving solutions and never stopped, and the routing test
    file hung until it was killed. A metaheuristic needs SOME termination
    condition, and every deterministic one available here is a bound the search
    may simply never reach.

    So the search is plain local descent from PATH_CHEAPEST_ARC: it improves
    until no move helps, then stops. That always terminates, always returns the
    same plan for the same inputs, and gives up some solution quality on large
    instances. For beats of 10-20 stops the gap is small, and a reproducible
    plan is worth more here than an unreproducible better one — the audit trail
    is the product.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    if use_metaheuristic and solution_limit:
        # Retained for a caller that explicitly opts in AND supplies a bound it
        # knows is reachable. Nothing in this repo does.
        p.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
        p.solution_limit = solution_limit
    p.log_search = False
    return p


# ---------------------------------------------------------------------------
# TSP — single vehicle
# ---------------------------------------------------------------------------

def solve_tsp(
    matrix: list[list[int]],
    depot: int = 0,
    time_windows: "Sequence[tuple[int, int] | None] | None" = None,
) -> list[int]:
    """Return an ordered list of *all* node indices (starting from depot).

    Uses OR-Tools PATH_CHEAPEST_ARC — deterministic and near-optimal for up to
    ~20 stops (typical field beat size). No local-search metaheuristic, so the
    same inputs always produce the same route.

    time_windows, if given, must be the same length as matrix; each entry is
    (earliest, latest) in seconds from now, or None for "no constraint" (the
    depot entry should always be None). A stop whose window can't be met makes
    the solve infeasible — we retry once without windows so a route is always
    returned (windows are a soft feature, not a hard blocker).

    Falls back to nearest-neighbour greedy if OR-Tools is unavailable.
    """
    n = len(matrix)
    if n <= 1:
        return list(range(n))

    try:
        from ortools.constraint_solver import pywrapcp

        manager = pywrapcp.RoutingIndexManager(n, 1, depot)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_idx: int, to_idx: int) -> int:
            return matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

        cb = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(cb)

        if time_windows is not None:
            service = avg_visit_seconds()

            def time_with_service(from_idx: int, to_idx: int) -> int:
                f = manager.IndexToNode(from_idx)
                t = manager.IndexToNode(to_idx)
                return matrix[f][t] + (0 if f == depot else service)

            time_cb = routing.RegisterTransitCallback(time_with_service)
            routing.AddDimension(time_cb, _TIME_WINDOW_SLACK, _TIME_WINDOW_HORIZON,
                                 False, "Time")
            dim = routing.GetDimensionOrDie("Time")
            for node, window in enumerate(time_windows):
                if window is None:
                    continue
                dim.CumulVar(manager.NodeToIndex(node)).SetRange(*window)

        solution = routing.SolveWithParameters(_search_params())
        if solution:
            route: list[int] = []
            idx = routing.Start(0)
            while not routing.IsEnd(idx):
                route.append(manager.IndexToNode(idx))
                idx = solution.Value(routing.NextVar(idx))
            return route

        if time_windows is not None:
            logger.warning("tsp.infeasible_with_windows; retrying without")
            return solve_tsp(matrix, depot, time_windows=None)

    except Exception as exc:
        logger.warning("tsp.ortools_failed error=%s; nearest-neighbour", exc)

    return _nearest_neighbour(matrix, depot)


def _nearest_neighbour(matrix: list[list[int]], start: int) -> list[int]:
    n = len(matrix)
    visited: set[int] = {start}
    route = [start]
    cur = start
    while len(visited) < n:
        nxt = min((j for j in range(n) if j not in visited),
                  key=lambda j: matrix[cur][j], default=None)
        if nxt is None:
            break
        route.append(nxt)
        visited.add(nxt)
        cur = nxt
    return route


# ---------------------------------------------------------------------------
# Public: single agent
# ---------------------------------------------------------------------------

def plan_route(
    case_coords: Sequence[tuple[float, float]],
    start_lat: float,
    start_lon: float,
    time_windows: "Sequence[tuple[int, int] | None] | None" = None,
    forced_next: int | None = None,
    matrix: TravelMatrix | None = None,
    with_geometry: bool = False,
) -> RouteResult:
    """Sequence one agent's stops AND return the numbers the solver used.

    This is what optimize_route() should always have returned. `with_geometry`
    makes a second OSRM call for the road shape — off by default because the
    nightly planner builds many beats and only the ones a map will draw need it.
    """
    if not case_coords:
        return RouteResult(order=[])
    if len(case_coords) == 1:
        m = matrix or fetch_osrm_table([(start_lat, start_lon), case_coords[0]])
        legs = [RouteLeg(None, 0, m.durations[0][1], m.distances[0][1])]
        return RouteResult(order=[0], legs=legs, travel_seconds=legs[0].seconds,
                           service_seconds=avg_visit_seconds(),
                           metres=legs[0].metres, source=m.source)

    if forced_next is not None:
        # Pull the urgent stop out, force it first, optimise the rest from there.
        f_lat, f_lon = case_coords[forced_next]
        rest_idx = [i for i in range(len(case_coords)) if i != forced_next]
        rest = [case_coords[i] for i in rest_idx]
        rest_windows = ([time_windows[i] for i in rest_idx]
                        if time_windows is not None else None)
        tail = plan_route(rest, f_lat, f_lon, time_windows=rest_windows,
                          with_geometry=with_geometry)
        head = fetch_osrm_table([(start_lat, start_lon), (f_lat, f_lon)])
        first = RouteLeg(None, forced_next, head.durations[0][1], head.distances[0][1])
        legs = [first] + [
            RouteLeg(rest_idx[l.from_stop] if l.from_stop is not None else forced_next,
                     rest_idx[l.to_stop], l.seconds, l.metres)
            for l in tail.legs
        ]
        return RouteResult(
            order=[forced_next] + [rest_idx[i] for i in tail.order],
            legs=legs,
            travel_seconds=first.seconds + tail.travel_seconds,
            service_seconds=avg_visit_seconds() * (1 + len(tail.order)),
            metres=first.metres + tail.metres,
            source=head.source if head.source == tail.source else SOURCE_HAVERSINE,
            geometry=tail.geometry,
        )

    all_coords = [(start_lat, start_lon)] + list(case_coords)
    m = matrix or fetch_osrm_table(all_coords)
    all_windows = [None, *time_windows] if time_windows is not None else None
    ordered_nodes = solve_tsp(m.durations, depot=0, time_windows=all_windows)

    order = [i - 1 for i in ordered_nodes if i > 0]
    legs, travel, metres = [], 0, 0
    prev_node = 0
    for node in ordered_nodes[1:]:
        secs = m.durations[prev_node][node]
        dist = m.distances[prev_node][node]
        legs.append(RouteLeg(from_stop=prev_node - 1 if prev_node > 0 else None,
                             to_stop=node - 1, seconds=secs, metres=dist))
        travel += secs
        metres += dist
        prev_node = node

    geometry = None
    if with_geometry:
        geometry, _, _ = fetch_osrm_route(
            [(start_lat, start_lon)] + [case_coords[i] for i in order])

    return RouteResult(order=order, legs=legs, travel_seconds=travel,
                       service_seconds=avg_visit_seconds() * len(order),
                       metres=metres, source=m.source, geometry=geometry)


def optimize_route(
    case_coords: Sequence[tuple[float, float]],
    start_lat: float,
    start_lon: float,
    time_windows: "Sequence[tuple[int, int] | None] | None" = None,
    forced_next: int | None = None,
    matrix_provider: MatrixProvider = fetch_osrm_matrix,
) -> list[int]:
    """Return the optimized visiting order as indices into *case_coords*.

    THIN WRAPPER, KEPT FOR THE EXISTING CALLERS. New code should call
    plan_route(), which returns the same order plus the per-leg seconds and
    metres — throwing those away is precisely the defect fixed on 2026-09-08.

    `matrix_provider` is honoured here because it is the documented seam for a
    learned travel-time model.
    """
    if not case_coords:
        return []
    if len(case_coords) == 1:
        return [0]

    if forced_next is not None:
        f_lat, f_lon = case_coords[forced_next]
        rest_idx = [i for i in range(len(case_coords)) if i != forced_next]
        rest = [case_coords[i] for i in rest_idx]
        rest_windows = ([time_windows[i] for i in rest_idx]
                        if time_windows is not None else None)
        tail = optimize_route(rest, f_lat, f_lon, time_windows=rest_windows,
                              matrix_provider=matrix_provider)
        return [forced_next] + [rest_idx[i] for i in tail]

    all_coords = [(start_lat, start_lon)] + list(case_coords)
    durations = matrix_provider(all_coords)
    all_windows = [None, *time_windows] if time_windows is not None else None
    ordered = solve_tsp(durations, depot=0, time_windows=all_windows)
    return [i - 1 for i in ordered if i > 0]


# ---------------------------------------------------------------------------
# Public: the whole fleet — CVRPTW
# ---------------------------------------------------------------------------

@dataclass
class Vehicle:
    """One agent, as the solver sees them."""

    key: int                        # caller's index; echoed back in FleetResult
    start: tuple[float, float]      # base lat/lon
    capacity: int                   # max stops for the day
    shift_start_s: int = 0          # seconds from midnight
    shift_end_s: int = 24 * 3_600


def plan_fleet(
    stops: Sequence[tuple[float, float]],
    vehicles: Sequence[Vehicle],
    *,
    time_windows: "Sequence[tuple[int, int] | None] | None" = None,
    drop_penalties: Sequence[int] | None = None,
    service_seconds: Sequence[int] | None = None,
    allowed_vehicles: "Sequence[Sequence[int] | None] | None" = None,
) -> FleetResult:
    """Assign AND sequence every stop across every agent in one solve.

    WHY THIS EXISTS. The planner previously solved one TSP per agent over cases
    a separate bipartite stage had already assigned. Sequencing therefore could
    never say "this case belongs on someone else's day" — the two halves of the
    same problem were decided in sequence, by different objectives, and stage 2
    could only defer an outlier it had no power to reassign.

    DROP PENALTIES ARE WHAT MAKE IT PRIZE-COLLECTING. Every stop gets a
    disjunction: the solver may leave it out, at a cost. Set that cost from what
    the case is worth and the day fills with the most valuable feasible work
    rather than the nearest — which is the whole of "recovery-optimised routing"
    and needs no ML at all. A stop with no penalty given falls back to a high
    constant, i.e. "drop only if you must".

    `allowed_vehicles` IS A COMPLIANCE MECHANISM, NOT AN OPTIMISATION HINT, and
    without it this function must never be used on real cases. The allocator
    applies five hard gates BEFORE any scoring — do-not-contact, hostility, the
    female-agent requirement, the 16 km territory boundary and PTP fatigue — and
    a fleet solver that is free to move any stop to any vehicle would silently
    undo all of them while reporting a shorter route. Passing the eligible
    vehicle set per stop constrains the solve to pairings the gates already
    permit, so the guarantees hold by construction rather than by re-testing.
    None for a stop means "any vehicle"; an EMPTY list means the stop cannot be
    served by anyone and is dropped.
    """
    n_stops, n_veh = len(stops), len(vehicles)
    if n_stops == 0 or n_veh == 0:
        return FleetResult(plans={v.key: RouteResult(order=[]) for v in vehicles})

    # Node layout: 0..n_veh-1 are the vehicle depots, then the stops.
    coords = [v.start for v in vehicles] + list(stops)
    m = fetch_osrm_table(coords)

    def stop_node(i: int) -> int:
        return n_veh + i

    try:
        from ortools.constraint_solver import pywrapcp

        starts = list(range(n_veh))
        manager = pywrapcp.RoutingIndexManager(len(coords), n_veh, starts, starts)
        routing = pywrapcp.RoutingModel(manager)

        service = list(service_seconds) if service_seconds else [avg_visit_seconds()] * n_stops

        def transit(from_idx: int, to_idx: int) -> int:
            f = manager.IndexToNode(from_idx)
            t = manager.IndexToNode(to_idx)
            extra = service[f - n_veh] if f >= n_veh else 0
            return m.durations[f][t] + extra

        cb = routing.RegisterTransitCallback(transit)
        routing.SetArcCostEvaluatorOfAllVehicles(cb)

        # Capacity: one unit of demand per stop, depots zero.
        def demand(from_idx: int) -> int:
            return 0 if manager.IndexToNode(from_idx) < n_veh else 1

        dcb = routing.RegisterUnaryTransitCallback(demand)
        routing.AddDimensionWithVehicleCapacity(
            dcb, 0, [max(1, v.capacity) for v in vehicles], True, "Capacity")

        # Time, with each vehicle's own shift as its depot window.
        routing.AddDimension(cb, _TIME_WINDOW_SLACK, _TIME_WINDOW_HORIZON, False, "Time")
        tdim = routing.GetDimensionOrDie("Time")
        for vi, veh in enumerate(vehicles):
            tdim.CumulVar(routing.Start(vi)).SetRange(veh.shift_start_s, veh.shift_end_s)
            tdim.CumulVar(routing.End(vi)).SetRange(veh.shift_start_s, veh.shift_end_s)
        if time_windows is not None:
            for i, window in enumerate(time_windows):
                if window is None:
                    continue
                lo, hi = window
                tdim.CumulVar(manager.NodeToIndex(stop_node(i))).SetRange(int(lo), int(hi))

        # Eligibility: restrict each stop to the vehicles allowed to serve it.
        # Done before the disjunctions so an unservable stop still has a defined
        # way out (it is dropped) rather than making the model infeasible.
        if allowed_vehicles is not None:
            for i, allowed in enumerate(allowed_vehicles):
                if allowed is None:
                    continue
                idx = manager.NodeToIndex(stop_node(i))
                routing.VehicleVar(idx).SetValues([-1, *[int(v) for v in allowed]])

        # Disjunctions: a stop may be dropped, at a price.
        default_penalty = 10_000_000
        for i in range(n_stops):
            pen = int(drop_penalties[i]) if drop_penalties else default_penalty
            routing.AddDisjunction([manager.NodeToIndex(stop_node(i))], max(pen, 1))

        solution = routing.SolveWithParameters(_search_params())
        if solution is None:
            logger.warning("fleet.infeasible n_stops=%d n_vehicles=%d", n_stops, n_veh)
            return FleetResult(plans={v.key: RouteResult(order=[]) for v in vehicles},
                               dropped=list(range(n_stops)), source=m.source,
                               feasible=False)

        plans: dict[int, RouteResult] = {}
        served: set[int] = set()
        for vi, veh in enumerate(vehicles):
            order, legs, travel, metres = [], [], 0, 0
            idx = routing.Start(vi)
            prev_node = manager.IndexToNode(idx)
            idx = solution.Value(routing.NextVar(idx))
            while not routing.IsEnd(idx):
                node = manager.IndexToNode(idx)
                s_i = node - n_veh
                secs, dist = m.durations[prev_node][node], m.distances[prev_node][node]
                legs.append(RouteLeg(
                    from_stop=prev_node - n_veh if prev_node >= n_veh else None,
                    to_stop=s_i, seconds=secs, metres=dist))
                travel += secs
                metres += dist
                order.append(s_i)
                served.add(s_i)
                prev_node = node
                idx = solution.Value(routing.NextVar(idx))
            plans[veh.key] = RouteResult(
                order=order, legs=legs, travel_seconds=travel,
                service_seconds=sum(service[i] for i in order),
                metres=metres, source=m.source)

        return FleetResult(plans=plans,
                           dropped=sorted(set(range(n_stops)) - served),
                           source=m.source, feasible=True)

    except Exception as exc:
        logger.warning("fleet.solver_failed error=%s; no fleet plan produced", exc)
        return FleetResult(plans={v.key: RouteResult(order=[]) for v in vehicles},
                           dropped=list(range(n_stops)), source=m.source,
                           solver="failed", feasible=False)
