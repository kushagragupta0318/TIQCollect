# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. First automated tests in this repo (backend/
#   tests/ was previously just an empty __init__.py) — geo-fence and RBI
#   contact-hours are the compliance-critical functions record_visit relies
#   on. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone

from app.core.geo import haversine_metres, within_geo_fence, is_within_contact_hours, point_in_geojson_polygon

_SQUARE = {"type": "Polygon", "coordinates": [[
    [77.0, 28.4], [77.2, 28.4], [77.2, 28.6], [77.0, 28.6], [77.0, 28.4],
]]}


def test_point_in_geojson_polygon_inside_and_outside():
    assert point_in_geojson_polygon(28.5, 77.1, _SQUARE) is True
    assert point_in_geojson_polygon(12.9, 77.6, _SQUARE) is False


def test_point_in_geojson_polygon_none_or_empty_is_false_not_a_crash():
    assert point_in_geojson_polygon(28.5, 77.1, None) is False
    assert point_in_geojson_polygon(28.5, 77.1, {}) is False
    assert point_in_geojson_polygon(28.5, 77.1, {"type": "Polygon", "coordinates": []}) is False
    assert point_in_geojson_polygon(28.5, 77.1, {"type": "Polygon", "coordinates": [[]]}) is False


def test_multipolygon_with_an_empty_entry_does_not_crash():
    """tiq-auditor LOW: `any(_point_in_ring(lat, lon, poly[0]) for poly in coords)`
    indexed poly[0] before checking poly was non-empty, so one malformed
    entry in an otherwise-valid MultiPolygon raised an unhandled IndexError
    — a 500 on an unrelated request — instead of being treated as "nothing
    to check against" the same way an empty Polygon ring already was."""
    multi_with_empty = {"type": "MultiPolygon", "coordinates": [[], _SQUARE["coordinates"]]}
    assert point_in_geojson_polygon(28.5, 77.1, multi_with_empty) is True
    assert point_in_geojson_polygon(12.9, 77.6, multi_with_empty) is False
    assert point_in_geojson_polygon(28.5, 77.1, {"type": "MultiPolygon", "coordinates": [[]]}) is False


def test_haversine_zero_distance():
    assert haversine_metres(19.076, 72.877, 19.076, 72.877) == 0


def test_within_geo_fence_true_when_close():
    distance, ok = within_geo_fence(19.0760, 72.8770, 19.0761, 72.8771)
    assert ok is True
    assert distance < 100


def test_within_geo_fence_false_when_far():
    distance, ok = within_geo_fence(19.0760, 72.8770, 19.1000, 72.9000)
    assert ok is False


def test_contact_hours_blocks_late_night():
    late_night_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)  # ~1:30 AM IST
    assert is_within_contact_hours(late_night_utc) is False


def test_contact_hours_allows_midday():
    midday_utc = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)  # ~3:30 PM IST
    assert is_within_contact_hours(midday_utc) is True


def test_contact_hours_boundary_8am_ist_allowed():
    start_utc = datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc)  # exactly 8:00 AM IST
    assert is_within_contact_hours(start_utc) is True


def test_contact_hours_boundary_7pm_ist_blocked():
    end_utc = datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc)  # exactly 7:00 PM IST
    assert is_within_contact_hours(end_utc) is False
