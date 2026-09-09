"""The batch path must be the single-row path, only faster.

TWO DEFECTS FOUND 2026-09-09 BY COUNTING COLUMNS IN THE LIVE DATABASE, not by
reading code. `DecisionEngine.score_batch` returned bare probabilities, and the
production planner is its only caller, so everything the single-row `score()`
does around the probability was missing from every score the product served:

  * THE COVERAGE FLOOR WAS NOT APPLIED. `score()` declines below 60% of the
    model's own inputs; `score_batch` scored anyway. Measured across the 11,817
    served rows the day it was found, min coverage was 1.000 — nothing had been
    mis-served yet. That is the whole point: the guard was absent for the day it
    stops being 1.000, and 2026-09-08's fault #2 is exactly that day.
  * POINTS, BAND AND REASON CODES WERE NEVER COMPUTED. `ModelPrediction` has all
    three columns, `score_cases_and_log` assigns all three — from a result that
    never carried them. 11,817 rows: points NULL, band NULL, reason_codes [].

Neither raised. Both are the shape this repo keeps meeting: a plausible answer
with a guarantee quietly missing from it.

The load-bearing test here is `test_the_probability_the_allocator_gets_is_
unchanged`. Everything else is new behaviour; that one asserts the allocator's
input did NOT move, which is what makes the rest safe to ship.
"""
from __future__ import annotations

import pytest

from app.ml.pipeline.engine import MIN_FEATURE_COVERAGE, DecisionEngine
from app.services.ml_scoring_service import MLScoringService
from app.core.config import settings
from app.models.model_prediction import ModelPrediction

from tests.test_ml_scoring_adapter import (  # noqa: F401
    book, db, setup_db,
)


@pytest.fixture
def engine():
    e = DecisionEngine.get("recovery_risk")
    if e is None:
        pytest.skip("no champion artifact in this environment")
    return e


def _full(engine, **over):
    """A feature dict with every champion input supplied."""
    base = {"dpd": 62.0, "cibil_score": 640.0, "ptp_kept_ratio": 0.5,
            "overdue_amount": 24000.0}
    return {**{f: base.get(f, 1.0) for f in engine.selected}, **over}


# ---------------------------------------------------------------------------
# The number the allocator consumes must not have moved
# ---------------------------------------------------------------------------

def test_the_probability_the_allocator_gets_is_unchanged(engine):
    """`score_batch` is now a wrapper. Its output is what it always was."""
    rows = [_full(engine, dpd=d, cibil_score=c)
            for d, c in ((5, 780), (62, 640), (150, 520), (400, 300))]
    batch = engine.score_batch(rows)
    assert all(p is not None for p in batch)
    # The single-row path is the reference: same model, same calibrator.
    for r, p in zip(rows, batch):
        assert engine.score(r).probability == pytest.approx(p, abs=1e-6)
    # And it is still ordered the way a risk model must be.
    assert batch == sorted(batch)


def test_batch_and_single_row_agree_on_every_field_they_both_report(engine):
    rows = [_full(engine, dpd=d) for d in (10, 62, 200)]
    for r, det in zip(rows, engine.score_batch_detailed(rows)):
        one = engine.score(r)
        assert det.probability == pytest.approx(one.probability, abs=1e-6)
        assert det.points == one.points
        assert det.band == one.band
        assert det.feature_coverage == one.feature_coverage
        assert det.is_modelled == one.is_modelled
        assert [x["feature"] for x in det.reason_codes] == \
               [x["feature"] for x in one.reason_codes]


# ---------------------------------------------------------------------------
# The floor, on the path that actually serves
# ---------------------------------------------------------------------------

def test_the_coverage_floor_now_applies_to_the_batch_path(engine):
    """One input of four. `score()` has always declined this; `score_batch`
    returned a number for it, and the planner used that number."""
    sparse = {"dpd": 90.0}
    assert engine.score(sparse).probability is None          # always was
    assert engine.score_batch([sparse]) == [None]            # now too

    det = engine.score_batch_detailed([sparse])[0]
    assert det.is_modelled is False
    assert det.feature_coverage < MIN_FEATURE_COVERAGE
    assert "features were supplied" in det.fallback_reason
    assert set(det.missing_features) == set(engine.selected) - {"dpd"}


def test_one_sparse_row_does_not_poison_the_rows_beside_it(engine):
    """A batch is many borrowers. Declining one must not decline the others,
    and must not shift which result belongs to whom."""
    rows = [_full(engine, dpd=10), {"dpd": 90.0}, _full(engine, dpd=300)]
    out = engine.score_batch_detailed(rows)
    assert [r.is_modelled for r in out] == [True, False, True]
    assert out[0].probability < out[2].probability, "results were misaligned"
    assert out[0].probability == pytest.approx(
        engine.score(rows[0]).probability, abs=1e-6)


def test_a_thin_borrower_is_not_handed_to_the_allocator_as_a_number(db, book,
                                                                    monkeypatch):
    """End to end through the service the planner calls: when the adapter
    cannot supply the model's inputs, the case must be ABSENT from the returned
    probabilities, not present with a confident-looking one.

    The sparse vector is injected rather than built from rows, because on THIS
    schema it cannot be built from rows — see the test below, which is the
    finding that makes this one necessary rather than redundant.
    """
    monkeypatch.setattr(MLScoringService, "build_features",
                        lambda self, loan, as_of=None: {"dpd": 62.0})

    svc = MLScoringService(db)
    probs, rows = svc.score_cases_and_log([book["case"]])

    assert probs == {}, "a below-floor borrower reached the allocator"
    assert len(rows) == 1, "the decline was not recorded at all"
    assert rows[0].is_modelled is False
    assert rows[0].probability is None
    assert rows[0].feature_coverage < MIN_FEATURE_COVERAGE
    assert "floor" in (rows[0].fallback_reason or "")


# ---------------------------------------------------------------------------
# What a served row records
# ---------------------------------------------------------------------------

def test_a_served_prediction_records_what_the_score_was_made_of(db, book):
    """points, band and reason codes were NULL/[] on all 11,817 live rows."""
    svc = MLScoringService(db)
    probs, rows = svc.score_cases_and_log([book["case"]])
    assert probs and len(rows) == 1
    row = rows[0]

    assert row.points is not None and isinstance(row.points, int)
    assert row.band and len(row.band) <= 4
    assert row.reason_codes, "no reason codes on a served score"
    assert {"feature", "points"} <= set(row.reason_codes[0])
    assert all(r["feature"] in row.features for r in row.reason_codes), \
        "a reason code names a feature the row does not carry"


def test_the_band_and_the_probability_describe_the_same_borrower(db, book, engine):
    """points run the other way to probability — a worse borrower must score
    fewer points, or the two numbers on the row contradict each other."""
    worse = engine.score_batch_detailed([_full(engine, dpd=400, cibil_score=300)])[0]
    better = engine.score_batch_detailed([_full(engine, dpd=5, cibil_score=800)])[0]
    assert worse.probability > better.probability
    assert worse.points < better.points


def test_the_row_is_persistable_with_everything_on_it(db, book):
    """The columns exist and accept what we now put in them — a JSON column
    that rejects the reason-code shape would fail only at 20:00."""
    svc = MLScoringService(db)
    _, rows = svc.score_cases_and_log([book["case"]])
    db.commit()
    stored = db.query(ModelPrediction).filter(
        ModelPrediction.id == rows[0].id).one()
    assert stored.points and stored.band and stored.reason_codes
    assert stored.feature_coverage == 1.0


def test_logging_off_still_scores_and_still_declines(db, book, monkeypatch):
    """ML_LOG_PREDICTIONS governs the RECORD, never the decision."""
    monkeypatch.setattr(settings, "ML_LOG_PREDICTIONS", False)
    svc = MLScoringService(db)
    probs, rows = svc.score_cases_and_log([book["case"]])
    assert probs and rows == []

    monkeypatch.setattr(MLScoringService, "build_features",
                        lambda self, loan, as_of=None: {"dpd": 62.0})
    probs, rows = svc.score_cases_and_log([book["case"]])
    assert probs == {} and rows == []


def test_the_floor_cannot_currently_BITE_through_this_adapter(db, book):
    """MEASURED, AND RECORDED AS A FINDING RATHER THAN FIXED HERE.

    Three of the champion's four inputs cannot be missing from a real row:
    `overdue_amount` is NOT NULL in the schema, `dpd` falls back to 0.0, and
    `ptp_kept_ratio` falls back to 0.5 when a borrower has no PTP history. Only
    `cibil_score` is genuinely optional, so live coverage can be 1.00 or 0.75
    and nothing else — and 0.75 is above the 0.60 floor.

    Two things follow, and both are the point of writing it down:

      * the floor is a guard against a SCHEMA or ADAPTER change, not against
        thin borrowers. That is still worth having — 2026-09-08's fault #2 was
        an adapter change, and coverage fell to exactly 0.75.
      * `ptp_kept_ratio = 0.5` for a borrower with no promises is a DEFAULT
        WEARING AN OBSERVATION'S CLOTHES, and it counts toward coverage. That
        contradicts this repo's own rule that a factor with no evidence
        abstains. Changing it would move the score of every no-history
        borrower, so it is reported here and NOT changed in a pass whose remit
        is integration defects.
    """
    from app.models.ptp import PTP

    db.query(PTP).delete()
    book["customer"].cibil_score = None
    db.commit()

    feats = MLScoringService(db).build_features(book["loan"])
    engine = DecisionEngine.get("recovery_risk")
    if engine is None:
        pytest.skip("no champion artifact")
    coverage, missing = engine._coverage(feats)

    assert missing == ["cibil_score"], (
        f"another feature became genuinely optional: {missing}. If so this "
        f"comment is out of date and the floor may now bite for real.")
    assert coverage == 0.75
    assert coverage >= MIN_FEATURE_COVERAGE
    assert feats["ptp_kept_ratio"] == 0.5, "the default, with no PTP rows at all"


def test_a_missing_artifact_is_loud_not_merely_empty(db, book, monkeypatch, caplog):
    """The one degradation on this path that used to say nothing.

    No champion meant an empty dict; the planner fell back correctly and the
    only trace was `scored=0`, which also prints for an empty pool. That is the
    2026-09-08 pattern exactly — correct degradation hiding a real fault.
    """
    import logging

    from app.ml.pipeline import engine as engine_mod

    monkeypatch.setattr(engine_mod.DecisionEngine, "get",
                        classmethod(lambda cls, *a, **k: None))
    with caplog.at_level(logging.ERROR):
        probs, rows = MLScoringService(db).score_cases_and_log([book["case"]])

    assert (probs, rows) == ({}, [])
    assert any("no_champion_artifact" in r.message or
               "no champion" in r.message.lower() for r in caplog.records), \
        "the allocator lost its model and nothing was logged at error"
