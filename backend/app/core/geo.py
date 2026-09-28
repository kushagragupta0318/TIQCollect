from math import radians, sin, cos, sqrt, atan2
from datetime import datetime, timezone, timedelta

from app.core.config import settings

EARTH_RADIUS_M = 6_371_000

IST = timezone(timedelta(hours=5, minutes=30))

# Sourced from settings so the fence radius and RBI contact window are .env
# changes, not code changes. The names are kept (visit_service imports them)
# but now resolve to the configured values — override via GEO_FENCE_METRES /
# CONTACT_HOUR_START / CONTACT_HOUR_END in .env (e.g. to widen the window for a
# non-IST demo).
GEO_FENCE_METRES = settings.GEO_FENCE_METRES
RBI_CONTACT_START = settings.CONTACT_HOUR_START   # 8 AM IST by default
RBI_CONTACT_END = settings.CONTACT_HOUR_END       # 7 PM IST by default


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lam = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lam / 2) ** 2
    return 2 * EARTH_RADIUS_M * atan2(sqrt(a), sqrt(1 - a))


def is_within_contact_hours(dt: datetime | None = None) -> bool:
    """RBI mandates collections contact between 8 AM and 7 PM IST only."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    ist_hour = dt.astimezone(IST).hour
    return RBI_CONTACT_START <= ist_hour < RBI_CONTACT_END


def within_geo_fence(
    visit_lat: float, visit_lon: float,
    customer_lat: float, customer_lon: float,
) -> tuple[float, bool]:
    """Returns (distance_metres, is_within_fence)."""
    dist = haversine_metres(visit_lat, visit_lon, customer_lat, customer_lon)
    return dist, dist <= GEO_FENCE_METRES


def _point_in_ring(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Ray casting on one GeoJSON linear ring: [[lon, lat], ...] — GeoJSON
    coordinate order is (lon, lat), the reverse of this codebase's own
    (lat, lon) convention everywhere else, so the swap happens once, here."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x_at_lat = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at_lat:
                inside = not inside
    return inside


def point_in_geojson_polygon(lat: float, lon: float, geojson: dict | None) -> bool:
    """True if (lat, lon) falls inside a GeoJSON Polygon or MultiPolygon's
    outer ring, ignoring holes (interior rings) — good enough for "is this
    base location roughly inside the region's coverage area", not a survey
    tool. `geojson=None` (most regions today have no coverage_geojson at
    all) is treated as "nothing to check against" by the CALLER, not here —
    this function only answers the geometry question it's asked."""
    if not geojson:
        return False
    kind = geojson.get("type")
    coords = geojson.get("coordinates")
    if kind == "Polygon" and coords:
        return _point_in_ring(lat, lon, coords[0])
    if kind == "MultiPolygon" and coords:
        return any(_point_in_ring(lat, lon, poly[0]) for poly in coords)
    return False
