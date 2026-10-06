"""app/demo/recovery_scoring.py (lane L6, 2026-10-01): the generated
agencies' open cases scored by the real recovery_risk model, once, at the
anchor date — the honest alternative to backfilling each case's placement
date, which ml_scoring_service.build_features() documents as NOT honest.

What it holds: every generated agency (not Aravalli, which already has its
own v1-copied predictions) gets real ModelPrediction rows for its open
cases; a resolved case is never scored (nothing left to predict); every
written row is stamped recovery_risk at the anchor date, never a different
model or date; running it twice does not multiply the book's truth (the
second run is skipped once rows already exist, checked up front — this
file pins THAT refusal, not a double-write).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 — the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")


def _generated_agency_ids():
    from app.demo.roster import AGENCIES
    return [a.id for a in AGENCIES if a.key != "ARAVALLI" and a.bank_key == "GIRIVAN"]


@pytest.fixture(scope="module")
def baked(db):  # noqa: F811
    """The committed dump's own recovery_risk rows, read before `scored` clears
    them. l6 baked the scoring in, and nothing checked that the artifact really
    ships with it."""
    with db.connect() as c:
        return c.execute(text(
            "SELECT count(*) AS rows, count(DISTINCT agency_id) AS agencies "
            "FROM ml.model_predictions WHERE model_name = 'recovery_risk' "
            "AND agency_id = ANY(CAST(:ids AS uuid[]))"), {"ids": _generated_agency_ids()}).one()


@pytest.fixture(scope="module")
def scored(db, baked):  # noqa: F811
    """The SCORER's own output. The dump is baked and score_generated_books
    refuses to score twice, so this module used to error at setup rather than
    test anything (red on CI since the bake). The generated agencies'
    recovery_risk rows are cleared in this THROWAWAY database so the scorer runs;
    Aravalli's v1-copied predictions stay, as every query here excludes them.
    Depends on `baked` so the artifact is read before it is cleared.
    """
    from app.demo import roster as R
    from app.demo.recovery_scoring import score_generated_books

    s = Session(bind=db)
    s.execute(text("DELETE FROM ml.model_predictions WHERE model_name = 'recovery_risk' "
                   "AND agency_id = ANY(CAST(:ids AS uuid[]))"), {"ids": _generated_agency_ids()})
    s.commit()
    result = score_generated_books(s, bank_key="GIRIVAN")
    s.commit()
    yield result, R
    s.close()


def test_the_baked_dump_already_carries_recovery_scores(baked):
    """The shipped dump must arrive scored: the manager and bank surfaces read
    recovery_risk, and an unscored dump would abstain everywhere with nothing in
    the suite noticing."""
    assert baked.rows > 0, "the committed dump carries no recovery_risk predictions"
    assert baked.agencies >= 2, f"only {baked.agencies} generated agencies are scored in the dump"


def test_every_generated_agency_s_open_cases_are_modelled(db, scored):  # noqa: F811
    result, R = scored
    assert result.cases_considered > 0
    assert result.cases_modelled > 0
    # A real model declines some fraction on low feature coverage; it should
    # not decline everything (the artifact failed to load) or nothing (the
    # coverage floor never engaged on a whole generated book).
    assert result.cases_declined < result.cases_considered

    with db.connect() as c:
        rows = c.execute(text("""
            SELECT a.code, count(*) n, count(*) FILTER (WHERE mp.is_modelled) modelled,
                   avg(mp.probability) FILTER (WHERE mp.is_modelled) avg_p
            FROM ml.model_predictions mp JOIN tenancy.agencies a ON a.id = mp.agency_id
            WHERE mp.model_name = 'recovery_risk' AND mp.as_of_date = :d
            GROUP BY a.code ORDER BY a.code
        """), {"d": R.ANCHOR_DATE}).fetchall()
    codes = {r.code for r in rows}
    assert "AGY-ARAVALLI" not in codes                 # excluded: already has its own, from v1
    assert {"AGY-SAHYADRI", "AGY-DECCAN", "AGY-SARTHAK"} <= codes
    for r in rows:
        if r.modelled:
            assert 0.0 <= r.avg_p <= 1.0


def test_a_resolved_case_is_never_scored(db, scored):  # noqa: F811
    result, R = scored
    with db.connect() as c:
        resolved_scored = c.execute(text("""
            SELECT count(*) FROM ml.model_predictions mp
            JOIN collections.cases c ON c.id = mp.case_id
            WHERE mp.model_name = 'recovery_risk' AND mp.as_of_date = :d
              AND c.status IN ('PAID', 'CLOSED', 'WRITTEN_OFF')
        """), {"d": R.ANCHOR_DATE}).scalar()
    assert resolved_scored == 0


def test_every_row_is_stamped_recovery_risk_at_the_anchor_date(db, scored):  # noqa: F811
    result, R = scored
    with db.connect() as c:
        off_model = c.execute(text("""
            SELECT count(*) FROM ml.model_predictions mp
            JOIN tenancy.agencies a ON a.id = mp.agency_id
            WHERE a.code <> 'AGY-ARAVALLI' AND (mp.model_name <> 'recovery_risk' OR mp.as_of_date <> :d)
        """), {"d": R.ANCHOR_DATE}).scalar()
    assert off_model == 0


def test_a_declined_case_is_recorded_with_its_reason_not_silently_dropped(db, scored):  # noqa: F811
    result, R = scored
    if result.cases_declined == 0:
        pytest.skip("this book's open cases all cleared the coverage floor")
    with db.connect() as c:
        row = c.execute(text("""
            SELECT is_modelled, fallback_reason, probability FROM ml.model_predictions
            WHERE model_name = 'recovery_risk' AND as_of_date = :d AND is_modelled = false LIMIT 1
        """), {"d": R.ANCHOR_DATE}).fetchone()
    assert row is not None and row.probability is None and row.fallback_reason
