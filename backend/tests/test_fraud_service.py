# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-19. Covers services/fraud_service.py. The checks are pure
# arithmetic over Visit fields, so they are exercised with SimpleNamespace
# stand-ins and no database — same style as test_payment_service.py.
#
# The negative cases matter more than the positive ones here. A detector that
# cries wolf teaches a manager to dismiss the panel, so every check has a test
# proving it stays quiet on the legitimate shape it most resembles.
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.config import settings
from app.services.fraud_service import (
    DUPLICATE_PHOTO, FAR_FROM_CUSTOMER, HIGH, IMPOSSIBLE_TRAVEL, LOW, MEDIUM,
    OVERLAPPING_VISITS, PHOTO_LOCATION_MISMATCH, VISIT_TOO_SHORT,
    FraudService, implied_speed_kmh,
)

SVC = FraudService(db=None)
T0 = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)

# Connaught Place, and a point ~30 km away in Gurugram.
CP = (28.6315, 77.2167)
GURGAON = (28.4595, 77.0266)


def visit(vid="v1", *, lat=CP[0], lon=CP[1], t_in=T0, mins=20, met=True,
          dist=25.0, verified=True, photos=None, hashes=None):
    p = photos or {}
    h = hashes or {}
    return SimpleNamespace(
        id=vid, agent_id="a1", case_id="c1",
        check_in_latitude=lat, check_in_longitude=lon,
        check_in_time=t_in, check_out_time=t_in + timedelta(minutes=mins),
        customer_met=met, distance_from_customer_metres=dist, geo_verified=verified,
        agent_photo_lat=p.get("alat"), agent_photo_lon=p.get("alon"),
        borrower_photo_lat=p.get("blat"), borrower_photo_lon=p.get("blon"),
        object_photo_lat=None, object_photo_lon=None,
        agent_photo_sha256=h.get("a"), borrower_photo_sha256=h.get("b"),
        object_photo_sha256=None,
        agent=SimpleNamespace(employee_code="EMP1", user=SimpleNamespace(full_name="Test Agent")),
        case=SimpleNamespace(case_number="CASE1"),
    )


# ── implied_speed_kmh ────────────────────────────────────────────────────────
def test_speed_basic():
    assert round(implied_speed_kmh(1000, 60)) == 60          # 1 km in 1 min


def test_speed_none_when_no_time_passed():
    """Zero or negative gaps are overlaps, reported separately — not a division
    by zero and not an infinite speed."""
    assert implied_speed_kmh(1000, 0) is None
    assert implied_speed_kmh(1000, -30) is None


# ── impossible travel ────────────────────────────────────────────────────────
def test_impossible_travel_flagged():
    a = visit("v1", mins=10)
    b = visit("v2", lat=GURGAON[0], lon=GURGAON[1], t_in=T0 + timedelta(minutes=20))
    found = SVC._travel_and_overlap([a, b])
    assert [f["type"] for f in found] == [IMPOSSIBLE_TRAVEL]
    assert found[0]["severity"] == HIGH
    assert found[0]["evidence"]["implied_speed_kmh"] > settings.FRAUD_MAX_SPEED_KMH


def test_realistic_round_is_not_flagged():
    """30 km with two hours between visits is an ordinary NCR round."""
    a = visit("v1", mins=20)
    b = visit("v2", lat=GURGAON[0], lon=GURGAON[1], t_in=T0 + timedelta(hours=2))
    assert SVC._travel_and_overlap([a, b]) == []


def test_short_hop_never_flagged():
    a = visit("v1", mins=15)
    b = visit("v2", lat=CP[0] + 0.004, lon=CP[1], t_in=T0 + timedelta(minutes=30))
    assert SVC._travel_and_overlap([a, b]) == []


def test_visits_on_different_days_not_compared():
    """Yesterday's last visit and today's first are not a journey."""
    a = visit("v1", mins=20)
    b = visit("v2", lat=GURGAON[0], lon=GURGAON[1], t_in=T0 + timedelta(days=1))
    assert SVC._travel_and_overlap([a, b]) == []


# ── overlapping visits ───────────────────────────────────────────────────────
def test_overlap_across_distance_is_high():
    a = visit("v1", mins=90)                                  # runs until 10:30
    b = visit("v2", lat=GURGAON[0], lon=GURGAON[1], t_in=T0 + timedelta(minutes=30))
    found = SVC._travel_and_overlap([a, b])
    assert found[0]["type"] == OVERLAPPING_VISITS
    assert found[0]["severity"] == HIGH


def test_overlap_at_same_address_is_low():
    """Co-borrowers and multiple loans in one building genuinely produce two
    visits at one doorstep; a missed check-out is admin, not fraud."""
    a = visit("v1", mins=90)
    b = visit("v2", t_in=T0 + timedelta(minutes=30))           # same coordinates
    found = SVC._travel_and_overlap([a, b])
    assert found[0]["type"] == OVERLAPPING_VISITS
    assert found[0]["severity"] == LOW


# ── photo location ───────────────────────────────────────────────────────────
def test_photo_taken_elsewhere_flagged():
    v = visit(photos={"alat": GURGAON[0], "alon": GURGAON[1]})
    found = SVC._photo_location([v])
    assert [f["type"] for f in found] == [PHOTO_LOCATION_MISMATCH]
    assert found[0]["evidence"]["drift_metres"] > settings.FRAUD_PHOTO_DRIFT_METRES


def test_photo_at_the_door_not_flagged():
    v = visit(photos={"alat": CP[0] + 0.0005, "alon": CP[1]})  # ~55 m — GPS scatter
    assert SVC._photo_location([v]) == []


def test_missing_photo_gps_is_not_a_finding():
    """Most rows have no photo GPS at all. Absence of evidence must not be
    reported as evidence of wrongdoing."""
    assert SVC._photo_location([visit()]) == []


# ── duplicate photos ─────────────────────────────────────────────────────────
def test_duplicate_hash_across_visits_flagged():
    a = visit("v1", hashes={"a": "deadbeef" * 8})
    b = visit("v2", t_in=T0 + timedelta(hours=3), hashes={"a": "deadbeef" * 8})
    found = SVC._duplicate_photos([a, b])
    assert [f["type"] for f in found] == [DUPLICATE_PHOTO]
    assert found[0]["evidence"]["first_seen_visit_id"] == "v1"


def test_distinct_photos_not_flagged():
    a = visit("v1", hashes={"a": "a" * 64})
    b = visit("v2", hashes={"a": "b" * 64})
    assert SVC._duplicate_photos([a, b]) == []


def test_same_hash_on_one_visit_is_not_a_duplicate():
    """One image used as both agent and borrower photo on the SAME visit is
    caught, but a single photo on a single visit must not self-report."""
    assert SVC._duplicate_photos([visit("v1", hashes={"a": "c" * 64})]) == []


# ── short visits ─────────────────────────────────────────────────────────────
def test_sub_minute_visit_with_customer_met_is_medium():
    v = visit(mins=0, met=True)
    v.check_out_time = v.check_in_time + timedelta(seconds=20)
    found = SVC._short_visits([v])
    assert found[0]["type"] == VISIT_TOO_SHORT
    assert found[0]["severity"] == MEDIUM


def test_sub_minute_visit_without_meeting_is_low():
    """Nobody home is legitimately quick — worth noting, not worth alarming."""
    v = visit(met=False)
    v.check_out_time = v.check_in_time + timedelta(seconds=20)
    assert SVC._short_visits([v])[0]["severity"] == LOW


def test_normal_visit_not_flagged():
    assert SVC._short_visits([visit(mins=20)]) == []


def test_visit_without_checkout_is_not_flagged():
    v = visit()
    v.check_out_time = None
    assert SVC._short_visits([v]) == []


# ── distance from customer ───────────────────────────────────────────────────
def test_recorded_far_outside_the_fence_flagged():
    v = visit(dist=settings.GEO_FENCE_METRES * 5, verified=False)
    found = SVC._far_from_customer([v])
    assert found[0]["type"] == FAR_FROM_CUSTOMER
    assert found[0]["severity"] == HIGH


def test_inside_the_fence_not_flagged():
    assert SVC._far_from_customer([visit(dist=40.0)]) == []


def test_just_outside_the_fence_tolerated():
    """GPS scatter routinely pushes a genuine doorstep reading past the fence;
    the tolerance multiplier exists so that is not reported every time."""
    v = visit(dist=settings.GEO_FENCE_METRES * 1.2)
    assert SVC._far_from_customer([v]) == []
