# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. First automated tests in this repo (backend/
#   tests/ was previously just an empty __init__.py) — geo-fence and RBI
#   contact-hours are the compliance-critical functions record_visit relies
#   on. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone

from app.core.geo import haversine_metres, within_geo_fence, is_within_contact_hours


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
