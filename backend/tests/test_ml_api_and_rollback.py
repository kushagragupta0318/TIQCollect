"""The API's ML contract, and a rollback that is actually EXECUTED.

Two gaps the final audit could not close from source alone:

  * the allocation response served ML-DERIVED numbers while exposing nothing
    that said so, and nothing that let a client trace one back to its score;
  * "the rollback endpoint exists" was the only evidence rollback worked.

Both are closed here by running the real code against a real database, not by
reading it. The rollback runs inside the test session's transaction, so it
exercises the production code path and the same DB operations without touching
anything a person is looking at.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.config import settings
from app.models.allocation_decision import AllocationDecision
from app.models.allocation_run import AllocationRun
from app.models.beat import Beat
from app.models.model_prediction import ModelPrediction
from app.services.planner_service import PlannerService

from app.core.security import create_access_token
from tests._db import test_id
from tests.test_planner_service import (  # noqa: F401
    client, db_session, setup_db, test_data,
)


@pytest.fixture
def auth(test_data):
    """The manager's own bearer token — every endpoint here is tenant-scoped."""
    mgr = test_data["manager"]
    token = create_access_token(user_id=mgr.id, role=mgr.role.value,
                                device_id="test_device_ml")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ml_plan(monkeypatch, db_session, test_data):
    """A real SMART plan with the model driving it, and real prediction rows."""
    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", True)

    def fake_scores(self, cases):
        rows = [ModelPrediction(
            model_name="recovery_risk", model_version="1.1.0",
            artifact_sha256="deadbeef" * 8,
            entity_type="case", entity_id=c.id, loan_id=c.loan_id, case_id=c.id,
            as_of_date=date.today(), probability=0.72, is_modelled=True,
            features={"dpd": 45.0}, feature_coverage=1.0,
            outcome_baseline={"overdue_amount": 5000.0, "emi_amount": 2500.0,
                              "threshold_ratio": 0.8},
        ) for c in cases]
        self.db.add_all(rows)
        self._ml_prediction_rows = rows
        return {c.id: 0.28 for c in cases}

    monkeypatch.setattr(PlannerService, "_ml_recovery_probabilities", fake_scores)
    run = PlannerService(db_session, test_data["manager"].id).plan_next_day(
        strategy="SMART", force_replan=True)
    db_session.commit()
    return run


def _plan_payload(client, auth):
    r = client.get("/api/v1/manager/allocation/latest", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. The API exposes the ML that drove the decision
# ---------------------------------------------------------------------------

def test_the_response_carries_an_ml_block_on_every_decision(client, auth, ml_plan):
    body = _plan_payload(client, auth)
    assert body["has_plan"] is True
    decisions = body["decisions"]
    assert decisions
    for d in decisions:
        assert "ml" in d, "a decision reached the client with no ML block"
        assert set(d["ml"]) == {
            "used_for_decision", "probability_used", "borrower_p_recover",
            "shadow_prob_recovery", "value_transform", "expected_recovery_inr",
            "prediction_id", "model_name", "model_version", "feature_coverage",
        }


def test_the_ml_block_matches_the_persisted_decision(client, auth, db_session, ml_plan):
    """Not a plausible-looking block — the SAME values that are on the row."""
    body = _plan_payload(client, auth)
    for d in body["decisions"]:
        row = db_session.query(AllocationDecision).filter(
            AllocationDecision.id == d["decision_id"]).one()
        bd = row.score_breakdown or {}
        assert d["ml"]["used_for_decision"] == bool(bd.get("ml_used_for_decision"))
        assert d["ml"]["probability_used"] == bd.get("prob_recovery_ml")
        assert d["ml"]["expected_recovery_inr"] == bd.get("expected_case_inr")
        assert d["ml"]["shadow_prob_recovery"] == bd.get("prob_recovery")
        assert d["ml"]["prediction_id"] == row.model_prediction_id


def test_the_version_comes_off_the_prediction_row_not_a_constant(
        client, auth, db_session, ml_plan):
    """A hardcoded version would keep reporting the old model through a
    rollback, which is the one moment somebody relies on it."""
    body = _plan_payload(client, auth)
    served = [d for d in body["decisions"] if d["ml"]["prediction_id"]]
    assert served
    for d in served:
        mp = db_session.query(ModelPrediction).filter(
            ModelPrediction.id == d["ml"]["prediction_id"]).one()
        assert d["ml"]["model_version"] == mp.model_version
        assert d["ml"]["model_name"] == mp.model_name
        assert d["ml"]["feature_coverage"] == mp.feature_coverage

    # Change the stored version; the API must follow it.
    mp = db_session.query(ModelPrediction).filter(
        ModelPrediction.id == served[0]["ml"]["prediction_id"]).one()
    mp.model_version = "9.9.9-rolled-back"
    db_session.commit()
    again = _plan_payload(client, auth)
    moved = next(d for d in again["decisions"]
                 if d["ml"]["prediction_id"] == mp.id)
    assert moved["ml"]["model_version"] == "9.9.9-rolled-back"


def test_the_probability_used_is_null_when_the_model_did_not_drive_it(
        client, auth, db_session, ml_plan):
    """A client must not be able to mistake the shadow value for the live one."""
    row = db_session.query(AllocationDecision).first()
    bd = dict(row.score_breakdown or {})
    bd["ml_used_for_decision"] = False
    bd["prob_recovery_ml"] = None
    bd["prob_recovery"] = 0.85
    row.score_breakdown = bd
    db_session.commit()

    body = _plan_payload(client, auth)
    d = next(x for x in body["decisions"] if x["decision_id"] == row.id)
    assert d["ml"]["used_for_decision"] is False
    assert d["ml"]["probability_used"] is None
    assert d["ml"]["shadow_prob_recovery"] == 0.85


def test_the_expected_recovery_the_api_serves_is_the_one_the_model_produced(
        client, auth, db_session, ml_plan):
    """The number the UI renders must be reproducible from the probability the
    API reports beside it — the defect that made the panel overstate by 6x."""
    body = _plan_payload(client, auth)
    checked = 0
    for d in body["decisions"]:
        ml = d["ml"]
        if not (ml["used_for_decision"] and ml["expected_recovery_inr"]):
            continue
        row = db_session.query(AllocationDecision).filter(
            AllocationDecision.id == d["decision_id"]).one()
        case = row.case
        remaining = max(0.0, float(case.target_amount or 0)
                        - float(case.collected_amount or 0))
        if remaining <= 0:
            continue
        implied = ml["expected_recovery_inr"] / remaining
        assert abs(implied - ml["probability_used"]) < 0.002, (
            f"the rupee figure does not follow from the probability reported "
            f"with it: implied {implied}, reported {ml['probability_used']}")
        checked += 1
    assert checked, "no ML-driven decision with a collectable balance to check"


# ---------------------------------------------------------------------------
# 2. Rollback, EXECUTED
# ---------------------------------------------------------------------------

def test_rollback_removes_the_plan_and_leaves_no_orphans(
        client, auth, db_session, ml_plan):
    """Executed end to end against the database, not asserted from source."""
    run_id = ml_plan.id
    assert db_session.query(Beat).filter(
        Beat.allocation_run_id == run_id).count() > 0
    assert db_session.query(AllocationDecision).filter(
        AllocationDecision.run_id == run_id).count() > 0

    r = client.post("/api/v1/manager/allocation/rollback",
                    json={"run_id": run_id}, headers=auth)
    assert r.status_code == 200, r.text
    db_session.expire_all()

    # The beats it created are gone...
    assert db_session.query(Beat).filter(
        Beat.allocation_run_id == run_id).count() == 0
    # ...and no beat anywhere still points at the rolled-back run.
    assert db_session.query(Beat).filter(
        Beat.allocation_run_id == run_id).all() == []
    # The run itself survives as an audit record rather than vanishing.
    run = db_session.query(AllocationRun).filter(
        AllocationRun.id == run_id).first()
    assert run is not None, "the run was deleted; the audit trail is gone"


def test_the_api_reflects_the_rolled_back_state(client, auth, db_session, ml_plan):
    """What the frontend would fetch after a rollback — the same endpoint the
    page calls, so the UI cannot show a plan the database no longer has."""
    before = _plan_payload(client, auth)
    assert before["has_plan"] is True
    assert before["beats"]

    client.post("/api/v1/manager/allocation/rollback",
                json={"run_id": ml_plan.id}, headers=auth)
    db_session.expire_all()

    after = _plan_payload(client, auth)
    assert not after.get("beats"), (
        "the API still serves beats for a rolled-back plan — the page would "
        "render a plan that no longer exists")


def test_ml_does_not_keep_affecting_a_rolled_back_plan(
        client, auth, db_session, ml_plan):
    """Predictions are append-only history and MUST survive. What must not
    survive is their influence: no live beat may still be built from them."""
    preds_before = db_session.query(ModelPrediction).count()
    assert preds_before > 0

    client.post("/api/v1/manager/allocation/rollback",
                json={"run_id": ml_plan.id}, headers=auth)
    db_session.expire_all()

    # History intact.
    assert db_session.query(ModelPrediction).count() == preds_before
    # Influence gone.
    assert db_session.query(Beat).filter(
        Beat.allocation_run_id == ml_plan.id).count() == 0


def test_a_normal_plan_still_works_after_a_rollback(
        client, auth, db_session, ml_plan, test_data, monkeypatch):
    """The state a rollback leaves behind must be plannable again."""
    client.post("/api/v1/manager/allocation/rollback",
                json={"run_id": ml_plan.id}, headers=auth)
    db_session.expire_all()

    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", True)
    fresh = PlannerService(db_session, test_data["manager"].id).plan_next_day(
        strategy="SMART", force_replan=True)
    db_session.commit()

    assert fresh.id != ml_plan.id
    assert db_session.query(Beat).filter(
        Beat.allocation_run_id == fresh.id).count() > 0
    body = _plan_payload(client, auth)
    assert body["has_plan"] is True
    assert body["run_id"] == fresh.id


def test_rollback_refuses_a_run_that_is_not_the_managers(
        client, auth, db_session, ml_plan):
    """A rollback is destructive; tenancy must hold on it."""
    other = AllocationRun(
        id=test_id("run-someone-else"), manager_user_id=test_id("another-manager"),
        plan_date=date.today(), strategy="SMART", status="PLANNED",
        total_cases_evaluated=0, total_cases_allocated=0,
        total_cases_deferred=0, total_cases_blocked=0,
        total_agents_planned=0, expected_recovery_total=0.0,
    )
    db_session.add(other)
    db_session.commit()

    r = client.post("/api/v1/manager/allocation/rollback",
                    json={"run_id": test_id("run-someone-else")}, headers=auth)
    # 400 "not found" is the scoped lookup refusing to see it, which is the
    # right shape: the endpoint does not confirm another agency's run exists.
    assert r.status_code in (400, 403, 404), (
        f"another manager's run was rollback-able: {r.status_code} {r.text}")
    assert "not found" in r.text.lower() or r.status_code in (403, 404)
    # And it is genuinely untouched.
    still = db_session.query(AllocationRun).filter(
        AllocationRun.id == test_id("run-someone-else")).one()
    assert still.status == "PLANNED"
