"""The retraining lifecycle, end to end, with the champion as the thing at risk.

WHAT THE 2026-09-09 AUDIT FOUND. `retrain_recommended` was a boolean returned
into a log line: monitoring fired correctly and the chain stopped. These tests
drive the chain that now reads it, and every one of them asserts the same
invariant at the end — **the champion pointer is where it was** — because that
is the only thing a mistake here can cost.

SCENARIOS A-J from the brief are each named below. What they use is a
deterministic FIXTURE COHORT, not real matured outcomes: the first genuinely
matured production cohort cannot exist before 2026-10-08. So these prove the
ORCHESTRATION is correct and cannot prove the retrained model is good, and the
two claims are kept apart everywhere they appear.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.pipeline import lifecycle, registry
from app.ml.pipeline.comparison import compare_to_incumbent
from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION, OutcomeStatus
from app.ml.pipeline.production_dataset import (
    InsufficientProductionData, MIN_MINORITY_ROWS, build_training_frame,
)
from app.models.base import Base
from app.models.model_candidate import CandidateState, ModelCandidate
from app.models.model_prediction import ModelPrediction
from app.models.user import User, UserRole
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

SERVING = "1.1.0"
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


@pytest.fixture
def user(db):
    u = User(id=str(uuid.uuid4()), email="mgr_ml@t.in", phone="9800000099",
             full_name="M", hashed_password="h", role=UserRole.AGENCY_MANAGER,
             is_active=True, is_verified=True)
    db.add(u); db.commit()
    return u


@pytest.fixture
def second_reviewer(db):
    """The other pair of eyes.

    Added 2026-09-10 with the four-eyes rule in lifecycle.promote. Before it,
    every promotion test approved and promoted as `user`, which is precisely the
    path the rule now refuses — so the tests were demonstrating a flow the
    product should never have allowed, and six of them failed the moment the
    rule landed. That is the rule working, not the tests being wrong.
    """
    u = User(id=str(uuid.uuid4()), email="mgr_ml2@t.in", phone="9800000098",
             full_name="M2", hashed_password="h", role=UserRole.AGENCY_MANAGER,
             is_active=True, is_verified=True)
    db.add(u); db.commit()
    return u


@pytest.fixture
def champion_guard():
    """Fail loudly if a test moves the real champion pointer.

    The suite runs against the committed artifacts, so a bug in promotion would
    otherwise silently repoint the model this repo ships.
    """
    pointer = registry.ARTIFACT_ROOT / "recovery_risk" / "champion.txt"
    before = pointer.read_text() if pointer.exists() else None
    yield before
    after = pointer.read_text() if pointer.exists() else None
    assert after == before, (
        f"a test moved the real champion pointer: {before!r} -> {after!r}")


def _report(*, verdict="retrain_recommended", reasons=("Gini has fallen 31%",),
            n_matured=1200, version=SERVING):
    return {"model": "recovery_risk", "version": version,
            "outcome_definition_version": OUTCOME_DEFINITION_VERSION,
            "n_matured": n_matured, "verdict": verdict,
            "retrain_recommended": verdict == "retrain_recommended",
            "reasons": list(reasons), "status": "ready"}


def _pred(db, *, case, as_of, features, outcome, version=SERVING,
          odv=OUTCOME_DEFINITION_VERSION, is_modelled=True, prob=0.5):
    # 2026-09-24 (v2): entity_id is a native UUID, and case_id / loan_id are
    # real FKs that SQLite now enforces. These fixture predictions have no case
    # or loan row behind them and never needed one — the lifecycle reads the
    # frozen features and the label — so they name the entity and nothing else.
    row = ModelPrediction(
        id=str(uuid.uuid4()), model_name="recovery_risk", model_version=version,
        entity_type="case", entity_id=test_id(f"case:{case}"), case_id=None, loan_id=None,
        as_of_date=as_of, probability=prob, is_modelled=is_modelled,
        features=features, feature_coverage=1.0, outcome_horizon_days=30,
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


def _cohort(db, n=2600, *, days=20, signal=True, odv=OUTCOME_DEFINITION_VERSION,
            outcome=None):
    """A matured production cohort spread over enough as-of dates to split.

    Deterministic: `dpd` carries the signal and the label follows it with a
    fixed pattern, so the fitted model is reproducible run to run.
    """
    rng = np.random.default_rng(7)
    start = date.today() - timedelta(days=90)
    for i in range(n):
        as_of = start + timedelta(days=i % days)
        dpd = float(20 + (i % 180))
        cibil = float(820 - (i % 300))
        ptp = round(float((i % 10) / 10.0), 2)
        overdue = float(2_000 + (i % 40) * 500)
        if outcome is not None:
            y = outcome
        elif signal:
            # A real relationship plus noise, so the model has something to
            # learn and cannot reach a suspicious Gini.
            logit = (dpd - 110) / 60.0 + (650 - cibil) / 180.0 - ptp
            y = int(rng.random() < 1.0 / (1.0 + np.exp(-logit)))
        else:
            y = int(rng.random() < 0.5)
        _pred(db, case=f"c{i}", as_of=as_of,
              features={"dpd": dpd, "cibil_score": cibil,
                        "ptp_kept_ratio": ptp, "overdue_amount": overdue},
              outcome=y, odv=odv)
    db.commit()


# ---------------------------------------------------------------------------
# The trigger — what may and may not start a retrain
# ---------------------------------------------------------------------------

def test_scenario_a_healthy_monitoring_starts_nothing(db, champion_guard):
    """SCENARIO A. Healthy -> no retraining, champion unchanged."""
    assert lifecycle.start_retraining(
        db, "recovery_risk", _report(verdict="healthy", reasons=())) is None
    assert db.query(ModelCandidate).count() == 0


def test_a_monitor_that_could_not_judge_never_triggers(db, champion_guard):
    """Three different ways of not knowing, none of them evidence of decay."""
    for verdict in ("insufficient_data", "insufficient_outcome_variation",
                    "not_ready"):
        assert lifecycle.start_retraining(db, "recovery_risk",
                                          _report(verdict=verdict)) is None
    assert db.query(ModelCandidate).count() == 0


def test_scenario_b_degradation_opens_exactly_one_candidate(db, champion_guard):
    """SCENARIO B. Degradation detected -> a candidate exists and is TRAINING."""
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    assert cand is not None
    assert cand.state == CandidateState.TRAINING
    assert cand.incumbent_version == SERVING
    assert cand.trigger_reasons == ["Gini has fallen 31%"]
    assert cand.monitoring_run_id.startswith("mon-")
    assert cand.state_history and cand.state_history[0]["to"] == "TRAINING"


def test_the_same_monitoring_event_cannot_open_two_candidates(db, champion_guard):
    """Idempotency. The nightly job runs again over the same matured cohort and
    must not start a second training run."""
    first = lifecycle.start_retraining(db, "recovery_risk", _report())
    again = lifecycle.start_retraining(db, "recovery_risk", _report())
    assert first is not None and again is None
    assert db.query(ModelCandidate).count() == 1


def test_the_event_id_is_about_the_observation_not_the_clock(db):
    """Two runs seeing the same cohort agree; a different cohort does not."""
    a = lifecycle.monitoring_event_id(_report())
    b = lifecycle.monitoring_event_id(_report())
    c = lifecycle.monitoring_event_id(_report(n_matured=1201))
    assert a == b and a != c


def test_a_second_candidate_is_suppressed_while_one_awaits_a_person(db, champion_guard):
    """Without this a decayed model opens a candidate every night until
    somebody looks."""
    first = lifecycle.start_retraining(db, "recovery_risk", _report())
    first.transition(CandidateState.PENDING_APPROVAL, "test")
    db.commit()
    assert lifecycle.start_retraining(
        db, "recovery_risk", _report(reasons=("KS has fallen 40%",))) is None
    assert db.query(ModelCandidate).count() == 1


def test_the_switch_turns_the_automation_off_without_touching_monitoring(
        db, monkeypatch, champion_guard):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ML_AUTO_RETRAIN_ENABLED", False)
    assert lifecycle.start_retraining(db, "recovery_risk", _report()) is None
    assert db.query(ModelCandidate).count() == 0


# ---------------------------------------------------------------------------
# SCENARIO J — insufficient real production data, and NO synthetic fallback
# ---------------------------------------------------------------------------

def test_scenario_j_no_production_data_stops_with_an_explicit_state(
        db, champion_guard):
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)

    assert out.state == CandidateState.INSUFFICIENT_DATA
    assert out.candidate_version is None, "an artifact was minted with no data"
    assert "no matured" in (out.error or "").lower()
    assert out.training_cohort["reason"] == "no_rows"


def test_a_thin_cohort_is_refused_rather_than_trained_on(db, champion_guard):
    _cohort(db, n=300, days=10)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)
    assert out.state == CandidateState.INSUFFICIENT_DATA
    assert any("rows, need" in p for p in out.training_cohort["problems"])


def test_a_one_class_cohort_is_refused(db, champion_guard):
    _cohort(db, n=2600, days=20, outcome=1)
    with pytest.raises(InsufficientProductionData) as exc:
        build_training_frame(db, "recovery_risk")
    assert any("recovered outcomes, need" in p
               for p in exc.value.detail["problems"])


def test_the_production_builder_cannot_reach_the_synthetic_panel(db):
    """The one fallback that must not exist. Asserted on the import graph, then
    on behaviour: with no rows it RAISES rather than returning a frame."""
    import app.ml.pipeline.production_dataset as pd_mod

    src = Path(pd_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("book_simulator", "BookSimulator", "build_modelling_dataset",
                      "read_parquet"):
        assert forbidden not in src.split('"""', 2)[-1], (
            f"the production training frame references {forbidden}")
    with pytest.raises(InsufficientProductionData):
        build_training_frame(db, "recovery_risk")


# ---------------------------------------------------------------------------
# The production training frame
# ---------------------------------------------------------------------------

def test_the_frame_is_built_from_frozen_vectors_and_attached_labels(db):
    _cohort(db)
    frame, cohort = build_training_frame(db, "recovery_risk")

    assert len(frame) == cohort.n_rows == 2600
    assert set(frame.y.unique()) == {0, 1}
    assert cohort.outcome_definition_version == OUTCOME_DEFINITION_VERSION
    assert cohort.horizon_days == 30
    assert cohort.n_periods == 20
    assert cohort.cohort_digest and len(cohort.cohort_digest) == 64
    # Traceability: prediction -> case/loan -> as-of -> outcome.
    for col in ("prediction_id", "case_id", "loan_id", "as_of_date", "y"):
        assert col in frame.columns
    assert frame.prediction_id.nunique() == len(frame)


def test_censored_and_unmatured_rows_never_become_training_examples(db):
    """A bank write-off is not borrower behaviour, and an unmatured row is not
    a non-event. Both carry actual_outcome NULL and both must be absent."""
    _cohort(db)
    base = len(build_training_frame(db, "recovery_risk")[0])

    as_of = date.today() - timedelta(days=60)
    feats = {"dpd": 90.0, "cibil_score": 600.0, "ptp_kept_ratio": 0.5,
             "overdue_amount": 5000.0}
    for i in range(50):
        r = _pred(db, case=f"cens{i}", as_of=as_of, features=feats, outcome=None)
        r.outcome_status = OutcomeStatus.CENSORED_WRITTEN_OFF.value
        r.outcome_definition_version = OUTCOME_DEFINITION_VERSION
    for i in range(50):
        _pred(db, case=f"young{i}", as_of=date.today(), features=feats, outcome=None)
    db.commit()

    frame, _ = build_training_frame(db, "recovery_risk")
    assert len(frame) == base
    assert not frame.case_id.astype(str).str.startswith("cens").any()
    assert not frame.case_id.astype(str).str.startswith("young").any()


def test_a_foreign_outcome_definition_is_not_pooled_in(db):
    _cohort(db)
    base = len(build_training_frame(db, "recovery_risk")[0])
    _cohort(db, n=400, days=10, odv="some-other-rule-2.0.0")
    frame, cohort = build_training_frame(db, "recovery_risk")
    assert len(frame) == base
    assert cohort.outcome_definition_version == OUTCOME_DEFINITION_VERSION


def test_declined_scores_are_excluded(db):
    _cohort(db)
    base = len(build_training_frame(db, "recovery_risk")[0])
    for i in range(100):
        _pred(db, case=f"dec{i}", as_of=date.today() - timedelta(days=60),
              features={"dpd": 90.0}, outcome=1, is_modelled=False)
    db.commit()
    assert len(build_training_frame(db, "recovery_risk")[0]) == base


def test_re_scores_of_one_account_day_count_once(db):
    """The same rule the monitor applies. Nine copies of one observation would
    inflate n and correlate the errors."""
    as_of = date.today() - timedelta(days=60)
    feats = {"dpd": 60.0, "cibil_score": 700.0, "ptp_kept_ratio": 0.4,
             "overdue_amount": 8000.0}
    _cohort(db)
    base = len(build_training_frame(db, "recovery_risk")[0])
    for _ in range(8):
        _pred(db, case="c0", as_of=as_of, features=feats, outcome=1)
    db.commit()
    frame, cohort = build_training_frame(db, "recovery_risk")
    assert len(frame) == base + 1
    assert cohort.excluded["duplicate_rescores"] == 7


def test_no_post_prediction_column_can_enter_the_frame(db):
    """LEAKAGE TEST. The frame's only feature source is the frozen vector, so a
    column written after the prediction cannot appear even if the row carries
    it — the outcome columns are read for the LABEL and nothing else."""
    _cohort(db)
    frame, _ = build_training_frame(db, "recovery_risk")
    leaky = {"actual_outcome", "outcome_status", "outcome_attached_at",
             "probability", "points", "band", "label_comparison",
             "outcome_baseline", "collected_amount", "amount_paid"}
    assert not (leaky & set(frame.columns)), (
        f"post-prediction columns reached the training frame: "
        f"{leaky & set(frame.columns)}")


def test_the_frame_is_ordered_in_time_so_the_split_is_chronological(db):
    _cohort(db)
    frame, _ = build_training_frame(db, "recovery_risk")
    periods = frame["as_of_period"].tolist()
    assert periods == sorted(periods)


def test_two_builds_of_the_same_rows_produce_the_same_digest(db):
    _cohort(db)
    _, a = build_training_frame(db, "recovery_risk")
    _, b = build_training_frame(db, "recovery_risk")
    assert a.cohort_digest == b.cohort_digest
    _pred(db, case="extra", as_of=date.today() - timedelta(days=60),
          features={"dpd": 30.0, "cibil_score": 700.0, "ptp_kept_ratio": 0.5,
                    "overdue_amount": 4000.0}, outcome=0)
    db.commit()
    _, c = build_training_frame(db, "recovery_risk")
    assert c.cohort_digest != a.cohort_digest


# ---------------------------------------------------------------------------
# SCENARIOS C, D, E — validation and the incumbent comparison
# ---------------------------------------------------------------------------

def _fake_train(monkeypatch, *, passed=True, gates=None):
    """Replace the fit with a deterministic stand-in.

    The trainer is exercised for real in `test_ml_pipeline.py`; what these
    scenarios are about is what the ORCHESTRATOR does with each outcome, and a
    real 2,600-row fit per scenario would make the suite unusable.
    """
    from app.ml.pipeline import train as train_mod
    import pandas as pd

    rows = gates or [{"gate": "gini_min", "observed": 0.42, "threshold": ">= 0.25",
                      "result": "PASS" if passed else "FAIL"}]

    class _Fake:
        def __init__(self, spec, panel, **kw):
            self.spec, self.panel = spec, panel

        def _split(self):
            n = len(self.panel)
            i, j = int(n * 0.6), int(n * 0.75)
            return (self.panel.iloc[:i].copy(), self.panel.iloc[i:j].copy(),
                    self.panel.iloc[j:].copy())

        def run(self, *, make_champion=True):
            assert make_champion is False, "the orchestrator asked to promote"
            return train_mod.TrainResult(
                spec=self.spec, artifact_dir=Path("."),
                metrics={"oot": {"gini": 0.42}}, gates=pd.DataFrame(rows),
                selected=list(self.spec.all_features), passed=passed)

    monkeypatch.setattr("app.ml.pipeline.train.ModelTrainer", _Fake)
    return _Fake


def _fake_scores(monkeypatch, *, challenger, incumbent):
    """Calibrated score vectors of a chosen separation.

    THE SCORES HAVE TO BE CALIBRATED, and finding that out was the point. A
    first version returned `sigmoid(strength * y + noise)`, which ranks
    beautifully and predicts 0.72 where the observed rate is 0.50 — and
    `calibration_not_worse` correctly rejected it even at +0.40 Gini. That gate
    is not decoration: the allocator computes
    `expected_case_inr = collectable x probability`, so a model that is 22
    points out misprices every case by 22 points of its balance however well it
    ranks.

    So the separation is generated as a latent Gaussian and converted to the
    exact posterior, which is calibrated by construction:

        s | y ~ N(d*y, 1)   =>   logit P(y=1|s) = logit(pi) + d*s - d^2/2
    """
    def _scores(model, version, frame, features):
        n = len(frame)
        rng = np.random.default_rng(3 if "candidate" in version else 4)
        y = frame["y"].to_numpy().astype(float)
        d = float(challenger if "candidate" in version else incumbent)
        s = d * y + rng.normal(0, 1.0, n)
        pi = min(max(float(y.mean()), 1e-6), 1 - 1e-6)
        logit = np.log(pi / (1 - pi)) + d * s - (d ** 2) / 2.0
        return 1.0 / (1.0 + np.exp(-logit))
    monkeypatch.setattr(lifecycle, "_engine_scores", _scores)


def test_scenario_c_a_challenger_that_fails_a_gate_is_rejected_and_inert(
        db, monkeypatch, champion_guard):
    """SCENARIO C. Fails validation -> REJECTED_VALIDATION, champion unchanged."""
    _cohort(db)
    _fake_train(monkeypatch, passed=False, gates=[
        {"gate": "gini_min", "observed": 0.11, "threshold": ">= 0.25",
         "result": "FAIL"},
        {"gate": "ks_min", "observed": 12.0, "threshold": ">= 20.0",
         "result": "FAIL"}])
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)

    assert out.state == CandidateState.REJECTED_VALIDATION
    assert out.gates_passed is False
    assert "gini_min" in out.rejection_reason and "ks_min" in out.rejection_reason
    assert out.comparison_passed is None, "a failed model reached the comparison"
    assert out.is_promotable is False
    # Every gate row is persisted, not a summary.
    assert len(out.gate_results) == 2


def test_scenario_d_passing_absolute_gates_is_not_enough_to_beat_the_incumbent(
        db, monkeypatch, champion_guard):
    """SCENARIO D. The defect the audit named: absolute gates are wide
    (gini_min 0.25 against a champion at 0.5136), so a competent-but-worse refit
    clears them. It must still be rejected."""
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    _fake_scores(monkeypatch, challenger=0.6, incumbent=2.2)   # clearly worse

    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)

    assert out.gates_passed is True, "this scenario requires the gates to pass"
    assert out.state == CandidateState.REJECTED_COMPARISON
    assert out.comparison_passed is False
    assert out.gini_uplift < 0
    assert out.comparison_results["verdict"] == "challenger_worse"
    assert out.is_promotable is False


def test_an_equivalent_challenger_keeps_the_incumbent(db, monkeypatch,
                                                      champion_guard):
    """The stated policy for a tie: keep what is proven. Replacing a working
    model with an indistinguishable one is churn with a rollback attached."""
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    _fake_scores(monkeypatch, challenger=1.5, incumbent=1.5)

    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)

    assert out.state == CandidateState.REJECTED_COMPARISON
    assert out.comparison_results["verdict"] == "equivalent_keep_incumbent"
    assert abs(out.gini_uplift) <= out.comparison_results["uplift_tolerance"]


def test_scenario_e_a_strong_challenger_reaches_pending_approval_and_stops(
        db, monkeypatch, champion_guard):
    """SCENARIO E. Everything passes -> PENDING_APPROVAL. The champion is
    unchanged, and no automated path can move it further."""
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    _fake_scores(monkeypatch, challenger=2.6, incumbent=1.0)

    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)

    assert out.state == CandidateState.PENDING_APPROVAL
    assert out.gates_passed is True and out.comparison_passed is True
    assert out.gini_uplift > out.comparison_results["uplift_tolerance"]
    assert out.candidate_version and out.candidate_version != SERVING
    assert out.is_promotable is False, "PENDING_APPROVAL must not be promotable"
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_the_comparison_thresholds_are_derived_from_sampling_error(db):
    """Not picked. The tolerance must widen as the frame shrinks, or a tiny
    cohort would let noise promote a model."""
    y = np.array([1, 0] * 400)
    rng = np.random.default_rng(11)
    ch = rng.random(800)
    inc = rng.random(800)
    big = compare_to_incumbent("m", challenger_version="c", challenger_scores=ch,
                               incumbent_version="i", incumbent_scores=inc, y=y)
    small = compare_to_incumbent("m", challenger_version="c",
                                 challenger_scores=ch[:120],
                                 incumbent_version="i", incumbent_scores=inc[:120],
                                 y=y[:120])
    assert small.uplift_tolerance > big.uplift_tolerance


def test_the_comparison_scores_both_models_on_the_same_rows(db, monkeypatch,
                                                            champion_guard):
    """Two Ginis from two frames are two facts about two populations."""
    seen = {}

    def _scores(model, version, frame, features):
        seen[version] = (len(frame), tuple(frame["prediction_id"].tolist()[:5]))
        return np.linspace(0.1, 0.9, len(frame))

    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    monkeypatch.setattr(lifecycle, "_engine_scores", _scores)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    lifecycle.run_candidate(db, cand.id)

    assert len(seen) == 2
    assert len(set(seen.values())) == 1, "the two sides were scored on different rows"


# ---------------------------------------------------------------------------
# SCENARIOS F, G, H — the human gate
# ---------------------------------------------------------------------------

def _pending(db, monkeypatch, *, uplift=2.6):
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    _fake_scores(monkeypatch, challenger=uplift, incumbent=1.0)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)
    assert out.state == CandidateState.PENDING_APPROVAL
    return out


def test_scenario_f_human_rejection_leaves_the_champion_alone(db, monkeypatch,
                                                              user, champion_guard):
    """SCENARIO F."""
    cand = _pending(db, monkeypatch)
    out = lifecycle.reject(db, cand.id, user_id=user.id, note="not convinced")

    assert out.state == CandidateState.REJECTED_BY_HUMAN
    assert out.decided_by_id == user.id and out.decided_at is not None
    assert out.is_promotable is False
    with pytest.raises(lifecycle.ApprovalRefused):
        lifecycle.promote(db, cand.id, user_id=user.id)


def test_rejection_is_idempotent(db, monkeypatch, user, champion_guard):
    cand = _pending(db, monkeypatch)
    lifecycle.reject(db, cand.id, user_id=user.id)
    again = lifecycle.reject(db, cand.id, user_id=user.id)
    assert again.state == CandidateState.REJECTED_BY_HUMAN


def test_approval_records_a_person_and_still_does_not_promote(db, monkeypatch,
                                                              user, champion_guard):
    cand = _pending(db, monkeypatch)
    out = lifecycle.approve(db, cand.id, user_id=user.id, note="reviewed")

    assert out.state == CandidateState.APPROVED
    assert out.decided_by_id == user.id
    assert out.is_promotable is True
    # Approval alone changed nothing about what is served.
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_approval_is_idempotent(db, monkeypatch, user, champion_guard):
    cand = _pending(db, monkeypatch)
    lifecycle.approve(db, cand.id, user_id=user.id)
    again = lifecycle.approve(db, cand.id, user_id=user.id)
    assert again.state == CandidateState.APPROVED


def test_a_rejected_candidate_cannot_be_approved(db, monkeypatch, user,
                                                 champion_guard):
    _cohort(db)
    _fake_train(monkeypatch, passed=False)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)
    assert out.state == CandidateState.REJECTED_VALIDATION
    with pytest.raises(lifecycle.ApprovalRefused, match="PENDING_APPROVAL"):
        lifecycle.approve(db, cand.id, user_id=user.id)


def test_a_candidate_that_lost_the_comparison_cannot_be_approved(
        db, monkeypatch, user, champion_guard):
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    _fake_scores(monkeypatch, challenger=0.5, incumbent=2.2)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)
    assert out.state == CandidateState.REJECTED_COMPARISON
    with pytest.raises(lifecycle.ApprovalRefused):
        lifecycle.approve(db, cand.id, user_id=user.id)


def test_scenario_h_a_stale_approval_is_refused(db, monkeypatch, user,
                                                champion_guard):
    """SCENARIO H. The champion moved after the candidate was compared against
    it, so the comparison it passed describes a model nobody is running."""
    cand = _pending(db, monkeypatch)
    cand.incumbent_version = "0.9.0-something-else"   # as if the pointer moved
    db.commit()

    with pytest.raises(lifecycle.ApprovalRefused, match="stale"):
        lifecycle.approve(db, cand.id, user_id=user.id)
    assert cand.state == CandidateState.PENDING_APPROVAL
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_a_hand_edited_approved_row_still_cannot_be_promoted(db, monkeypatch,
                                                             user, champion_guard):
    """Defence in depth: APPROVED is necessary and not sufficient. Promotion
    re-reads the recorded gate and comparison results."""
    cand = _pending(db, monkeypatch)
    lifecycle.approve(db, cand.id, user_id=user.id)
    cand.comparison_passed = False        # as if someone edited the row
    db.commit()
    assert cand.is_promotable is False
    with pytest.raises(lifecycle.ApprovalRefused, match="not a pass"):
        lifecycle.promote(db, cand.id, user_id=user.id)
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


# ---------------------------------------------------------------------------
# SCENARIOS G, I — promotion and rollback, on an ISOLATED model
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_model(tmp_path, monkeypatch):
    """A throwaway model tree, so promotion is executed for real without ever
    touching the artifacts this repo ships."""
    root = tmp_path / "artifacts"
    (root / "demo_model" / "1.0.0").mkdir(parents=True)
    (root / "demo_model" / "2.0.0").mkdir(parents=True)
    for v in ("1.0.0", "2.0.0"):
        (root / "demo_model" / v / "metadata.json").write_text(
            json.dumps({"version": v, "gate_summary": "PASS"}))
    (root / "demo_model" / "champion.txt").write_text("1.0.0")
    monkeypatch.setattr(registry, "ARTIFACT_ROOT", root)
    return root / "demo_model"


def _approved(db, user, *, version="2.0.0", incumbent="1.0.0"):
    cand = ModelCandidate(
        id=str(uuid.uuid4()), model_name="demo_model",
        candidate_version=version, incumbent_version=incumbent,
        state=CandidateState.APPROVED, monitoring_run_id=f"mon-{uuid.uuid4().hex[:8]}",
        gates_passed=True, comparison_passed=True, gini_uplift=0.06,
        decided_by_id=user.id, decided_at=datetime.now(timezone.utc),
        state_history=[])
    db.add(cand); db.commit()
    return cand


def test_scenario_g_approved_candidate_promotes_and_leaves_an_audit_trail(
        db, user, second_reviewer, isolated_model, monkeypatch):
    """SCENARIO G. Approved -> promoted -> the pointer moves -> rollback target
    recorded."""
    monkeypatch.setattr("app.ml.pipeline.engine.DecisionEngine.reload",
                        classmethod(lambda cls, m: {"model": m}))
    cand = _approved(db, user)
    out = lifecycle.promote(db, cand.id, user_id=second_reviewer.id)

    assert out.state == CandidateState.PROMOTED
    assert (isolated_model / "champion.txt").read_text() == "2.0.0"
    assert out.promoted_from_version == "1.0.0", "no rollback target recorded"
    assert out.promoted_at is not None
    assert [h["to"] for h in out.state_history][-1] == "PROMOTED"


# ── four eyes ───────────────────────────────────────────────────────────────
# Every other gate on this path asks whether the MODEL is good enough. None
# asked how many PEOPLE agreed, so one person could approve and then promote in
# two clicks — and the role gate does not help, because `ManagerOnly` resolves to
# AGENCY_MANAGER + AGENCY_ADMIN and AGENCY_ADMIN distinguishes nothing anywhere
# in the codebase. Found by the repo-wide audit of 2026-09-10.

def test_the_approver_cannot_also_promote(db, user, isolated_model, monkeypatch):
    """The rule. Same person, both steps -> refused, and the pointer never moves."""
    monkeypatch.setattr(registry, "ARTIFACT_ROOT", isolated_model.parent)
    cand = _approved(db, user)
    before = (isolated_model / "champion.txt").read_text()

    with pytest.raises(lifecycle.ApprovalRefused, match="four-eyes"):
        lifecycle.promote(db, cand.id, user_id=user.id)

    db.refresh(cand)
    assert cand.state == CandidateState.APPROVED, "state must not advance"
    assert cand.promoted_at is None
    assert (isolated_model / "champion.txt").read_text() == before, (
        "the pointer moved on a refused promotion")


def test_a_second_reviewer_can_promote_the_same_candidate(
        db, user, second_reviewer, isolated_model, monkeypatch):
    """The rule must not have closed the door on its legitimate path — otherwise
    the safe outcome is indistinguishable from a broken one."""
    monkeypatch.setattr(registry, "ARTIFACT_ROOT", isolated_model.parent)
    cand = _approved(db, user)
    out = lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    assert out.state == CandidateState.PROMOTED
    assert (isolated_model / "champion.txt").read_text().strip() == "2.0.0"


def test_four_eyes_is_not_defeated_by_rejecting_and_re_approving(
        db, user, isolated_model, monkeypatch):
    """`decided_by_id` is overwritten by whoever decided LAST, so the obvious way
    round the rule is to have a second person approve and the first promote. That
    is fine — it is still two people. What must NOT work is one person laundering
    their own approval through a state change."""
    monkeypatch.setattr(registry, "ARTIFACT_ROOT", isolated_model.parent)
    cand = _approved(db, user)
    cand.state = CandidateState.PENDING_APPROVAL
    db.commit()
    lifecycle.approve(db, cand.id, user_id=user.id)      # same person again
    with pytest.raises(lifecycle.ApprovalRefused, match="four-eyes"):
        lifecycle.promote(db, cand.id, user_id=user.id)


def test_four_eyes_does_not_fire_when_no_approver_was_recorded(
        db, user, isolated_model, monkeypatch):
    """A NULL decided_by_id is absent evidence, not evidence of self-approval.
    Refusing on it would block a candidate nobody can ever promote."""
    monkeypatch.setattr(registry, "ARTIFACT_ROOT", isolated_model.parent)
    cand = _approved(db, user)
    cand.decided_by_id = None
    db.commit()
    out = lifecycle.promote(db, cand.id, user_id=user.id)
    assert out.state == CandidateState.PROMOTED


def test_promotion_is_idempotent(db, user, second_reviewer, isolated_model, monkeypatch):
    monkeypatch.setattr("app.ml.pipeline.engine.DecisionEngine.reload",
                        classmethod(lambda cls, m: {}))
    cand = _approved(db, user)
    lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    again = lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    assert again.state == CandidateState.PROMOTED
    assert (isolated_model / "champion.txt").read_text() == "2.0.0"


def test_scenario_i_rollback_after_promotion_restores_the_previous_champion(
        db, user, second_reviewer, isolated_model, monkeypatch):
    """SCENARIO I. The recorded `promoted_from_version` is the rollback target,
    and `ML_MODEL_VERSION` pins what is served without touching the pointer."""
    from app.core.config import settings
    from app.ml.pipeline.engine import DecisionEngine

    monkeypatch.setattr(DecisionEngine, "reload", classmethod(lambda cls, m: {}))
    cand = _approved(db, user)
    lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    assert (isolated_model / "champion.txt").read_text() == "2.0.0"

    # Route 1: pin the version. The pointer is untouched and the previous model
    # is served immediately — the fastest rollback and the one that needs no
    # file write.
    monkeypatch.setattr(settings, "ML_MODEL_VERSION", cand.promoted_from_version)
    assert registry.resolve_version("demo_model", settings.ML_MODEL_VERSION) == "1.0.0"

    # Route 2: move the pointer back, using the recorded target.
    registry.promote("demo_model", cand.promoted_from_version,
                     expected_incumbent="2.0.0", approved_by=user.id,
                     candidate_id="rollback")
    assert (isolated_model / "champion.txt").read_text() == "1.0.0"


def test_promotion_refuses_when_the_champion_moved_underneath_it(
        db, user, second_reviewer, isolated_model, monkeypatch):
    """The race the audit worried about: approval happened, then somebody else
    promoted something. Refused, not reconciled."""
    monkeypatch.setattr("app.ml.pipeline.engine.DecisionEngine.reload",
                        classmethod(lambda cls, m: {}))
    cand = _approved(db, user)
    (isolated_model / "champion.txt").write_text("2.0.0")     # moved by someone

    with pytest.raises(lifecycle.ApprovalRefused, match="champion moved"):
        lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    assert (isolated_model / "champion.txt").read_text() == "2.0.0"
    assert cand.state == CandidateState.APPROVED


def test_promotion_refuses_a_candidate_whose_artifact_failed_its_gates(
        db, user, second_reviewer, isolated_model):
    """A model is written even when it fails so the failure can be read.
    Pointing at that directory must not make it champion."""
    (isolated_model / "3.0.0").mkdir()
    (isolated_model / "3.0.0" / "metadata.json").write_text(
        json.dumps({"version": "3.0.0", "gate_summary": "FAIL"}))
    cand = _approved(db, user, version="3.0.0")

    with pytest.raises(lifecycle.ApprovalRefused, match="did not pass its gates"):
        lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    assert (isolated_model / "champion.txt").read_text() == "1.0.0"


def test_promotion_refuses_a_version_with_no_artifact(db, user, second_reviewer, isolated_model):
    cand = _approved(db, user, version="9.9.9-never-trained")
    with pytest.raises(lifecycle.ApprovalRefused, match="no artifact"):
        lifecycle.promote(db, cand.id, user_id=second_reviewer.id)
    assert (isolated_model / "champion.txt").read_text() == "1.0.0"


# ---------------------------------------------------------------------------
# Failure safety — the champion survives everything
# ---------------------------------------------------------------------------

def test_a_crash_during_training_leaves_a_failed_row_not_a_stuck_one(
        db, monkeypatch, champion_guard):
    """A candidate stuck in TRAINING blocks every future trigger for the model."""
    _cohort(db)

    class _Boom:
        def __init__(self, *a, **k): pass
        def _split(self): raise MemoryError("worker died")

    monkeypatch.setattr("app.ml.pipeline.train.ModelTrainer", _Boom)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)

    assert out.state == CandidateState.FAILED
    assert "MemoryError" in out.error
    assert out.is_promotable is False
    # And the model is now free to be retried by a later monitoring event.
    assert lifecycle.start_retraining(
        db, "recovery_risk", _report(n_matured=1300)) is not None


def test_a_crash_during_comparison_does_not_leave_a_promotable_candidate(
        db, monkeypatch, champion_guard):
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    monkeypatch.setattr(lifecycle, "_engine_scores",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)
    assert out.state == CandidateState.FAILED
    assert out.comparison_passed is None
    assert out.is_promotable is False


def test_re_running_a_finished_candidate_does_nothing(db, monkeypatch,
                                                      champion_guard):
    """Celery re-delivery. A second run must not mint a second artifact."""
    cand = _pending(db, monkeypatch)
    version = cand.candidate_version
    again = lifecycle.run_candidate(db, cand.id)
    assert again.state == CandidateState.PENDING_APPROVAL
    assert again.candidate_version == version


def test_a_model_that_declines_part_of_the_frame_is_not_compared(
        db, monkeypatch, champion_guard):
    """If a side could not score every row, the two vectors describe different
    populations and the comparison is meaningless."""
    _cohort(db)
    _fake_train(monkeypatch, passed=True)

    def _scores(model, version, frame, features):
        out = np.linspace(0.1, 0.9, len(frame))
        if "candidate" in version:
            out[0] = np.nan
        return out

    monkeypatch.setattr(lifecycle, "_engine_scores", _scores)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    out = lifecycle.run_candidate(db, cand.id)
    assert out.state == CandidateState.REJECTED_COMPARISON
    assert "same rows" in out.rejection_reason or "incomplete" in out.rejection_reason


def test_the_trainer_is_never_asked_to_promote(db, monkeypatch, champion_guard):
    """`make_champion=False` on every automated path. The stand-in asserts it,
    so a future edit that flips it fails here rather than in production."""
    _cohort(db)
    _fake_train(monkeypatch, passed=True)
    _fake_scores(monkeypatch, challenger=2.6, incumbent=1.0)
    cand = lifecycle.start_retraining(db, "recovery_risk", _report())
    assert lifecycle.run_candidate(db, cand.id).state == \
        CandidateState.PENDING_APPROVAL


# ---------------------------------------------------------------------------
# Cache / pointer safety
# ---------------------------------------------------------------------------

def test_a_promotion_is_visible_as_a_mismatch_until_the_process_reloads(
        isolated_model, monkeypatch):
    """Updating champion.txt does NOT update an already-loaded model object.
    That has to be detectable, or a fleet can split across two champions with
    every instance reporting itself healthy."""
    from app.ml.pipeline.engine import DecisionEngine

    class _Stub:
        def __init__(self, model, version):
            self.version = registry.resolve_version(model, version)

    monkeypatch.setattr(DecisionEngine, "__init__",
                        lambda self, m, v: setattr(self, "version",
                                                   registry.resolve_version(m, v)))
    DecisionEngine.clear_cache()
    try:
        DecisionEngine.get("demo_model")
        assert DecisionEngine.serving_state("demo_model")["loaded_versions"] == ["1.0.0"]
        assert DecisionEngine.serving_state("demo_model")["serving_matches_pointer"]

        (isolated_model / "champion.txt").write_text("2.0.0")
        state = DecisionEngine.serving_state("demo_model")
        assert state["pointer_version"] == "2.0.0"
        assert state["loaded_versions"] == ["1.0.0"]
        assert state["serving_matches_pointer"] is False, (
            "a stale in-process model reported itself as matching the pointer")

        after = DecisionEngine.reload("demo_model")
        assert after["loaded_versions"] == ["2.0.0"]
        assert after["serving_matches_pointer"] is True
        assert "instance" in after and ":" in after["instance"]
    finally:
        DecisionEngine.clear_cache()


# ---------------------------------------------------------------------------
# The nightly task's trigger — the arrow that did not exist
# ---------------------------------------------------------------------------

def test_the_nightly_task_enqueues_training_when_monitoring_asks(
        db, monkeypatch, champion_guard):
    """The arrow the audit marked NOT WIRED: retrain_recommended -> retraining."""
    import app.workers.tasks.model_outcomes as task_mod

    sent = []
    monkeypatch.setattr(
        "app.workers.tasks.model_retraining.run_candidate_training.delay",
        lambda cid: sent.append(cid))

    out = task_mod._retrain_trigger(db, "recovery_risk", _report())

    assert out["status"] == "enqueued"
    cand = db.query(ModelCandidate).one()
    assert sent == [cand.id], "the candidate was created but never enqueued"
    assert out["candidate_id"] == cand.id
    assert out["reasons"] == ["Gini has fallen 31%"]


def test_the_task_does_not_enqueue_on_any_non_triggering_state(
        db, monkeypatch, champion_guard):
    import app.workers.tasks.model_outcomes as task_mod

    sent = []
    monkeypatch.setattr(
        "app.workers.tasks.model_retraining.run_candidate_training.delay",
        lambda cid: sent.append(cid))

    assert task_mod._retrain_trigger(db, "recovery_risk", {"status": "not_ready"}
                                     )["status"] == "skipped"
    assert task_mod._retrain_trigger(
        db, "recovery_risk", _report(verdict="healthy", reasons=())
    )["status"] == "not_needed"
    assert task_mod._retrain_trigger(
        db, "recovery_risk",
        {**_report(verdict="insufficient_outcome_variation"),
         "retrain_recommended": False})["status"] == "not_needed"

    assert sent == []
    assert db.query(ModelCandidate).count() == 0


def test_a_duplicate_trigger_is_reported_as_suppressed_not_enqueued(
        db, monkeypatch, champion_guard):
    import app.workers.tasks.model_outcomes as task_mod

    sent = []
    monkeypatch.setattr(
        "app.workers.tasks.model_retraining.run_candidate_training.delay",
        lambda cid: sent.append(cid))

    first = task_mod._retrain_trigger(db, "recovery_risk", _report())
    second = task_mod._retrain_trigger(db, "recovery_risk", _report())

    assert first["status"] == "enqueued"
    assert second["status"] == "suppressed"
    assert len(sent) == 1
    assert db.query(ModelCandidate).count() == 1


def test_a_broken_trigger_cannot_cost_a_label(db, monkeypatch, champion_guard):
    """The same containment the comparison and the monitor already have:
    labelling commits first, and a read-out beside it must not fail the task."""
    import app.workers.tasks.model_outcomes as task_mod

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(task_mod, "_comparison_digest", lambda *a, **k: {})
    monkeypatch.setattr(task_mod, "_monitoring_digest", lambda *a, **k: _report())
    monkeypatch.setattr(task_mod, "_retrain_trigger",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    result = task_mod.attach_model_outcomes.apply(args=("recovery_risk",)).get()

    assert result["retraining"] == {"status": "error", "error": "RuntimeError"}
    for key in ("model", "definition_version", "horizon_days", "labelled"):
        assert key in result, "labelling did not complete"


def test_the_retraining_task_is_registered_but_deliberately_not_scheduled(
        champion_guard):
    """Retraining is not a nightly thing; it is a thing monitoring asks for."""
    from app.workers.celery_app import celery_app

    assert "app.workers.tasks.model_retraining" in celery_app.conf.include
    tasks = [v["task"] for v in celery_app.conf.beat_schedule.values()]
    assert len(tasks) == len(set(tasks)), "a scheduled job is registered twice"
    assert not any("model_retraining" in t for t in tasks)
    assert "app.workers.tasks.model_outcomes.attach_model_outcomes" in tasks


# ---------------------------------------------------------------------------
# The real trainer, on a real production frame
# ---------------------------------------------------------------------------

def test_the_real_trainer_runs_end_to_end_on_a_production_frame(
        db, tmp_path, monkeypatch, champion_guard):
    """NOT a stubbed trainer. The scenarios above replace `ModelTrainer` so the
    ORCHESTRATOR is the thing under test; this one runs the genuine fit, WOE
    binning, selection, sign checks and the full gate table against a frame
    built from `model_predictions`.

    It exists because everything else here would pass with a `_derive_spec`
    that produces a spec the real trainer cannot consume — an empty categorical
    list, a split column the plotting code chokes on, a segment column that is
    not there. Those are exactly the failures a stub cannot see.

    The artifact is written into a temporary registry root, so a real
    `registry.save` happens and touches nothing this repo ships.
    """
    monkeypatch.setattr(registry, "ARTIFACT_ROOT", tmp_path / "artifacts")
    _cohort(db, n=3000, days=25)

    from app.ml.pipeline import config as cfg
    from app.ml.pipeline.production_dataset import build_training_frame
    from app.ml.pipeline.train import ModelTrainer

    frame, cohort = build_training_frame(db, "recovery_risk")
    spec = lifecycle._derive_spec(cfg.RECOVERY_RISK,
                                  version="1.1.0-candidate-realtrainer",
                                  frame=frame)
    # The spec the orchestrator derives must be one the real trainer accepts.
    assert spec.split_col == "as_of_period" and spec.target == "y"
    assert set(spec.numeric_features) <= set(frame.columns)
    assert spec.gates.gini_min == cfg.RECOVERY_RISK.gates.gini_min, \
        "a gate threshold was weakened for the automated path"

    trainer = ModelTrainer(spec, frame, dataset_meta=cohort.to_dict())
    train, valid, oot = trainer._split()
    assert len(train) and len(valid) and len(oot), "the split produced an empty slice"
    assert train["as_of_period"].max() < valid["as_of_period"].min()
    assert valid["as_of_period"].max() < oot["as_of_period"].min(), \
        "the split is not chronological — the OOT read would be contaminated"

    result = trainer.run(make_champion=False)

    assert result.gates is not None and len(result.gates) >= 8, \
        "the automated path ran fewer gates than the manual CLI"
    names = set(result.gates["gate"])
    for required in ("gini_min", "gini_not_suspicious", "ks_min",
                     "rank_order_breaks_top5", "train_test_gini_gap"):
        assert required in names, f"gate {required} was not evaluated"
    # The artifact exists, is isolated, and is NOT champion.
    assert (result.artifact_dir / "metadata.json").exists()
    assert not (tmp_path / "artifacts" / "recovery_risk" / "champion.txt").exists(), \
        "an automated training run wrote a champion pointer"
