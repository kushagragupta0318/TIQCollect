# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. Priority sorting and filtering on GET /manager/cases.
#
#   WHY THIS EXISTS. The visit-priority score was only visible after opening an
#   individual case, which made it understandable but not actionable: a manager
#   could not find the cases worth working without opening them one at a time.
#
#   WHY THESE TESTS GO THROUGH HTTP. What is being guaranteed is what a manager
#   SEES — the order, the filter, and above all that the list and the detail
#   agree about the same case. A service-level test would pass while the endpoint
#   quietly paginated before sorting.
#
#   THE DESIGN THESE PIN. The score is computed, not stored, so it cannot be an
#   ORDER BY. The endpoint scores the filtered set and paginates in Python rather
#   than persisting a column, because a nightly-refreshed column would let the
#   list show a value up to a day stale while the detail computed live. Equality
#   between the two screens is the requirement; in-request scoring makes it hold
#   by construction. test_the_list_and_the_detail_report_the_same_score is the
#   assertion that would fail if anyone reintroduced a stored copy.
#
#   Fixtures are reused from test_visit_priority_service rather than rebuilt —
#   two copies of a five-case book would drift, and the scrambled insertion order
#   there is load-bearing (see that file's header).
# ─────────────────────────────────────────────────────────────────────────────
"""GET /manager/cases: priority ordering, band filtering, list/detail equality."""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.case import Case, CaseStatus
from app.models.user import UserRole
from tests.test_visit_priority_service import TODAY, Session, _build, engine
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

ENDPOINT = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "api" / "v1" / "endpoints" / "manager.py")


@pytest.fixture
def client(request):
    """A manager client over the shared in-memory book.

    The book is rebuilt per test and every case assigned to one of the two
    fixture agents — the manager list only shows cases belonging to their team,
    so an unassigned fixture would return an empty page and every assertion
    below would pass vacuously.
    """
    drop_schema(engine)
    create_schema(engine)
    db = Session()
    _build(db)
    for i, case in enumerate(db.query(Case).order_by(Case.case_number).all()):
        case.agent_id = test_id(f"ag{i % 2}")
        case.allocation_date = TODAY
    db.commit()

    def _override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    c = TestClient(app)
    c.hdr = {                                       # type: ignore[attr-defined]
        "Authorization":
            f"Bearer {create_access_token(test_id('u-mgr'), UserRole.AGENCY_MANAGER.value, 'test-device')}"
    }
    c.db = db                                       # type: ignore[attr-defined]
    yield c
    app.dependency_overrides.clear()
    db.close()


def _rows(client, **params):
    r = client.get("/api/v1/manager/cases", params=params, headers=client.hdr)
    assert r.status_code == 200, r.text
    return r.json()["cases"]


# ── Sorting ─────────────────────────────────────────────────────────────────
def test_the_list_sorts_highest_priority_first(client):
    """What a manager asking "what should my team work first" must get."""
    rows = _rows(client, sort="priority_desc")
    scores = [r["visit_priority"]["score"] for r in rows]
    assert scores == sorted(scores, reverse=True), scores
    assert rows[0]["case_number"] == "CASE0000001"      # the big pre-NPA case


def test_the_list_can_sort_lowest_priority_first(client):
    """The inverse view, for auditing what is being left alone."""
    rows = _rows(client, sort="priority_asc")
    scores = [r["visit_priority"]["score"] for r in rows]
    assert scores == sorted(scores), scores


def test_sorting_is_applied_before_pagination(client):
    """The bug this guards: paginating first and sorting the page would give a
    plausible-looking order in which page 2's best case outranks page 1's worst.
    Page 1 followed by page 2 must equal the whole list, in order."""
    whole = [r["case_number"] for r in _rows(client, sort="priority_desc")]
    p1 = [r["case_number"] for r in _rows(client, sort="priority_desc", limit=2, offset=0)]
    p2 = [r["case_number"] for r in _rows(client, sort="priority_desc", limit=2, offset=2)]
    assert p1 + p2 == whole[:4], (p1, p2, whole)


# ── Filtering ───────────────────────────────────────────────────────────────
def test_each_band_can_be_filtered_and_the_bands_partition_the_set(client):
    """Every row must carry the band it was filtered by, and the three bands
    must cover the actionable set exactly once. A case in two bands — or in
    none — means the filter and the badge disagree about the same case."""
    seen, counted = set(), 0
    for band in ("HIGH", "MEDIUM", "LOW"):
        rows = _rows(client, priority_band=band)
        assert all(r["visit_priority"]["band"] == band for r in rows), band
        seen |= {r["case_number"] for r in rows}
        counted += len(rows)
    assert counted == len(seen), "a case appeared under more than one band"
    everything = {r["case_number"] for r in _rows(client, sort="priority_desc")}
    assert seen == everything


def test_the_band_filter_is_case_insensitive(client):
    """A hand-typed query string should not silently return an empty page."""
    assert _rows(client, priority_band="high") == _rows(client, priority_band="HIGH")


def test_an_unknown_band_returns_nothing_rather_than_everything(client):
    """Failing open on a filter is how a manager acts on the wrong list."""
    assert _rows(client, priority_band="URGENT") == []


# ── What belongs in the actionable view ─────────────────────────────────────
def test_resolved_cases_are_excluded_from_the_priority_view(client):
    """A settled case has no next visit, so it is not work to be prioritised.

    The highest scorer is the one marked PAID here, so ordering cannot hide the
    failure — if exclusion breaks, it appears at position one.
    """
    cases = client.db.query(Case).order_by(Case.case_number).all()
    cases[1].status = CaseStatus.PAID
    cases[2].status = CaseStatus.CLOSED
    client.db.commit()

    numbers = {r["case_number"] for r in _rows(client, sort="priority_desc")}
    assert "CASE0000001" not in numbers
    assert "CASE0000002" not in numbers


def test_resolved_cases_are_still_reachable_in_the_ordinary_list(client):
    """Excluded from the priority view is not the same as hidden. A manager must
    still be able to find a settled case."""
    cases = client.db.query(Case).order_by(Case.case_number).all()
    cases[1].status = CaseStatus.PAID
    client.db.commit()
    assert "CASE0000001" in {r["case_number"] for r in _rows(client)}


def test_escalated_cases_stay_in_the_priority_view(client):
    """ESCALATED is open, visitable, and the work a manager most wants
    surfaced. Grouping it with PAID would quietly bury the hardest cases."""
    client.db.query(Case).filter(Case.case_number == "CASE0000001").one().status = \
        CaseStatus.ESCALATED
    client.db.commit()
    assert "CASE0000001" in {r["case_number"] for r in _rows(client, sort="priority_desc")}


def test_the_total_reflects_the_filtered_set_not_the_whole_book(client):
    """Pagination controls read `total`. Reporting the unfiltered count would
    render page links to pages that come back empty."""
    r = client.get("/api/v1/manager/cases", params={"priority_band": "HIGH"},
                   headers=client.hdr).json()
    assert r["total"] == len(r["cases"])
    everything = client.get("/api/v1/manager/cases",
                            params={"sort": "priority_desc"}, headers=client.hdr).json()
    assert r["total"] <= everything["total"]


# ── The consistency guarantee ───────────────────────────────────────────────
def test_the_list_and_the_detail_report_the_same_score(client):
    """THE ONE THAT MATTERS.

    Two screens disagreeing about one case is the outcome this feature cannot
    afford, and it is exactly what a persisted column refreshed nightly would
    have produced. Score, band, reason AND the three components are compared,
    because a shared score with a divergent breakdown is still a contradiction.
    """
    for row in _rows(client, sort="priority_desc"):
        detail = client.get(f"/api/v1/manager/cases/{row['id']}",
                            headers=client.hdr).json()
        lst, det = row["visit_priority"], detail["visit_priority"]
        assert lst["score"] == det["score"], row["case_number"]
        assert lst["band"] == det["band"], row["case_number"]
        assert lst["reason"] == det["reason"], row["case_number"]
        assert ([(c["code"], c["points"]) for c in lst["components"]]
                == [(c["code"], c["points"]) for c in det["components"]])


def test_every_row_in_the_priority_view_carries_a_score(client):
    """Sorting by a field that is null on some rows puts them somewhere
    arbitrary. In this view the score is what the order MEANS, so its absence
    is not survivable."""
    for row in _rows(client, sort="priority_desc"):
        vp = row["visit_priority"]
        assert vp is not None, row["case_number"]
        assert vp["score"] is not None and vp["band"] and vp["reason"]
        assert len(vp["components"]) == 3


# ── The legacy path must be untouched ───────────────────────────────────────
def test_the_default_ordering_is_the_legacy_one(client):
    """A manager who does not ask for the priority view sees the page they
    already know: newest allocation_date first. The fallback is preserved, not
    merely still reachable."""
    rows = _rows(client)
    dates = [r["allocation_date"] for r in rows]
    assert dates == sorted(dates, reverse=True), dates
    assert len(rows) == client.db.query(Case).count()


def test_an_unrecognised_sort_falls_back_rather_than_erroring(client):
    """An old bookmark or a typo must not 500, and must not silently reorder."""
    assert ([r["case_number"] for r in _rows(client, sort="nonsense")]
            == [r["case_number"] for r in _rows(client)])


# ── One scoring definition ──────────────────────────────────────────────────
def test_the_endpoint_does_not_reimplement_the_scoring():
    """The endpoint may CALL the scorer and reuse its sort key. It may not carry
    a ladder, a weight, a band edge or a status list of its own — asserted on
    the source, because a second copy would pass every behavioural test above
    right up until the day the two drifted.
    """
    src = ENDPOINT.read_text(encoding="utf-8")
    assert "score_cases" in src
    assert "_vp_sort_key" in src
    for banned in (r"MAX_VALUE_POINTS", r"PRIORITY_BAND_EDGES", r"_VALUE_LADDER",
                   r"_URGENCY_LADDER", r"_EFFORT_PENALTIES",
                   r"def _band_for", r"BAND_HIGH\s*="):
        assert not re.search(banned, src), banned
    # The excluded-status list is imported from the service, not restated.
    assert "_RESOLVED_STATUSES as _VISIT_PRIORITY_EXCLUDED" in src


def test_the_two_orderings_share_one_payload_builder():
    """Both paths must return identical row shapes. Two copies of the payload
    block would diverge the first time one of them gained a field."""
    src = ENDPOINT.read_text(encoding="utf-8")
    assert src.count("def _cases_payload(") == 1
    # Call sites only — the definition line also matches the bare name.
    calls = re.findall(r"return _cases_payload\(", src)
    assert len(calls) == 2, calls                          # legacy + priority
