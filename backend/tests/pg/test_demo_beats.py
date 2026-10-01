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


@pytest.fixture(scope="module")
def built(db):  # noqa: F811
    from app.demo.beats import generate_beats

    with db.begin() as conn:
        result = generate_beats(conn, bank_key="GIRIVAN")
    return result


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
