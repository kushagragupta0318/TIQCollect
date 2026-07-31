# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — VRPTW + urgent-override support added: solve_tsp() gained an
#   optional time_windows param (real OR-Tools time-dimension constraint,
#   line ~107); optimize_route() gained time_windows/forced_next/
#   matrix_provider params (line ~218) — forced_next pulls a stop out and
#   forces it immediately after the depot (used when a customer's window is
#   too tight to be a safe soft constraint). matrix_provider is the seam for
#   swapping OSRM for a learned travel-time model later with zero solver
#   changes. _OSRM_BASE now reads settings.OSRM_BASE_URL instead of a
#   hardcoded string. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
Route optimization for field collection beats.

Pipeline
--------
1. fetch_osrm_matrix()   — real road-time matrix from OSRM Table API (OSM, free)
2. solve_tsp()           — exact/near-optimal TSP via Google OR-Tools, with
                           optional per-stop time windows (VRPTW)
3. optimize_route()      — orchestrator; falls back to nearest-neighbour on any failure

The start point (agent's home or current GPS) is treated as the depot (node 0).
OSRM uses lon,lat order; all public interfaces use lat,lon for consistency.

optimize_route() takes the matrix source as a parameter (matrix_provider,
default fetch_osrm_matrix). To swap in a learned travel-time model later,
write a function with the same signature and pass it as matrix_provider —
OR-Tools' role (sequencing) is unaffected either way.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Sequence

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Defaults to OSRM's public demo server (no API key, OpenStreetMap data) —
# override OSRM_BASE_URL in .env to point at a self-hosted instance or paid
# provider in production; nothing else in this module needs to change.
_OSRM_BASE = settings.OSRM_BASE_URL
_OSRM_TIMEOUT = 8.0   # seconds
_BIG = 999_999         # sentinel for unreachable/null pairs in matrix

MatrixProvider = Callable[[Sequence[tuple[float, float]]], list[list[int]]]

_TIME_WINDOW_HORIZON = 24 * 3_600   # a stop may be reached up to a day out
_TIME_WINDOW_SLACK = 24 * 3_600     # allow waiting at a stop for its window

# Estimated real time spent doing a visit — folded into window-feasibility
# checks so cumulative time reflects "drive + visit," not just driving.
_AVG_VISIT_SECONDS = settings.AVG_VISIT_DURATION_MINUTES * 60


# ---------------------------------------------------------------------------
# Travel-time matrix
# ---------------------------------------------------------------------------

def fetch_osrm_matrix(coords: Sequence[tuple[float, float]]) -> list[list[int]]:
    """Return an N×N travel-time matrix (seconds) via OSRM Table API.

    coords — list of (lat, lon) pairs; node 0 is the depot (agent start).
    Falls back to Haversine-estimated times if OSRM is unreachable.
    """
    n = len(coords)
    # OSRM expects lon,lat
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{_OSRM_BASE}/table/v1/driving/{coord_str}?annotations=duration"

    try:
        resp = httpx.get(url, timeout=_OSRM_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok":
                raw = data["durations"]          # list[list[float | None]]
                return [
                    [int(raw[i][j]) if raw[i][j] is not None else _BIG for j in range(n)]
                    for i in range(n)
                ]
            logger.warning("OSRM returned code=%s", data.get("code"))
    except Exception as exc:
        logger.warning("OSRM request failed (%s); falling back to Haversine", exc)

    return _haversine_matrix(coords)


def _haversine_seconds(lat1: float, lon1: float, lat2: float, lon2: float,
                       avg_speed_kmh: float = 25.0) -> int:
    """Straight-line distance converted to travel time at avg_speed_kmh."""
    R = 6_371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(dist_km / avg_speed_kmh * 3_600)


def _haversine_matrix(coords: Sequence[tuple[float, float]]) -> list[list[int]]:
    n = len(coords)
    return [
        [_haversine_seconds(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
         for j in range(n)]
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# TSP solver — OR-Tools with nearest-neighbour fallback
# ---------------------------------------------------------------------------

def solve_tsp(
    matrix: list[list[int]],
    depot: int = 0,
    time_windows: "Sequence[tuple[int, int] | None] | None" = None,
) -> list[int]:
    """Return an ordered list of *all* node indices (starting from depot).

    Uses OR-Tools PATH_CHEAPEST_ARC — deterministic and near-optimal for
    up to ~20 stops (typical field beat size). No local-search metaheuristic
    so the same inputs always produce the same route.

    time_windows, if given, must be the same length as matrix/depot indexing;
    each entry is (earliest, latest) in seconds from now, or None for "no
    constraint" (the depot entry should always be None). A stop whose window
    can't be met without violating another stop's window makes the solve
    infeasible — in that case we retry once without windows so a route is
    still always returned (windows are a soft feature, not a hard blocker).

    Falls back to nearest-neighbour greedy if OR-Tools is unavailable.
    """
    n = len(matrix)
    if n <= 1:
        return list(range(n))

    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2

        manager = pywrapcp.RoutingIndexManager(n, 1, depot)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_idx: int, to_idx: int) -> int:
            return matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

        cb = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(cb)

        if time_windows is not None:
            # Separate callback for window feasibility: travel time *plus* the
            # time actually spent doing the visit at the departure stop (zero
            # at the depot). Using this only for the dimension — not for the
            # cost evaluator above — keeps the optimizer's objective (minimize
            # total driving time) unchanged; adding the same constant service
            # time to every arc doesn't change the optimal order anyway, but
            # keeping cost and feasibility concerns in separate callbacks is
            # clearer than relying on that not mattering.
            def time_with_service_callback(from_idx: int, to_idx: int) -> int:
                from_node = manager.IndexToNode(from_idx)
                to_node = manager.IndexToNode(to_idx)
                service = 0 if from_node == depot else _AVG_VISIT_SECONDS
                return matrix[from_node][to_node] + service

            time_cb = routing.RegisterTransitCallback(time_with_service_callback)
            routing.AddDimension(
                time_cb, _TIME_WINDOW_SLACK, _TIME_WINDOW_HORIZON, False, "Time"
            )
            time_dim = routing.GetDimensionOrDie("Time")
            for node, window in enumerate(time_windows):
                if window is None:
                    continue
                index = manager.NodeToIndex(node)
                time_dim.CumulVar(index).SetRange(*window)

        params = pywrapcp.DefaultRoutingSearchParameters()
        # PATH_CHEAPEST_ARC: fully deterministic, no randomness, good quality for N ≤ 20
        params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        # No local_search_metaheuristic — keeps results stable across identical calls

        solution = routing.SolveWithParameters(params)
        if solution:
            route: list[int] = []
            idx = routing.Start(0)
            while not routing.IsEnd(idx):
                route.append(manager.IndexToNode(idx))
                idx = solution.Value(routing.NextVar(idx))
            return route   # includes depot as first element

        if time_windows is not None:
            logger.warning("TSP infeasible with time windows; retrying without")
            return solve_tsp(matrix, depot, time_windows=None)

    except Exception as exc:
        logger.warning("OR-Tools TSP failed (%s); using nearest-neighbour", exc)

    return _nearest_neighbour(matrix, depot)


def _nearest_neighbour(matrix: list[list[int]], start: int) -> list[int]:
    n = len(matrix)
    visited: set[int] = {start}
    route = [start]
    cur = start
    while len(visited) < n:
        nxt = min(
            (j for j in range(n) if j not in visited),
            key=lambda j: matrix[cur][j],
            default=None,
        )
        if nxt is None:
            break
        route.append(nxt)
        visited.add(nxt)
        cur = nxt
    return route


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def optimize_route(
    case_coords: Sequence[tuple[float, float]],
    start_lat: float,
    start_lon: float,
    time_windows: "Sequence[tuple[int, int] | None] | None" = None,
    forced_next: int | None = None,
    matrix_provider: MatrixProvider = fetch_osrm_matrix,
) -> list[int]:
    """Return optimized visiting order as indices into *case_coords*.

    The start position (depot) is prepended internally as node 0.
    Returned indices index into the original case_coords list (0-based).

    time_windows — optional, same length/order as case_coords; each entry is
    (earliest, latest) in seconds from now, or None for "no constraint" (see
    solve_tsp for infeasibility handling).

    forced_next — optional index into case_coords that must be visited
    immediately after the depot regardless of cost (e.g. an urgent,
    just-called-in visit request). The rest of the stops are still optimized
    normally, starting from that stop's location as the new depot. Use this
    instead of a very tight time_windows entry — a hard "must arrive within
    30 min" window can make the whole solve infeasible, whereas forcing the
    stop next never fails.

    matrix_provider — the travel-time source, default fetch_osrm_matrix.
    Swap this for a learned travel-time model later (same signature); the
    TSP/VRPTW solving below is unaffected either way.
    """
    if not case_coords:
        return []
    if len(case_coords) == 1:
        return [0]

    if forced_next is not None:
        forced_lat, forced_lon = case_coords[forced_next]
        rest_indices = [i for i in range(len(case_coords)) if i != forced_next]
        rest_coords = [case_coords[i] for i in rest_indices]
        rest_windows = (
            [time_windows[i] for i in rest_indices] if time_windows is not None else None
        )
        rest_order = optimize_route(
            rest_coords, forced_lat, forced_lon,
            time_windows=rest_windows, matrix_provider=matrix_provider,
        )
        return [forced_next] + [rest_indices[i] for i in rest_order]

    # Depot = node 0, cases = nodes 1..N
    all_coords = [(start_lat, start_lon)] + list(case_coords)
    matrix = matrix_provider(all_coords)
    all_windows = [None, *time_windows] if time_windows is not None else None
    ordered = solve_tsp(matrix, depot=0, time_windows=all_windows)

    # Strip depot (index 0) and shift indices to match case_coords
    return [i - 1 for i in ordered if i > 0]
