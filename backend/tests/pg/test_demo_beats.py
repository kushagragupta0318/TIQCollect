"""app/demo/beats.py (lane L6, 2026-10-01): one planning.beats row per
(agent, day) with real visits or approved leave, for every generated agency.

What it holds: every agent-day with visits gets a beat whose actual
distance/duration come from real check-in GPS and timestamps, and whose
planned distance/duration come from a nearest-neighbour ordering of the
SAME cases (never a different case set — see the module's own docstring on
why this measures route efficiency, not plan completion); a leave day with
no visits gets a CANCELLED, is_leave_day beat; a day with neither carries no
beat at all; calling it twice refuses rather than duplicating.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 — the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")


def _generated_agency_ids():
    """The agencies generate_beats builds for: every one of GIRIVAN's except
    Aravalli, whose beats came from v1 with their own lifecycle."""
    from app.demo.roster import AGENCIES
    return [a.id for a in AGENCIES if a.key != "ARAVALLI" and a.bank_key == "GIRIVAN"]


@pytest.fixture(scope="module")
def baked(db):  # noqa: F811
    """What the COMMITTED DUMP already carries, read before `built` clears it.

    l6 baked the generated beats into the dump, so the restored fixture arrives
    with them. That is a real property of the artifact we ship, and nothing
    checked it; test_the_baked_dump_already_carries_beats does, from here.
    """
    with db.connect() as c:
        return c.execute(text(
            "SELECT count(*) AS beats, count(DISTINCT agency_id) AS agencies, "
            "       count(*) FILTER (WHERE NOT is_leave_day AND estimated_distance_km IS NULL) AS unplanned, "
            "       count(*) FILTER (WHERE beat_number IS NULL OR beat_number = '') AS unnumbered "
            "FROM planning.beats WHERE agency_id = ANY(CAST(:ids AS uuid[]))"), {"ids": _generated_agency_ids()}).one()


@pytest.fixture(scope="module")
def built(db, baked):  # noqa: F811
    """The GENERATOR's own output, which is what the rest of this module tests.

    The dump is baked, and generate_beats refuses to build twice, so every test
    here used to error at setup instead of testing anything (red on CI since the
    bake). Clearing the generated agencies' beats in this THROWAWAY database
    restores what the module was written to prove. Aravalli's rows stay — every
    query below excludes them anyway — and nothing references beats by foreign
    key, so the delete is local and complete. (leave_requests.beat_ids is a JSON
    list, not an FK; stale ids there are harmless in a database we drop.)
    It depends on `baked` so the artifact is read BEFORE it is cleared.
    """
    from app.demo.beats import generate_beats

    with db.begin() as conn:
        conn.execute(text("DELETE FROM planning.beats WHERE agency_id = ANY(CAST(:ids AS uuid[]))"),
                     {"ids": _generated_agency_ids()})
    with db.begin() as conn:
        result = generate_beats(conn, bank_key="GIRIVAN")
    return result


def test_the_baked_dump_already_carries_beats(baked):
    """The shipped dump must arrive with the generated book's beats: the demo
    opens on today's routes, and a dump that lost them would look empty on the
    agent app with nothing in the suite complaining."""
    assert baked.beats > 0, "the committed dump carries no generated beats"
    assert baked.agencies >= 2, f"only {baked.agencies} generated agencies have beats in the dump"
    # Every working day carries a planned distance -- that is what makes the
    # beat a measurement of route efficiency rather than a bare list of cases.
    assert baked.unplanned == 0, f"{baked.unplanned} baked working beats have no estimated distance"
    assert baked.unnumbered == 0, f"{baked.unnumbered} baked beats have no beat number"
    # NOT asserted: route_geometry. The baked book is HISTORICAL, reconstructed
    # from past visits, and only 339 of its 25,590 beats carry a polyline --
    # geometry comes from planning a day, not from having worked one. Today's
    # freshly generated beats do all carry it, which is a different population;
    # conflating the two is a mistake this comment exists to stop repeating.


def test_every_generated_agency_has_beats(db, built):  # noqa: F811
    """Aravalli has its OWN beats already, copied from v1
    (migrate_v1_to_v2.py: T.copy("beats")) — a different lifecycle, a
    different beat_number convention, nothing this generator touches. Every
    query here that could see them explicitly excludes AGY-ARAVALLI, the
    same discipline this lane has needed for fraud_reviews,
    allocation_decisions and model_predictions."""
    from app.demo.roster import AGENCIES

    assert built.agent_days > 0
    with db.connect() as c:
        rows = c.execute(text("""
            SELECT a.code, count(*) n, count(*) FILTER (WHERE b.is_leave_day) leave_n,
                   count(*) FILTER (WHERE NOT b.is_leave_day) visit_n
            FROM planning.beats b JOIN tenancy.agencies a ON a.id = b.agency_id
            WHERE a.code <> 'AGY-ARAVALLI'
            GROUP BY a.code ORDER BY a.code
        """)).fetchall()
    codes = {r.code for r in rows}
    for agency in AGENCIES:
        if agency.key in ("ARAVALLI", "HOOGHLY") or agency.bank_key != "GIRIVAN":
            continue
        assert f"AGY-{agency.key}" in codes
    awadh = next(r for r in rows if r.code == "AGY-AWADH")
    assert awadh.visit_n > 0


def test_a_visit_day_s_beat_matches_the_real_cases_and_is_completed(db, built):  # noqa: F811
    with db.connect() as c:
        row = c.execute(text("""
            SELECT b.total_cases, b.cases_completed, b.status, b.estimated_distance_km,
                   b.actual_distance_km, b.estimated_duration_minutes, b.actual_duration_minutes,
                   b.route_source,
                   (SELECT count(*) FROM collections.visits v
                    WHERE v.agent_id = b.agent_id
                      AND (v.check_in_time AT TIME ZONE 'Asia/Kolkata')::date = b.beat_date) real_visits
            FROM planning.beats b JOIN tenancy.agencies a ON a.id = b.agency_id
            WHERE a.code <> 'AGY-ARAVALLI' AND NOT b.is_leave_day LIMIT 1
        """)).fetchone()
    assert row is not None
    assert row.status == "COMPLETED" and row.total_cases == row.cases_completed == row.real_visits
    assert row.estimated_distance_km >= 0 and row.actual_distance_km >= 0
    assert row.estimated_duration_minutes >= 0 and row.actual_duration_minutes >= 0
    assert row.route_source == "haversine"


def test_a_leave_day_beat_is_cancelled_and_empty(db, built):  # noqa: F811
    with db.connect() as c:
        row = c.execute(text("""
            SELECT b.status, b.total_cases, b.cases_completed, b.estimated_distance_km,
                   b.actual_distance_km, b.leave_type
            FROM planning.beats b JOIN tenancy.agencies a ON a.id = b.agency_id
            WHERE a.code <> 'AGY-ARAVALLI' AND b.is_leave_day LIMIT 1
        """)).fetchone()
    if row is None:
        pytest.skip("this book's approved leave never missed a visit-day entirely")
    assert row.status == "CANCELLED" and row.total_cases == 0 and row.cases_completed == 0
    assert row.estimated_distance_km == 0 and row.actual_distance_km == 0
    assert row.leave_type is not None


def test_beat_number_matches_the_real_planner_s_format(db, built):  # noqa: F811
    with db.connect() as c:
        mismatched = c.execute(text("""
            SELECT count(*) FROM planning.beats b
            JOIN workforce.agents g ON g.id = b.agent_id
            JOIN tenancy.agencies a ON a.id = b.agency_id
            WHERE a.code <> 'AGY-ARAVALLI'
              AND b.beat_number <> 'BEAT-' || to_char(b.beat_date, 'YYYYMMDD') || '-' || g.employee_code
        """)).scalar()
    assert mismatched == 0


def test_a_second_call_refuses_rather_than_duplicates(db, built):  # noqa: F811
    from app.demo.beats import generate_beats

    with db.begin() as conn:
        with pytest.raises(RuntimeError, match="refusing to build twice"):
            generate_beats(conn, bank_key="GIRIVAN")
