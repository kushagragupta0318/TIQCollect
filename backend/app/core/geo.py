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
