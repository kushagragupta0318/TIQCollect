"""The production monitoring pipeline, run end to end on controlled data.

WHAT THIS DOES AND DOES NOT CLAIM.

  (A) It proves the PRODUCTION PATH is executable and wired correctly:
      label attachment -> readiness -> deduplication -> metrics -> retrain
      decision, on the serving version and the current outcome definition, with
      the nightly task as the entry point.

  (B) It proves NOTHING about real predictive performance. Every outcome here
      is written by the test. The live database has n_matured = 0 and the first
      real cohort cannot mature before 2026-10-08. A number computed from
      fabricated labels is not evidence about borrowers and must never be
      reported as though it were.

The distinction is the point. A pipeline that cannot run is a defect that can be
fixed today; a pipeline with nothing to run on is a fact about the calendar.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.pipeline import monitor as mon
from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION, OutcomeStatus
from app.models.base import Base
from app.models.model_prediction import ModelPrediction

# 2026-09-16 — the served version and its feature set come from the pointer
# (tests/_served_champion.py); a literal "1.1.0" described nothing once 2.2.0
# was promoted and the whole chain correctly reported not_ready.
from tests._served_champion import served_vector, serving_version  # noqa: E402
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

SERVING = serving_version()
engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    create_schema(bind=engine)
    yield
    drop_schema(bind=engine)


@pytest.fixture
def db():
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _pred(db, *, case, as_of, prob, outcome=None, version=SERVING,
          odv=OUTCOME_DEFINITION_VERSION, coverage=1.0):
    row = ModelPrediction(
        id=str(uuid.uuid4()), model_name="recovery_risk", model_version=version,
        entity_type="case", entity_id=case, case_id=case, as_of_date=as_of,
        probability=prob, is_modelled=True, feature_coverage=coverage,
        features=served_vector(int(prob * 100), dpd=40.0 + (prob * 100), cibil_score=600.0,
                               ptp_kept_ratio=0.5, overdue_amount=5000.0),
        outcome_baseline={"overdue_amount": 5000.0, "emi_amount": 2500.0,
                          "threshold_ratio": 0.8},
    )
    if outcome is not None:
        row.actual_outcome = outcome
        row.outcome_status = (OutcomeStatus.NOT_RECOVERED.value if outcome
                              else OutcomeStatus.RECOVERED.value)
        row.outcome_definition_version = odv
    db.add(row)
    return row


def _cohort(db, n=700, *, as_of=None, version=SERVING, separable=True):
    """A matured cohort big enough to clear MIN_ROWS_FOR_PERFORMANCE.

    `separable=True` is CALIBRATED, not merely well-ordered: within each
    probability level, exactly that share of rows is a bad. An earlier version
    used a step function (`y = 1 if p >= 0.30`) which ranks perfectly and is
    badly calibrated — mean predicted 0.495 against a realised bad rate of 0.70
    — and the calibration trigger added on 2026-09-09 correctly fired on it.
    The threshold was right and the fixture was wrong: a healthy model is one
    whose probabilities MEAN something, and a "healthy" fixture that a
    calibration check rejects was never healthy.
    """
    as_of = as_of or date.today() - timedelta(days=40)
    if not separable:
        for i in range(n):
            _pred(db, case=f"c{i}", as_of=as_of, prob=(i % 100) / 100.0,
                  outcome=i % 2, version=version)
        db.commit()
        return
    # Deterministic and calibrated: of the k rows at probability p, exactly
    # round(p*k) are bad.
    buckets: dict[float, list[int]] = {}
    for i in range(n):
        buckets.setdefault((i % 100) / 100.0, []).append(i)
    for p, idx in buckets.items():
        n_bad = int(round(p * len(idx)))
        for j, i in enumerate(idx):
            _pred(db, case=f"c{i}", as_of=as_of, prob=p,
                  outcome=1 if j < n_bad else 0, version=version)
    db.commit()


# ---------------------------------------------------------------------------
# (A) The production path runs, stage by stage
# ---------------------------------------------------------------------------

def test_readiness_gates_before_anything_expensive_happens(db):
    """Below the threshold the monitor must not be reached at all."""
    import app.workers.tasks.model_outcomes as task_mod

    for i in range(10):
        _pred(db, case=f"c{i}", as_of=date.today() - timedelta(days=40),
              prob=0.5, outcome=1)
    db.commit()

    calls = []
    orig = mon.monitor_model
    mon.monitor_model = lambda *a, **k: calls.append(1)
    try:
        digest = task_mod._monitoring_digest(db, "recovery_risk")
    finally:
        mon.monitor_model = orig
    assert calls == [], "monitor_model ran below the readiness threshold"
    assert digest["status"] == "not_ready"
    assert digest["n_matured"] == 10
    assert digest["required"] == mon.MIN_MATURED_FOR_MONITORING


def test_the_whole_chain_runs_once_a_cohort_matures(db):
    """label -> readiness -> dedup -> metrics -> verdict, via the nightly task."""
    import app.workers.tasks.model_outcomes as task_mod

    _cohort(db)
    digest = task_mod._monitoring_digest(db, "recovery_risk")

    assert digest["status"] == "ready"
    assert digest["n_matured"] == 700
    assert digest["version"] == SERVING
    assert digest["outcome_definition_version"] == OUTCOME_DEFINITION_VERSION
    perf = digest["performance"]
    for k in ("n", "auc_live", "gini_live", "ks_live", "brier_live",
              "calibration_gap", "bad_rate_live", "recovery_rate_live"):
        assert k in perf, f"metric {k} never computed"
    assert digest["verdict"] in {"healthy", "retrain_recommended"}
    # Derived quantities must cohere, or the metrics block is decorative.
    # Both are rounded to 4dp for display, so the identity holds to within
    # half a rounding unit — still enough to catch the two describing
    # different things, which is what this line is for.
    assert perf["auc_live"] == pytest.approx((perf["gini_live"] + 1) / 2,
                                             abs=1e-4)
    assert 0.0 <= perf["brier_live"] <= 1.0


def test_a_healthy_cohort_does_not_recommend_a_retrain(db):
    _cohort(db, separable=True)
    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.performance, "no metrics computed"
    assert rep.retrain_recommended is False, rep.reasons


def test_a_collapsed_model_does_recommend_a_retrain(db):
    """Labels uncorrelated with the score: Gini ~0, far past the relative-drop
    trigger. The threshold is NOT touched — the data is."""
    _cohort(db, separable=False)
    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.retrain_recommended is True
    assert rep.verdict == "retrain_recommended"
    assert any("Gini" in r or "rank order" in r for r in rep.reasons), rep.reasons


# ---------------------------------------------------------------------------
# Version isolation, and the version that is actually SERVED
# ---------------------------------------------------------------------------

def test_the_monitor_defaults_to_the_version_actually_being_served(db):
    """`version=None` must mean the serving artifact, not "pool everything"."""
    from app.ml.pipeline.engine import DecisionEngine

    serving = mon.serving_version("recovery_risk")
    if serving is None:
        pytest.skip("no champion artifact in this environment")
    assert DecisionEngine.get("recovery_risk").version == serving

    _cohort(db, version=serving)
    _cohort(db, n=200, version="0.9.0-old",
            as_of=date.today() - timedelta(days=41))
    rep = mon.monitor_model(db, "recovery_risk", lookback_days=10_000)
    assert rep.version == serving
    assert rep.n_matured == 700
    assert rep.excluded["other_model_version"] == 200


def test_a_foreign_outcome_definition_never_reaches_the_metrics(db):
    _cohort(db)
    for i in range(300):
        _pred(db, case=f"other{i}", as_of=date.today() - timedelta(days=40),
              prob=0.5, outcome=1, odv="some-other-rule-2.0.0")
    db.commit()

    gate = mon.readiness(db, "recovery_risk", version=SERVING)
    assert gate.n_matured == 700
    assert gate.n_excluded_other_outcome_version == 300
    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.n_matured == 700
    assert rep.excluded["other_outcome_definition"] == 300


# ---------------------------------------------------------------------------
# Duplicate planning runs cannot inflate anything
# ---------------------------------------------------------------------------

def test_repeated_planning_cannot_walk_the_monitor_over_its_threshold(db):
    """The failure this guards: a manager clicking Re-Plan enough times makes
    the monitor 'ready' with no new borrowers at all."""
    as_of = date.today() - timedelta(days=40)
    for i in range(80):                       # 80 accounts...
        for _ in range(9):                    # ...re-scored nine times each
            _pred(db, case=f"c{i}", as_of=as_of, prob=0.5, outcome=1)
    db.commit()

    assert db.query(ModelPrediction).count() == 720   # over the 500 threshold
    gate = mon.readiness(db, "recovery_risk", version=SERVING)
    assert gate.n_matured == 80
    assert gate.ready is False, (
        "720 rows from 80 accounts opened the gate — re-planning inflates it")


def test_metrics_are_computed_on_one_row_per_account_day(db):
    as_of = date.today() - timedelta(days=40)
    for i in range(600):
        p = (i % 100) / 100.0
        y = 1 if p >= 0.30 else 0
        _pred(db, case=f"c{i}", as_of=as_of, prob=p, outcome=y)
        _pred(db, case=f"c{i}", as_of=as_of, prob=p, outcome=y)   # re-score
    db.commit()

    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert db.query(ModelPrediction).count() == 1200
    assert rep.n_predictions == 600
    assert rep.n_matured == 600
    assert rep.excluded["duplicate_rescores"] == 600


# ---------------------------------------------------------------------------
# Containment: monitoring must never cost a label
# ---------------------------------------------------------------------------

def test_a_broken_monitor_cannot_block_outcome_attachment(db, monkeypatch):
    """Labels are committed before the monitor runs. A read-only diagnostic
    that raises must not turn a correct labelling run into a failed one."""
    import app.workers.tasks.model_outcomes as task_mod

    rows = [_pred(db, case=f"c{i}", as_of=date.today() - timedelta(days=40),
                  prob=0.5, outcome=1) for i in range(5)]
    db.commit()
    ids = [r.id for r in rows]

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(task_mod, "_comparison_digest", lambda *a, **k: {})
    monkeypatch.setattr(task_mod, "_monitoring_digest",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("monitor exploded")))

    result = task_mod.attach_model_outcomes.apply(args=("recovery_risk",)).get()

    assert result["monitoring"] == {"status": "error", "error": "RuntimeError"}
    for key in ("model", "definition_version", "horizon_days", "due", "labelled"):
        assert key in result, "labelling did not complete"

    fresh = Session()
    try:
        for pid in ids:
            row = fresh.query(ModelPrediction).filter_by(id=pid).one()
            assert row.actual_outcome == 1
            assert row.outcome_definition_version == OUTCOME_DEFINITION_VERSION
    finally:
        fresh.close()


# ---------------------------------------------------------------------------
# The first REAL cohort enters monitoring with nobody pressing anything
# ---------------------------------------------------------------------------

def test_the_nightly_task_is_scheduled_and_registered():
    """Wiring, not behaviour: a task that beat schedules but the worker never
    imported raises "Received unregistered task" at 19:15 and nowhere else."""
    from app.workers.celery_app import celery_app

    name = "app.workers.tasks.model_outcomes.attach_model_outcomes"
    assert "app.workers.tasks.model_outcomes" in celery_app.conf.include
    entry = next(v for v in celery_app.conf.beat_schedule.values()
                 if v["task"] == name)
    assert entry["schedule"].hour == {19}
    assert entry["schedule"].minute == {15}


def test_maturity_alone_admits_a_cohort_with_no_manual_step(db):
    """The whole automatic path: rows that are merely too young are invisible,
    and become visible purely because the horizon elapses."""
    import app.workers.tasks.model_outcomes as task_mod

    # Written today, horizon not elapsed -> not matured, so not counted.
    for i in range(700):
        _pred(db, case=f"c{i}", as_of=date.today(), prob=(i % 100) / 100.0)
    db.commit()
    assert mon.readiness(db, "recovery_risk", version=SERVING).n_matured == 0
    assert task_mod._monitoring_digest(db, "recovery_risk")["status"] == "not_ready"

    # The labeller fills them in once the horizon has passed. Nothing else
    # changes: no flag, no manual trigger, no redeploy.
    for row in db.query(ModelPrediction).all():
        row.actual_outcome = 1 if (row.probability or 0) >= 0.30 else 0
        row.outcome_status = (OutcomeStatus.NOT_RECOVERED.value
                              if row.actual_outcome else OutcomeStatus.RECOVERED.value)
        row.outcome_definition_version = OUTCOME_DEFINITION_VERSION
    db.commit()

    gate = mon.readiness(db, "recovery_risk", version=SERVING)
    assert gate.ready is True
    assert task_mod._monitoring_digest(db, "recovery_risk")["status"] == "ready"


def test_monitoring_is_read_only_and_cannot_alter_a_single_label(db):
    """The monitor reads the labels the labeller wrote. If it could write one,
    every metric it reports would be partly a measurement of itself."""
    import app.workers.tasks.model_outcomes as task_mod

    _cohort(db)
    before = {r.id: (r.actual_outcome, r.outcome_status, r.probability,
                     r.outcome_definition_version, r.model_version)
              for r in db.query(ModelPrediction).all()}

    digest = task_mod._monitoring_digest(db, "recovery_risk")
    assert digest["status"] == "ready"
    db.expire_all()

    after = {r.id: (r.actual_outcome, r.outcome_status, r.probability,
                    r.outcome_definition_version, r.model_version)
             for r in db.query(ModelPrediction).all()}
    assert after == before, "the monitor wrote to the rows it was measuring"


def test_a_declined_score_cannot_open_the_gate_it_will_be_dropped_from(db):
    """The gate must count exactly what the monitor will measure.

    A borrower whose features fall below the coverage floor is recorded as a
    DECLINED prediction — probability NULL, is_modelled False — which is the
    right row to keep and the wrong row to count. `_load` drops it; if
    `readiness` did not, the gate would open at 500 and the Gini would be
    computed on fewer observations than the gate promised, which is the one
    thing the 500 was chosen to guarantee.
    """
    as_of = date.today() - timedelta(days=40)
    for i in range(600):
        row = _pred(db, case=f"declined{i}", as_of=as_of, prob=0.5, outcome=1)
        row.probability = None
        row.is_modelled = False
        row.fallback_reason = "only 25% of the model's 4 features were supplied"
    db.commit()

    assert db.query(ModelPrediction).count() == 600
    gate = mon.readiness(db, "recovery_risk", version=SERVING)
    assert gate.n_matured == 0, "declined scores counted toward the gate"
    assert gate.ready is False

    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.n_matured == 0
    assert rep.performance == {}


# ---------------------------------------------------------------------------
# Enough rows, one class — which is not the same thing as too few rows
# ---------------------------------------------------------------------------

def test_a_single_class_cohort_says_so_instead_of_asking_for_more_rows(db):
    """2026-09-09. With 500+ matured rows carrying ONE class, the report used to
    fall into the too-few-rows branch and print "only 1884 matured outcomes;
    performance needs 500" — self-contradictory, and the line that would have
    appeared on 2026-10-08: the demo book holds no payments after 2026-09-08, so
    every matured row would have labelled NOT_RECOVERED.

    Discrimination is undefined without both classes. That is a fact about the
    COHORT, not a shortfall in it, and telling a reader to wait for rows that
    have already arrived is worse than saying nothing.
    """
    as_of = date.today() - timedelta(days=40)
    for i in range(700):
        _pred(db, case=f"c{i}", as_of=as_of, prob=(i % 100) / 100.0, outcome=1)
    db.commit()

    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)

    assert rep.n_matured == 700
    assert rep.verdict == "insufficient_outcome_variation"
    assert rep.performance == {}, "a Gini was reported on one class"
    assert rep.outcome_variation == {
        "n_matured": 700, "n_classes": 1, "bad_rate_live": 1.0,
        "required_rows": mon.MIN_ROWS_FOR_PERFORMANCE,
    }
    joined = " ".join(rep.reasons)
    assert "ONE class" in joined and "every outcome is 'not recovered'" in joined
    assert "only 700 matured outcomes" not in joined, (
        "the row-shortfall message fired on a cohort that has enough rows")
    assert rep.retrain_recommended is False, "one class is not evidence of decay"


def test_the_all_recovered_case_reads_the_other_way_round(db):
    as_of = date.today() - timedelta(days=40)
    for i in range(600):
        _pred(db, case=f"c{i}", as_of=as_of, prob=(i % 100) / 100.0, outcome=0)
    db.commit()

    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.verdict == "insufficient_outcome_variation"
    assert rep.outcome_variation["bad_rate_live"] == 0.0
    assert "every outcome is 'recovered'" in " ".join(rep.reasons)


def test_too_few_rows_still_says_too_few_rows(db):
    """The existing behaviour, unchanged: below the threshold the message is
    about the row count, and the gate itself is untouched."""
    as_of = date.today() - timedelta(days=40)
    for i in range(120):
        _pred(db, case=f"c{i}", as_of=as_of, prob=(i % 100) / 100.0,
              outcome=1 if i % 2 else 0)
    db.commit()

    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.n_matured == 120
    assert rep.verdict == "insufficient_data"
    assert rep.performance == {} and rep.outcome_variation == {}
    assert "only 120 matured outcomes" in " ".join(rep.reasons)
    assert mon.MIN_ROWS_FOR_PERFORMANCE == 500, "the gate moved"


def test_both_classes_and_enough_rows_still_computes_the_metrics(db):
    """The normal path must be unaffected by the branch added beside it."""
    _cohort(db, separable=True)
    rep = mon.monitor_model(db, "recovery_risk", version=SERVING,
                            lookback_days=10_000)
    assert rep.verdict == "healthy"
    assert rep.outcome_variation == {}
    assert rep.performance["gini_live"] and rep.performance["ks_live"]
    assert rep.performance["n"] == 700


def test_the_nightly_task_carries_the_distinction_through(db):
    """The digest is what a person actually reads at 19:15."""
    import app.workers.tasks.model_outcomes as task_mod

    as_of = date.today() - timedelta(days=40)
    for i in range(700):
        _pred(db, case=f"c{i}", as_of=as_of, prob=(i % 100) / 100.0, outcome=1)
    db.commit()

    digest = task_mod._monitoring_digest(db, "recovery_risk")
    assert digest["status"] == "ready", "readiness is about rows, and they exist"
    assert digest["verdict"] == "insufficient_outcome_variation"
    assert digest["outcome_variation"]["n_classes"] == 1
    assert digest["performance"] == {}
