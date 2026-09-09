"""The monitoring gate: when it runs, when it refuses, and what it refuses to mix.

Every test executes the gate or the monitor against rows in a database. None
inspects source text.

THE THING BEING PROTECTED is that a verdict is never produced from a population
that cannot support one. Two ways that happens, and both are covered here: too
few matured outcomes, and enough outcomes only because rows were borrowed from
the wrong model version or the wrong labelling rule.
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

MODEL = "recovery_risk"
SERVING = "1.1.0"
OTHER_VERSION = "1.0.0"
OTHER_RULE = "some-other-outcome-2.0.0"

test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _alternating(i):
    """A separable signal, so Gini is well defined rather than degenerate."""
    return 1 if (i % 100) >= 30 else 0


def _preds(db, n, *, outcome=1, version=SERVING, rule=OUTCOME_DEFINITION_VERSION,
           day_span=60):
    """n predictions. `outcome=None` leaves them unmatured."""
    today = date.today()
    rows = []
    for i in range(n):
        if outcome is None:
            y = None
        elif callable(outcome):
            y = outcome(i)
        else:
            y = outcome
        rows.append(ModelPrediction(
            id=str(uuid.uuid4()), model_name=MODEL, model_version=version,
            entity_type="case", entity_id=str(uuid.uuid4()),
            as_of_date=today - timedelta(days=day_span - (i % day_span)),
            probability=(i % 100) / 100.0,
            is_modelled=True,
            features={"dpd": float(i % 180), "cibil_score": 300.0 + (i % 500),
                      "ptp_kept_ratio": (i % 10) / 10.0,
                      "overdue_amount": 1000.0 * (1 + i % 20)},
            actual_outcome=y,
            outcome_definition_version=(None if y is None else rule),
            outcome_status=(None if y is None
                            else OutcomeStatus.NOT_RECOVERED.value),
        ))
    db.add_all(rows)
    db.commit()
    return rows


# ---------------------------------------------------------------------------
# Below threshold -> the monitor does not run
# ---------------------------------------------------------------------------

def test_below_threshold_the_gate_refuses(db):
    _preds(db, 10, outcome=_alternating)
    gate = mon.readiness(db, MODEL, version=SERVING)
    assert gate.ready is False
    assert gate.status == "not_ready"
    assert gate.n_matured == 10
    assert gate.required == mon.MIN_MATURED_FOR_MONITORING


def test_below_threshold_the_task_does_not_run_the_monitor(db, monkeypatch):
    """`not_ready` must be reported WITHOUT calling monitor_model at all — the
    gate exists to avoid the work, not merely to suppress the verdict."""
    import app.workers.tasks.model_outcomes as task_mod

    calls = []
    monkeypatch.setattr(mon, "monitor_model", lambda *a, **k: calls.append(1))
    _preds(db, 10, outcome=_alternating)

    digest = task_mod._monitoring_digest(db, MODEL)
    assert calls == [], "monitor_model ran below the threshold"
    assert digest["status"] == "not_ready"
    assert digest["n_matured"] == 10
    assert digest["required"] == mon.MIN_MATURED_FOR_MONITORING


def test_not_ready_carries_both_counts_so_the_log_line_is_actionable(db):
    _preds(db, 7, outcome=_alternating)
    d = mon.readiness(db, MODEL, version=SERVING).to_dict()
    assert d["n_matured"] == 7 and d["required"] == 500
    assert d["model_version"] == SERVING
    assert d["outcome_definition_version"] == OUTCOME_DEFINITION_VERSION


def test_unmatured_rows_never_count_toward_the_gate(db):
    """NULL means 'not yet known', never 0. Counting unmatured rows would open
    the gate on a population with nothing in it to measure."""
    _preds(db, 600, outcome=None)
    gate = mon.readiness(db, MODEL, version=SERVING)
    assert gate.n_matured == 0
    assert gate.ready is False


# ---------------------------------------------------------------------------
# Threshold reached -> it runs
# ---------------------------------------------------------------------------

def test_threshold_reached_opens_the_gate(db):
    _preds(db, mon.MIN_MATURED_FOR_MONITORING, outcome=_alternating)
    gate = mon.readiness(db, MODEL, version=SERVING)
    assert gate.ready is True and gate.status == "ready"


def test_threshold_reached_the_task_runs_the_monitor(db):
    import app.workers.tasks.model_outcomes as task_mod

    _preds(db, 520, outcome=_alternating)
    digest = task_mod._monitoring_digest(db, MODEL)

    assert digest["status"] == "ready"
    assert digest["n_matured"] == 520
    assert digest["verdict"] in {"healthy", "retrain_recommended"}


def test_the_task_digest_drops_the_heavy_tables(db):
    """The return value goes to the Celery result backend and a log line.
    Decile and calibration tables belong in a report a person reads."""
    import app.workers.tasks.model_outcomes as task_mod

    _preds(db, 520, outcome=_alternating)
    digest = task_mod._monitoring_digest(db, MODEL)
    assert "decile_table" not in digest
    assert "calibration_table" not in digest
    assert digest["performance"]["gini_live"] is not None


def test_the_report_carries_every_field_the_review_needs(db):
    _preds(db, 600, outcome=_alternating)
    rep = mon.monitor_model(db, MODEL, version=SERVING)

    perf = rep.performance
    assert rep.n_matured == 600
    for key in ("n", "auc_live", "gini_live", "ks_live", "brier_live",
                "calibration_gap", "bad_rate_live", "recovery_rate_live"):
        assert key in perf, f"missing {key}"
    # Coherence, not thresholds: these are derived and must agree.
    assert perf["auc_live"] == pytest.approx((perf["gini_live"] + 1) / 2)
    assert perf["recovery_rate_live"] == pytest.approx(1 - perf["bad_rate_live"])
    assert 0.0 <= perf["brier_live"] <= 1.0
    assert rep.calibration_table is not None
    assert rep.decile_table is not None

    st = rep.stability
    assert "score_psi" in st
    assert set(st["champion_features"]) >= {"dpd", "cibil_score",
                                            "ptp_kept_ratio", "overdue_amount"}
    assert st["features_missing_from_predictions"] == []
    assert {r["feature"] for r in st["per_feature"]} == set(st["champion_features"])

    assert rep.version == SERVING
    assert rep.outcome_definition_version == OUTCOME_DEFINITION_VERSION


def test_benchmarks_from_the_artifact_sit_beside_the_live_figures(db):
    """A live Gini with nothing to compare against cannot trigger anything —
    the retrain rule is a RELATIVE drop."""
    _preds(db, 600, outcome=_alternating)
    perf = mon.monitor_model(db, MODEL, version=SERVING).performance
    assert "gini_at_development" in perf
    assert "ks_at_development" in perf
    assert "bad_rate_at_development" in perf
    if perf["gini_at_development"]:
        assert perf["gini_relative_drop"] is not None


def test_a_missing_champion_feature_is_a_finding_not_a_shorter_table(db):
    """`ptp_kept_ratio` once vanished from every served vector while coverage
    stayed above its floor. A PSI table that is simply shorter looks healthy."""
    rows = _preds(db, 600, outcome=_alternating)
    for r in rows:
        f = dict(r.features)
        f.pop("ptp_kept_ratio")
        r.features = f
    db.commit()

    rep = mon.monitor_model(db, MODEL, version=SERVING)
    assert rep.stability["features_missing_from_predictions"] == ["ptp_kept_ratio"]
    assert rep.retrain_recommended is True
    assert any("absent from the served vectors" in r for r in rep.reasons)


# ---------------------------------------------------------------------------
# Versions are never silently mixed
# ---------------------------------------------------------------------------

def test_another_model_version_cannot_open_the_gate(db):
    """490 of the serving version plus 200 of an older one is not 690."""
    _preds(db, 490, outcome=_alternating, version=SERVING)
    _preds(db, 200, outcome=_alternating, version=OTHER_VERSION)

    gate = mon.readiness(db, MODEL, version=SERVING)
    assert gate.n_matured == 490
    assert gate.ready is False
    assert gate.n_excluded_other_model_version == 200


def test_another_outcome_definition_cannot_open_the_gate(db):
    """Rows labelled by a rule the model was never validated against are a
    different measurement, not extra sample."""
    _preds(db, 480, outcome=_alternating, rule=OUTCOME_DEFINITION_VERSION)
    _preds(db, 300, outcome=_alternating, rule=OTHER_RULE)

    gate = mon.readiness(db, MODEL, version=SERVING)
    assert gate.n_matured == 480
    assert gate.ready is False
    assert gate.n_excluded_other_outcome_version == 300


def test_the_monitor_excludes_foreign_versions_and_says_so(db):
    _preds(db, 550, outcome=_alternating, version=SERVING)
    _preds(db, 120, outcome=_alternating, version=OTHER_VERSION)
    _preds(db, 90, outcome=_alternating, rule=OTHER_RULE)

    rep = mon.monitor_model(db, MODEL, version=SERVING)
    assert rep.version == SERVING
    assert rep.n_matured == 550
    assert rep.excluded["other_model_version"] == 120
    assert rep.excluded["other_outcome_definition"] == 90


def test_a_foreign_outcome_rule_drops_the_label_but_keeps_the_row(db):
    """Stability is computed on the served population, matured or not. Dropping
    the whole row would shrink the drift sample for a labelling reason."""
    _preds(db, 550, outcome=_alternating, rule=OUTCOME_DEFINITION_VERSION)
    _preds(db, 90, outcome=_alternating, rule=OTHER_RULE)

    rep = mon.monitor_model(db, MODEL, version=SERVING)
    assert rep.n_matured == 550
    assert rep.n_predictions == 640, "the row was dropped, not just its label"


def test_default_version_is_the_serving_one_not_all_of_them(db):
    """`version=None` used to mean 'pool everything' and then report the modal
    version as if it described the population. It now means 'what is serving'."""
    if mon.serving_version(MODEL) is None:
        pytest.skip("no champion artifact")
    _preds(db, 550, outcome=_alternating, version=SERVING)
    _preds(db, 400, outcome=_alternating, version=OTHER_VERSION)

    rep = mon.monitor_model(db, MODEL)
    assert rep.version == SERVING
    assert rep.n_matured == 550


def test_pooling_across_versions_must_be_asked_for_and_is_labelled(db):
    _preds(db, 300, outcome=_alternating, version=SERVING)
    _preds(db, 300, outcome=_alternating, version=OTHER_VERSION)

    rep = mon.monitor_model(db, MODEL, all_versions=True)
    assert rep.version == f"{OTHER_VERSION}|{SERVING}"
    assert any("POOLED" in r for r in rep.reasons)


# ---------------------------------------------------------------------------
# Monitoring failure must not block labelling
# ---------------------------------------------------------------------------

def test_a_broken_monitor_does_not_fail_the_labelling_task(db, monkeypatch):
    """Labels are already committed when the monitor runs. A read-only
    diagnostic that raises must not turn a correct run into a failed one — and
    the failure must stay visible rather than vanishing."""
    import app.workers.tasks.model_outcomes as task_mod

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(task_mod, "_comparison_digest", lambda *a, **k: {})
    monkeypatch.setattr(
        task_mod, "_monitoring_digest",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("monitor exploded")))

    result = task_mod.attach_model_outcomes.apply(args=(MODEL,)).get()

    assert result["monitoring"] == {"status": "error", "error": "RuntimeError"}
    # The labelling summary is still whole — the task did not bail early.
    for key in ("model", "definition_version", "horizon_days", "due", "labelled"):
        assert key in result, f"labelling did not complete: {key} missing"


def test_a_broken_monitor_leaves_committed_labels_intact(db, monkeypatch):
    import app.workers.tasks.model_outcomes as task_mod

    ids = [r.id for r in _preds(db, 3, outcome=1)]
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(task_mod, "_comparison_digest", lambda *a, **k: {})
    monkeypatch.setattr(
        task_mod, "_monitoring_digest",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    task_mod.attach_model_outcomes.apply(args=(MODEL,)).get()

    # Re-queried, not refreshed: the task closes its session in `finally`, which
    # expunges the instances the fixture handed out. Reading the rows back from
    # the database is also the stronger check — it asserts what was COMMITTED,
    # not what a still-attached object happens to hold in memory.
    fresh = Session()
    try:
        for pid in ids:
            row = fresh.query(ModelPrediction).filter_by(id=pid).one()
            assert row.actual_outcome == 1
            assert row.outcome_definition_version == OUTCOME_DEFINITION_VERSION
    finally:
        fresh.close()
