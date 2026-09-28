"""Phase 4: the lifecycle stages, on a materialised ledger.

The full replay lives in `scripts/phase4_ledger_lifecycle.py` and takes minutes.
These pin the properties that replay depends on, executably and quickly:

  * a prediction carries a frozen baseline taken at as_of, not at labelling;
  * the payment window's edges are where `outcomes.py` says they are;
  * reversed, rejected and pending money is treated as the definition says;
  * censoring reaches the labeller through the columns it actually reads;
  * the comparison honours the label that was ATTACHED, rather than forming a
    fresh opinion from state that has moved on;
  * monitoring cannot mix model versions or outcome definitions.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.ml.pipeline import monitor as mon
from app.ml.pipeline.engine import DecisionEngine
from app.ml.pipeline.label_comparison import compare, compare_all
from app.ml.pipeline.outcomes import (
    OUTCOME_DEFINITION_VERSION, OutcomeStatus, attach_outcomes, evaluate,
    payment_window,
)
from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.materialise import Materialiser
from app.models.base import Base
from app.models.case import Case
from app.models.customer import Customer
from app.models.loan import Loan, LoanStatus
from app.models.model_prediction import ModelPrediction
from app.models.payment import Payment, PaymentStatus
from app.services.ml_scoring_service import MLScoringService
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

# Sized so ONE cohort clears `MIN_MATURED_FOR_MONITORING` (500). At 260
# borrowers the monitoring assertion skipped, which is the same as not having
# it — a gate nobody runs proves nothing.
CFG = LedgerConfig(n_borrowers=900, months=20, seed=23)
AS_OF_DAY = 400
HORIZON = 30

engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def lifecycle():
    """Score one cohort, advance 30 days, label it — the production sequence."""
    create_schema(bind=engine)
    prev = settings.ML_MODEL_VERSION
    settings.ML_MODEL_VERSION = "1.2.0-ledger"
    DecisionEngine.clear_cache()
    if DecisionEngine.get("recovery_risk") is None:
        settings.ML_MODEL_VERSION = prev
        DecisionEngine.clear_cache()
        pytest.skip("no 1.2.0-ledger artifact; run phase 2 first")

    ledger = LedgerSimulator(CFG).run(intercept=-4.1562)
    db = Session()
    mat = Materialiser(ledger, CFG)
    mat.load(db)

    mat.rewind_to(db, AS_OF_DAY)
    as_of = CFG.start_date + timedelta(days=AS_OF_DAY)
    term = ledger.lifecycle[ledger.lifecycle.event != "OPENED"]
    closed = term.groupby("loan_id").day.min().to_dict()
    live = [r.loan_id for r in ledger.loans.itertuples()
            if r.opened_day <= AS_OF_DAY < closed.get(r.loan_id, 10 ** 9)]
    cases = [c for c in (db.query(Case).filter(Case.id == Materialiser.db_id("case", lid)).first()
                         for lid in live) if c is not None]
    _, rows = MLScoringService(db).score_cases_and_log(cases, as_of=as_of)
    db.commit()

    mature_day = AS_OF_DAY + HORIZON + 1
    mat.rewind_to(db, mature_day)
    mature_as_of = CFG.start_date + timedelta(days=mature_day)
    summary = attach_outcomes(db, "recovery_risk", as_of=mature_as_of)
    try:
        yield ledger, db, mat, rows, summary, mature_as_of
    finally:
        db.close()
        drop_schema(bind=engine)
        settings.ML_MODEL_VERSION = prev
        DecisionEngine.clear_cache()


# ---------------------------------------------------------------------------
# 1. Predictions
# ---------------------------------------------------------------------------

def test_predictions_are_written_with_the_serving_version(lifecycle):
    _, db, _, rows, _, _ = lifecycle
    assert rows, "no predictions written"
    assert {r.model_version for r in rows} == {"1.2.0-ledger"}
    assert db.query(ModelPrediction).count() == len(rows)


def test_the_rollout_gate_chose_that_version_not_the_champion(lifecycle):
    """`ML_MODEL_VERSION` is what selected 1.2.0-ledger. Until 2026-09-09 the
    setting was inert and every request served the champion regardless."""
    from app.ml.pipeline import registry

    assert registry.resolve_version("recovery_risk", "champion") != "1.2.0-ledger"
    assert DecisionEngine.get("recovery_risk").version == "1.2.0-ledger"


def test_every_prediction_carries_a_baseline_frozen_at_as_of(lifecycle):
    """The baseline is what the OUTCOME is measured against, and both of its
    terms are overwritten in place on Loan. Read at labelling time it would be
    compared against a payment window those very payments already reduced."""
    _, db, _, rows, _, _ = lifecycle
    for r in rows[:50]:
        assert r.outcome_baseline is not None
        assert set(r.outcome_baseline) >= {"overdue_amount", "emi_amount"}
        assert r.as_of_date == (CFG.start_date + timedelta(days=AS_OF_DAY))


def test_the_frozen_baseline_is_not_the_balance_at_labelling_time(lifecycle):
    """The whole point of freezing. After 30 more days of payments the loan's
    overdue has moved, and at least some rows must show it."""
    _, db, _, rows, _, _ = lifecycle
    moved = 0
    for r in rows[:200]:
        loan = db.get(Loan, r.loan_id)                  # v2: loan_id is the loan's id, not its account number
        if abs(float(loan.overdue_amount) -
               float(r.outcome_baseline["overdue_amount"])) > 1.0:
            moved += 1
    assert moved > 0, "no balance moved over the horizon — the freeze is untested"


# ---------------------------------------------------------------------------
# 2-4. Outcomes, windows, statuses, censoring
# ---------------------------------------------------------------------------

def test_outcomes_are_attached_under_the_versioned_definition(lifecycle):
    _, db, _, _, summary, _ = lifecycle
    assert summary["labelled"] > 0
    laballed = db.query(ModelPrediction).filter(
        ModelPrediction.actual_outcome.isnot(None)).all()
    assert laballed
    assert {r.outcome_definition_version for r in laballed} == {
        OUTCOME_DEFINITION_VERSION}


def test_both_outcome_classes_occur(lifecycle):
    _, db, _, _, summary, _ = lifecycle
    by = summary["by_status"]
    assert by.get(OutcomeStatus.RECOVERED.value, 0) > 0
    assert by.get(OutcomeStatus.NOT_RECOVERED.value, 0) > 0


def test_the_payment_window_edges_are_where_outcomes_says(lifecycle):
    _, db, _, rows, _, _ = lifecycle
    start, end = payment_window(rows[0].as_of_date, HORIZON)
    assert start.date() == rows[0].as_of_date
    assert end.date() == rows[0].as_of_date + timedelta(days=HORIZON)
    # Half-open at the start: money on the observation day belongs to neither
    # the features nor the label.
    assert start.hour == 23 and start.minute == 59


def test_only_verified_money_counts_toward_the_outcome(lifecycle):
    """Reversed, rejected and pending receipts are money that did not stick."""
    _, db, _, rows, _, _ = lifecycle
    row = next(r for r in rows if r.outcome_baseline)
    start, end = payment_window(row.as_of_date, HORIZON)
    in_window = (db.query(Payment)
                 .filter(Payment.case_id == row.case_id,
                         Payment.payment_date > start,
                         Payment.payment_date <= end).all())
    verified = sum(float(p.amount) for p in in_window
                   if p.status is PaymentStatus.VERIFIED)
    res = evaluate(db, row, as_of=row.as_of_date + timedelta(days=HORIZON + 1))
    if res.status in (OutcomeStatus.RECOVERED, OutcomeStatus.NOT_RECOVERED):
        assert res.amount_paid == pytest.approx(round(verified, 2), abs=0.02)


def test_censoring_reaches_the_labeller_through_the_real_columns(lifecycle):
    """`censoring_status` reads `Loan.status`, `Case.status`,
    `Case.resolution_notes` and `Customer.tags`. If the materialiser did not
    rewind those, no prediction could ever be censored and the labeller's whole
    censoring branch would go untested."""
    _, db, _, _, summary, _ = lifecycle
    censored = {k: v for k, v in summary["by_status"].items()
                if k.startswith("CENSORED")}
    assert censored, summary["by_status"]
    # And the state really is on the rows, not just in the summary.
    assert db.query(Loan).filter(
        Loan.status.in_([LoanStatus.WRITTEN_OFF, LoanStatus.SETTLED])).count() > 0


def test_a_censored_prediction_gets_a_status_but_no_label(lifecycle):
    _, db, _, _, _, _ = lifecycle
    rows = (db.query(ModelPrediction)
            .filter(ModelPrediction.outcome_status.like("CENSORED%")).all())
    assert rows
    assert all(r.actual_outcome is None for r in rows)


# ---------------------------------------------------------------------------
# 5. Label comparison
# ---------------------------------------------------------------------------

def test_the_comparison_never_overwrites_the_model_label(lifecycle):
    _, db, _, _, _, mature_as_of = lifecycle
    before = {r.id: (r.actual_outcome, r.outcome_status)
              for r in db.query(ModelPrediction).all()}
    compare_all(db, "recovery_risk", as_of=mature_as_of)
    after = {r.id: (r.actual_outcome, r.outcome_status)
             for r in db.query(ModelPrediction).all()}
    assert before == after


def test_the_comparison_honours_the_label_that_was_attached(lifecycle):
    """It must compare the two LABELS, not re-derive one of them from state that
    has moved on. Re-deriving let a late write-off censor a row whose label was
    already committed — which in the Phase 4 replay dropped the comparable bad
    rate from 0.70 to 0.51 and fired the retrain trigger on a healthy book."""
    _, db, _, _, _, mature_as_of = lifecycle
    row = (db.query(ModelPrediction)
           .filter(ModelPrediction.actual_outcome.isnot(None)).first())
    assert row is not None
    res = compare(db, row, as_of=mature_as_of)
    assert res.model_status == row.outcome_status
    assert res.model_outcome == row.actual_outcome


def test_the_comparison_reports_every_cause_and_both_definitions(lifecycle):
    _, db, _, _, _, mature_as_of = lifecycle
    rep = compare_all(db, "recovery_risk", as_of=mature_as_of)
    from app.ml.pipeline.label_comparison import ALL_CAUSES

    assert set(rep["cause_breakdown"]) == set(ALL_CAUSES)
    assert rep["comparable_rows"] > 0
    assert rep["model_definition"]["labelled"] > 0
    assert rep["repayment_definition"]["labelled"] > 0
    # The model's bar is strictly stricter, so disagreement runs one way.
    assert rep["cause_breakdown"]["direction_anomaly"] == 0


# ---------------------------------------------------------------------------
# 6/10. Monitoring, and version isolation
# ---------------------------------------------------------------------------

def test_monitoring_runs_on_the_serving_version_and_current_definition(lifecycle):
    _, db, _, _, _, _ = lifecycle
    gate = mon.readiness(db, "recovery_risk")
    assert gate.model_version == "1.2.0-ledger"
    assert gate.outcome_definition_version == OUTCOME_DEFINITION_VERSION
    if not gate.ready:
        pytest.skip(f"only {gate.n_matured} matured in this small fixture")
    rep = mon.monitor_model(db, "recovery_risk", version=gate.model_version,
                            outcome_definition_version=gate.outcome_definition_version,
                            lookback_days=10_000)
    assert rep.version == "1.2.0-ledger"
    assert rep.outcome_definition_version == OUTCOME_DEFINITION_VERSION
    for k in ("auc_live", "gini_live", "ks_live", "brier_live",
              "calibration_gap", "bad_rate_live"):
        assert k in rep.performance


def test_a_foreign_model_version_cannot_reach_the_monitor(lifecycle):
    """Relabel some rows to another version and they must drop out of both the
    gate's count and the report — a model judged on another model's predictions
    is not being judged at all."""
    _, db, _, _, _, _ = lifecycle
    before = mon.readiness(db, "recovery_risk").n_matured
    victims = (db.query(ModelPrediction)
               .filter(ModelPrediction.actual_outcome.isnot(None)).limit(25).all())
    if len(victims) < 25:
        pytest.skip("not enough matured rows in this fixture")
    for v in victims:
        v.model_version = "9.9.9-other"
    db.commit()
    try:
        gate = mon.readiness(db, "recovery_risk")
        assert gate.n_matured == before - 25
        assert gate.n_excluded_other_model_version == 25
    finally:
        for v in victims:
            v.model_version = "1.2.0-ledger"
        db.commit()


def test_a_foreign_outcome_definition_cannot_reach_the_monitor(lifecycle):
    _, db, _, _, _, _ = lifecycle
    before = mon.readiness(db, "recovery_risk").n_matured
    victims = (db.query(ModelPrediction)
               .filter(ModelPrediction.actual_outcome.isnot(None)).limit(20).all())
    if len(victims) < 20:
        pytest.skip("not enough matured rows in this fixture")
    for v in victims:
        v.outcome_definition_version = "some-other-rule-2.0.0"
    db.commit()
    try:
        gate = mon.readiness(db, "recovery_risk")
        assert gate.n_matured == before - 20
        assert gate.n_excluded_other_outcome_version == 20
    finally:
        for v in victims:
            v.outcome_definition_version = OUTCOME_DEFINITION_VERSION
        db.commit()


def test_the_champion_is_untouched_by_the_whole_replay(lifecycle):
    """Whatever the pointer names (1.1.0 until 2026-09-16, 2.2.0 since), the
    replay scores 1.2.0-ledger by setting and never moves it."""
    from app.ml.pipeline import registry

    assert registry.resolve_version("recovery_risk", "champion") in {"1.1.0", "2.2.0"}
    assert registry.resolve_version("recovery_risk", "champion") != "1.2.0-ledger"
