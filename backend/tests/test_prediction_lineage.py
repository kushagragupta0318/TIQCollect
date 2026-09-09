"""A decision must name the exact prediction that informed it.

WHAT WENT WRONG. `model_predictions` carried no run linkage, and a re-plan
re-scores the whole pool and writes a fresh row per case. Measured on the live
demo book: nine allocation runs for plan_date 2026-09-10, 7-9 prediction rows
per case, and **201 of 214 allocated decisions matched MORE THAN ONE
prediction** on (case_id, agent_id). Thirteen were unambiguous.

Two consequences, both covered here. A decision could not be traced back to the
score that produced it. And once outcomes mature, the same account-day would
contribute eight or nine near-identical rows to the monitor -- inflating n and
correlating the errors, so a Gini would look better-estimated than it is.

None of these tests inspects source text.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.ml.pipeline import monitor as mon
from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION, OutcomeStatus
from app.models.allocation_decision import AllocationDecision
from app.models.model_prediction import ModelPrediction
from app.services.planner_service import PlannerService

# Reuse the planner harness that already builds a full manager/agent/case world.
from tests.test_planner_service import (  # noqa: F401
    client, db_session, setup_db, test_data,
)


def _plan(db, manager_id, **kw):
    return PlannerService(db, manager_id).plan_next_day(
        strategy="SMART", force_replan=True, **kw)


def _decisions(db, run_id):
    return db.query(AllocationDecision).filter(
        AllocationDecision.run_id == run_id).all()


@pytest.fixture
def ml_on(monkeypatch, db_session, test_data):
    """Force the planner down the ML path with deterministic probabilities.

    The point is the LINEAGE, not the model: a real engine would need an
    artifact in the test image, and a fixed probability makes the assertions
    about which row was linked rather than about what it said.
    """
    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", True)

    def fake_scores(self, cases):
        # Write real ModelPrediction rows, exactly as score_cases_and_log does,
        # so the lineage has something to point at.
        rows = []
        for c in cases:
            rows.append(ModelPrediction(
                model_name="recovery_risk", model_version="1.1.0",
                entity_type="case", entity_id=c.id,
                loan_id=c.loan_id, case_id=c.id,
                as_of_date=date.today(), probability=0.7,
                is_modelled=True, features={"dpd": 45.0},
                feature_coverage=1.0,
                outcome_baseline={"overdue_amount": 5000.0,
                                  "emi_amount": 2500.0, "threshold_ratio": 0.8},
            ))
        self.db.add_all(rows)
        self._ml_prediction_rows = rows
        return {c.id: 0.3 for c in cases}

    monkeypatch.setattr(PlannerService, "_ml_recovery_probabilities", fake_scores)
    return test_data


# ---------------------------------------------------------------------------
# 1 / 2. Lineage is unambiguous, and survives repeated planning
# ---------------------------------------------------------------------------

def test_a_decision_names_the_prediction_that_informed_it(db_session, ml_on):
    run = _plan(db_session, ml_on["manager"].id)
    db_session.commit()

    decs = _decisions(db_session, run.id)
    assert decs, "no decisions written"
    stamped = [d for d in decs if d.model_prediction_id]
    assert stamped, "no decision carries a prediction id"

    for d in stamped:
        mp = db_session.query(ModelPrediction).filter(
            ModelPrediction.id == d.model_prediction_id).one()
        # The link must point at THIS case's score, not merely at some row.
        assert mp.case_id == d.case_id


def test_repeated_planning_leaves_each_decision_pointing_at_its_own_run(
        db_session, ml_on):
    """THE DEFECT, reproduced and closed.

    Three re-plans for the same date produce three prediction rows per case.
    Before the lineage column, resolving a decision by (case_id, agent_id) hit
    all three. Now each run's decisions point at the rows that run wrote.
    """
    runs = []
    for _ in range(3):
        runs.append(_plan(db_session, ml_on["manager"].id))
        db_session.commit()

    all_preds = db_session.query(ModelPrediction).all()
    per_case: dict[str, int] = {}
    for p in all_preds:
        per_case[p.case_id] = per_case.get(p.case_id, 0) + 1
    assert max(per_case.values()) >= 3, (
        "fixture did not re-score, so the ambiguity is not being tested")

    # The old, ambiguous resolution: by case alone.
    ambiguous = sum(1 for n in per_case.values() if n > 1)
    assert ambiguous > 0

    # The new one: exactly one prediction per decision, and it belongs to a
    # DIFFERENT row for each run.
    seen_ids = set()
    for run in runs:
        stamped = [d for d in _decisions(db_session, run.id)
                   if d.model_prediction_id]
        assert stamped
        ids = {d.model_prediction_id for d in stamped}
        assert not (ids & seen_ids), (
            "two runs' decisions point at the same prediction rows — the "
            "lineage is not per-run")
        seen_ids |= ids


def test_the_latest_decision_resolves_to_exactly_one_prediction(db_session, ml_on):
    for _ in range(3):
        run = _plan(db_session, ml_on["manager"].id)
        db_session.commit()

    for d in _decisions(db_session, run.id):
        if not d.model_prediction_id:
            continue
        n = db_session.query(ModelPrediction).filter(
            ModelPrediction.id == d.model_prediction_id).count()
        assert n == 1


def test_every_scored_case_gets_lineage_not_only_the_allocated_ones(
        db_session, ml_on):
    """A deferred or blocked case was still scored. "The model said X and we
    held the case anyway" is exactly what somebody will want to audit."""
    run = _plan(db_session, ml_on["manager"].id)
    db_session.commit()

    scored = {p.case_id for p in db_session.query(ModelPrediction).all()}
    for d in _decisions(db_session, run.id):
        if d.case_id in scored:
            assert d.model_prediction_id is not None, (
                f"{d.outcome} decision for a scored case has no lineage")


# ---------------------------------------------------------------------------
# 3. Exploration swaps stay honest
# ---------------------------------------------------------------------------

def test_an_exploration_swap_keeps_the_prediction_and_moves_the_agent(
        db_session, ml_on, monkeypatch):
    """A swap changes WHO visits, not what the model said about the borrower.

    The probability is borrower-side and agent-independent, so the same
    prediction did inform the decision. What must follow the swap is the
    allocated agent and the prediction's own agent stamp — otherwise a
    randomised visit would be credited to the agent who never made it, which is
    precisely the propensity the epsilon-greedy slice exists to keep honest.

    DRIVEN DETERMINISTICALLY rather than by turning the exploration rate up and
    hoping. The fixture has two agents and a handful of cases, so a real swap is
    not guaranteed to occur — and a test that skips when it does not proves
    nothing. `allocate` is replaced with one that returns a KNOWN swap: the case
    is assigned to agent B while the decision records it as moved from agent A.
    """
    from app.models.allocation_decision import AllocationOutcome
    from app.services import global_allocator as ga

    a_from, a_to = ml_on["agent1"], ml_on["agent2"]

    def fake_allocate(self, *, cases, agents, **kw):
        case = cases[0]
        decisions = [AllocationDecision(
            id=str(uuid.uuid4()), run_id="", case_id=case.id,
            previous_agent_id=case.agent_id,
            # The FINAL agent, after the swap.
            allocated_agent_id=a_to.id,
            outcome=AllocationOutcome.ALLOCATED.value,
            reason="swapped for exploration",
            visit_priority_score=1.0, fit_score=1.0,
            score_breakdown={
                "exploration": True,
                "exploration_from_agent": a_from.id,
                "exploration_propensity": 0.5,
                "ml_used_for_decision": True,
                "prob_recovery_ml": 0.3,
            },
        )]
        return {a_to.id: [case]}, decisions, 100.0

    monkeypatch.setattr(ga.GlobalAllocator, "allocate", fake_allocate)
    run = _plan(db_session, ml_on["manager"].id)
    db_session.commit()

    decs = [d for d in _decisions(db_session, run.id)
            if (d.score_breakdown or {}).get("exploration") is True]
    assert decs, "the controlled swap did not reach the persisted decisions"
    d = decs[0]

    assert d.model_prediction_id, "a swapped decision lost its lineage"
    mp = db_session.query(ModelPrediction).filter(
        ModelPrediction.id == d.model_prediction_id).one()

    # Same borrower, same score — the swap does not invalidate the prediction.
    assert mp.case_id == d.case_id

    # The agent stamp follows the SWAP, not the pairing the solve first chose.
    assert d.allocated_agent_id == a_to.id
    assert mp.agent_id == a_to.id
    assert mp.agent_id != a_from.id, (
        "the prediction is credited to the agent the case was moved AWAY from")


def test_the_prediction_agent_stamp_matches_the_allocated_agent(db_session, ml_on):
    run = _plan(db_session, ml_on["manager"].id)
    db_session.commit()
    for d in _decisions(db_session, run.id):
        if not (d.allocated_agent_id and d.model_prediction_id):
            continue
        mp = db_session.query(ModelPrediction).filter(
            ModelPrediction.id == d.model_prediction_id).one()
        assert mp.agent_id == d.allocated_agent_id


# ---------------------------------------------------------------------------
# 4. Monitoring cannot multiply-count re-scores
# ---------------------------------------------------------------------------

def _matured(db, case_id, as_of, prob, outcome=1, version="1.1.0"):
    db.add(ModelPrediction(
        id=str(uuid.uuid4()), model_name="recovery_risk", model_version=version,
        entity_type="case", entity_id=case_id, case_id=case_id,
        as_of_date=as_of, probability=prob, is_modelled=True,
        features={"dpd": 40.0}, actual_outcome=outcome,
        outcome_status=OutcomeStatus.NOT_RECOVERED.value,
        outcome_definition_version=OUTCOME_DEFINITION_VERSION,
    ))


def test_readiness_counts_account_days_not_rows(db_session):
    """Nine re-plans of one case is one observation, not nine.

    Otherwise a manager clicking "Re-Plan" repeatedly walks the monitor over its
    own 500-row readiness threshold without a single new borrower.
    """
    today = date.today()
    for i in range(40):
        for _ in range(9):                       # nine re-scores of the same day
            _matured(db_session, f"case-{i}", today, 0.5)
    db_session.commit()

    gate = mon.readiness(db_session, "recovery_risk", version="1.1.0")
    assert db_session.query(ModelPrediction).count() == 360
    assert gate.n_matured == 40, (
        f"counted {gate.n_matured} — re-scores are being counted as "
        f"independent observations")


def test_the_monitor_frame_holds_one_row_per_account_day(db_session):
    today = date.today()
    for i in range(600):
        _matured(db_session, f"c{i}", today, (i % 100) / 100.0,
                 outcome=1 if i % 100 >= 30 else 0)
        _matured(db_session, f"c{i}", today, (i % 100) / 100.0,
                 outcome=1 if i % 100 >= 30 else 0)          # duplicate re-score
    db_session.commit()

    rep = mon.monitor_model(db_session, "recovery_risk", version="1.1.0",
                            lookback_days=10_000)
    assert db_session.query(ModelPrediction).count() == 1200
    assert rep.n_predictions == 600
    assert rep.n_matured == 600
    assert rep.excluded["duplicate_rescores"] == 600


def test_a_rescore_on_a_LATER_day_is_a_new_observation(db_session):
    """Dedup is per account-DAY. The same case scored tomorrow is genuinely new
    information and must not be collapsed into today's row."""
    today = date.today()
    for i in range(30):
        _matured(db_session, f"x{i}", today, 0.4)
        _matured(db_session, f"x{i}", today - timedelta(days=1), 0.4)
    db_session.commit()

    gate = mon.readiness(db_session, "recovery_risk", version="1.1.0")
    assert gate.n_matured == 60


def test_dedup_keeps_the_last_scored_row(db_session):
    """The final plan used the last score, so that is the one to judge."""
    today = date.today()
    _matured(db_session, "only-case", today, 0.11)
    db_session.commit()
    _matured(db_session, "only-case", today, 0.99)
    db_session.commit()

    df, excluded = mon._load(db_session, "recovery_risk", "1.1.0", None,
                             OUTCOME_DEFINITION_VERSION)
    assert len(df) == 1
    assert df.probability.iloc[0] == 0.99
    assert excluded["duplicate_rescores"] == 1
