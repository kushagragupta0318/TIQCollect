"""The monitoring verdict and the approval gate, over HTTP.

Two things the 2026-09-09 audit found had no surface at all: the monitoring
verdict (which lived in worker logs) and the candidate lifecycle (which did not
exist). Both are asserted here through the authenticated manager routes a person
would actually use, not by calling the functions underneath them.

THE ENDPOINT MUST NEVER READ AS HEALTHY ON ABSENT EVIDENCE. That is the single
most important assertion in this file: `not_ready` beside a development Gini of
0.5136 is exactly the shape of report somebody would act on.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.security import create_access_token
from app.ml.pipeline import registry
from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION, OutcomeStatus
from app.models.audit_log import AuditAction, AuditLog
from app.models.model_candidate import CandidateState, ModelCandidate
from app.models.model_prediction import ModelPrediction

from tests.test_planner_service import (  # noqa: F401
    client, db_session, setup_db, test_data,
)

# 2026-09-16 — read from the pointer; see tests/_served_champion.py.
from tests._served_champion import served_vector, serving_version  # noqa: E402

SERVING = serving_version()


@pytest.fixture
def auth(test_data):
    mgr = test_data["manager"]
    return {"Authorization": f"Bearer {create_access_token(
        user_id=mgr.id, role=mgr.role.value, device_id='ml-lifecycle')}"}


@pytest.fixture
def champion_guard():
    pointer = registry.ARTIFACT_ROOT / "recovery_risk" / "champion.txt"
    before = pointer.read_text() if pointer.exists() else None
    yield before
    after = pointer.read_text() if pointer.exists() else None
    assert after == before, f"the champion pointer moved: {before!r} -> {after!r}"


def _matured(db, n, *, outcome_fn, as_of=None):
    as_of = as_of or date.today() - timedelta(days=40)
    for i in range(n):
        p = (i % 100) / 100.0
        row = ModelPrediction(
            id=str(uuid.uuid4()), model_name="recovery_risk", model_version=SERVING,
            entity_type="case", entity_id=f"lc{i}", case_id=f"lc{i}",
            as_of_date=as_of, probability=p, is_modelled=True,
            feature_coverage=1.0,
            features=served_vector(i, dpd=40.0, cibil_score=600.0,
                                   ptp_kept_ratio=0.5, overdue_amount=5000.0),
            outcome_baseline={"overdue_amount": 5000.0, "emi_amount": 2500.0,
                              "threshold_ratio": 0.8},
        )
        y = outcome_fn(i, p)
        row.actual_outcome = y
        row.outcome_status = (OutcomeStatus.NOT_RECOVERED.value if y
                              else OutcomeStatus.RECOVERED.value)
        row.outcome_definition_version = OUTCOME_DEFINITION_VERSION
        db.add(row)
    db.commit()


def _health(client, auth):
    r = client.get("/api/v1/manager/ml/health", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# The health endpoint's states
# ---------------------------------------------------------------------------

def test_not_ready_is_reported_as_not_ready_and_never_as_healthy(
        client, auth, db_session, champion_guard):
    body = _health(client, auth)
    mon = body["monitoring"]

    assert mon["status"] == "not_ready"
    assert mon["verdict"] is None
    assert mon["retrain_recommended"] is False
    assert mon["n_matured"] == 0
    assert mon["required_matured"] == 500
    assert mon["performance"] == {} and mon["stability"] == {}
    assert "no conclusion about model health" in mon["note"]
    # The word that must not appear.
    assert mon["status"] != "healthy"


def test_the_development_metrics_are_not_replaced_by_production_ones(
        client, auth, db_session, champion_guard):
    """A development Gini presented as a live one is the number somebody acts
    on. Both are present and they are in different places."""
    body = _health(client, auth)
    models = {m["model"]: m for m in (body.get("models") or [])}
    assert models, "health no longer reports the artifacts at all"
    dev = models["recovery_risk"]["metrics_oot"]
    assert dev["gini"] and dev["ks"], "development metrics were dropped"
    assert "gini" not in body["monitoring"]
    assert body["monitoring"]["performance"] == {}


def test_a_healthy_production_cohort_reports_healthy_with_live_metrics(
        client, auth, db_session, champion_guard):
    # Calibrated and well ordered: within each probability level, that share
    # of rows is bad.
    buckets: dict[float, list[int]] = {}
    for i in range(700):
        buckets.setdefault((i % 100) / 100.0, []).append(i)
    order = {i: j for p, idx in buckets.items() for j, i in enumerate(idx)}
    _matured(db_session, 700,
             outcome_fn=lambda i, p: 1 if order[i] < round(p * 7) else 0)

    mon = _health(client, auth)["monitoring"]
    assert mon["status"] == "healthy" and mon["verdict"] == "healthy"
    assert mon["retrain_recommended"] is False
    assert mon["n_matured"] == 700
    assert mon["n_classes"] is None or mon["n_classes"] == 2
    for k in ("gini_live", "ks_live", "brier_live", "calibration_gap"):
        assert k in mon["performance"]
    assert mon["thresholds"]["gini_relative_drop"] == 0.20
    assert mon["thresholds"]["min_matured"] == 500


def test_a_one_class_cohort_is_its_own_state_not_healthy_and_not_too_few_rows(
        client, auth, db_session, champion_guard):
    _matured(db_session, 700, outcome_fn=lambda i, p: 1)

    mon = _health(client, auth)["monitoring"]
    assert mon["status"] == "insufficient_outcome_variation"
    assert mon["n_matured"] == 700
    assert mon["n_classes"] == 1
    assert mon["bad_rate_live"] == 1.0
    assert mon["retrain_recommended"] is False
    assert mon["performance"] == {}
    assert "ONE class" in " ".join(mon["retrain_reasons"])


def test_a_degraded_cohort_reports_retrain_recommended_with_its_reasons(
        client, auth, db_session, champion_guard):
    # Uncorrelated with the score: Gini ~0 against a development 0.5136.
    _matured(db_session, 700, outcome_fn=lambda i, p: i % 2)

    mon = _health(client, auth)["monitoring"]
    assert mon["status"] == "retrain_recommended"
    assert mon["retrain_recommended"] is True
    assert mon["retrain_reasons"]
    assert any("Gini" in r for r in mon["retrain_reasons"])
    assert mon["performance"]["gini_live"] is not None


def test_health_reports_what_this_process_is_serving_against_the_pointer(
        client, auth, db_session, champion_guard):
    # Earlier tests in this process load 1.1.0 by explicit version (the
    # scorecard-mechanics tests); a cache holding it beside the champion is
    # exactly the split-fleet state this endpoint exists to expose, but here
    # it is test pollution, not the process under test. Start clean.
    from app.ml.pipeline.engine import DecisionEngine
    DecisionEngine.clear_cache()
    serving = _health(client, auth)["serving"]["recovery_risk"]
    assert serving["pointer_version"] == champion_guard.strip()
    assert serving["serving_matches_pointer"] is True
    assert ":" in serving["instance"], "the answer must name the process"


def test_health_requires_a_manager(client, db_session):
    assert client.get("/api/v1/manager/ml/health").status_code in (401, 403)


# ---------------------------------------------------------------------------
# The candidate + approval routes
# ---------------------------------------------------------------------------

def _candidate(db, state=CandidateState.PENDING_APPROVAL, **over):
    cand = ModelCandidate(
        id=str(uuid.uuid4()), model_name="recovery_risk",
        candidate_version="1.1.0-candidate-20261008120000",
        state=state, monitoring_run_id=f"mon-{uuid.uuid4().hex[:12]}",
        trigger_reasons=["Gini has fallen 31% below its development value"],
        cohort_rows=2600, outcome_definition_version=OUTCOME_DEFINITION_VERSION,
        gate_results=[{"gate": "gini_min", "observed": 0.44,
                       "threshold": ">= 0.25", "result": "PASS"}],
        comparison_results={"verdict": "challenger_better", "gini_uplift": 0.061},
        state_history=[],
        **{"gates_passed": True, "comparison_passed": True,
           "gini_uplift": 0.061, "incumbent_version": SERVING, **over})
    db.add(cand); db.commit()
    return cand


def test_the_candidate_list_is_the_durable_retraining_report(
        client, auth, db_session, champion_guard):
    cand = _candidate(db_session)
    r = client.get("/api/v1/manager/ml/candidates", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    row = body["candidates"][0]
    # Everything the brief asks a retraining report to carry.
    for field in ("candidate_id", "state", "monitoring_run_id", "trigger_reasons",
                  "incumbent_version", "candidate_version", "training_cohort",
                  "gate_results", "gates_passed", "comparison_results",
                  "comparison_passed", "gini_uplift", "state_history"):
        assert field in row, f"the report omits {field}"
    assert row["candidate_id"] == cand.id


def test_the_detail_route_shows_whether_the_candidate_has_gone_stale(
        client, auth, db_session, champion_guard):
    cand = _candidate(db_session)
    r = client.get(f"/api/v1/manager/ml/candidates/{cand.id}", headers=auth)
    body = r.json()
    assert body["current_champion"] == champion_guard.strip()
    assert body["is_stale"] is False

    cand.incumbent_version = "0.0.1-gone"
    db_session.commit()
    body = client.get(f"/api/v1/manager/ml/candidates/{cand.id}",
                      headers=auth).json()
    assert body["is_stale"] is True


def test_approval_over_http_records_a_person_and_an_audit_row(
        client, auth, db_session, test_data, champion_guard):
    cand = _candidate(db_session)
    r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/approve",
                    headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "APPROVED"
    assert r.json()["decided_by_id"] == test_data["manager"].id

    row = (db_session.query(AuditLog)
           .filter(AuditLog.action == AuditAction.MODEL_CANDIDATE_APPROVED)
           .one())
    assert row.entity_id == cand.id
    assert row.details["version"] == cand.candidate_version
    # Approval alone must not move the pointer.
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_rejection_over_http_leaves_the_champion_alone(
        client, auth, db_session, champion_guard):
    cand = _candidate(db_session)
    r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/reject",
                    headers=auth, params={"note": "want another cycle"})
    assert r.status_code == 200
    assert r.json()["state"] == "REJECTED_BY_HUMAN"
    assert db_session.query(AuditLog).filter(
        AuditLog.action == AuditAction.MODEL_CANDIDATE_REJECTED).count() == 1
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_a_rejected_candidate_cannot_be_promoted_over_http(
        client, auth, db_session, champion_guard):
    cand = _candidate(db_session, state=CandidateState.REJECTED_VALIDATION,
                      gates_passed=False, comparison_passed=None)
    r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/promote",
                    headers=auth)
    assert r.status_code == 409, r.text
    assert "APPROVED" in r.json()["detail"]
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_the_same_manager_cannot_approve_and_then_promote_over_http(
        client, auth, db_session, champion_guard, test_data):
    """Four eyes, at the surface a person actually uses.

    Approve and promote are separate endpoints, so before 2026-09-10 one manager
    could hit both in sequence and the "second checkpoint" was the same judgement
    twice. The rule lives in lifecycle.promote; this asserts the HTTP contract it
    produces — 409, not 500, because the request was valid and the AUTHORITY was
    not.
    """
    cand = _candidate(db_session, state=CandidateState.PENDING_APPROVAL)
    ok = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/approve",
                     headers=auth, json={"note": "looks good"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["decided_by_id"] == test_data["manager"].id

    r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/promote",
                    headers=auth)
    assert r.status_code == 409, r.text
    assert "four-eyes" in r.json()["detail"]
    # The pointer is the thing that matters — a refused promotion that still
    # moved the champion would be the worst possible outcome.
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()

    from app.models.model_candidate import CandidateState as _CS
    db_session.refresh(cand)
    assert cand.state == _CS.APPROVED, "state advanced on a refused promotion"
    assert cand.promoted_at is None


def test_a_pending_candidate_cannot_be_promoted_without_approval(
        client, auth, db_session, champion_guard):
    """The gate the whole design exists for: no path from trained to live that
    does not pass through a person."""
    cand = _candidate(db_session, state=CandidateState.PENDING_APPROVAL)
    r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/promote",
                    headers=auth)
    assert r.status_code == 409
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_a_stale_approval_is_refused_over_http(client, auth, db_session,
                                               champion_guard):
    cand = _candidate(db_session, incumbent_version="0.0.1-gone")
    r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/approve",
                    headers=auth)
    assert r.status_code == 409
    assert "stale" in r.json()["detail"]
    assert registry.pointer_version("recovery_risk") == champion_guard.strip()


def test_the_lifecycle_routes_require_a_manager(client, db_session):
    cand = _candidate(db_session)
    for path in (f"/api/v1/manager/ml/candidates",
                 f"/api/v1/manager/ml/candidates/{cand.id}"):
        assert client.get(path).status_code in (401, 403)
    for verb in ("approve", "reject", "promote"):
        r = client.post(f"/api/v1/manager/ml/candidates/{cand.id}/{verb}")
        assert r.status_code in (401, 403), f"{verb} was reachable unauthenticated"
    assert cand.state == CandidateState.PENDING_APPROVAL


def test_an_unknown_candidate_is_a_404_not_a_500(client, auth, db_session):
    assert client.get("/api/v1/manager/ml/candidates/nope",
                      headers=auth).status_code == 404


def test_the_ml_routes_return_no_tenant_identifiers(client, auth, db_session,
                                                    champion_guard):
    """The behavioural half of the tenancy exemption.

    `/ml/health` and `/ml/candidates` are in `tenant_free` in
    test_manager_endpoints.py because they return model-level state, not
    anybody's rows. That is a claim about CONTENT, and the structural sweep is
    textual — it can only see that the routes do not mention scoping, never that
    what they return is safe. This test reads the payloads.

    The exemption is real: `model_candidates` has no tenant column at all, and
    the monitoring block returns counts, metrics and a verdict. If a future edit
    starts returning per-case rows (a list of the worst-scoring borrowers, say),
    this fails and the exemption has to be revisited rather than inherited.
    """
    import json
    import re

    _matured(db_session, 700, outcome_fn=lambda i, p: i % 2)
    cand = _candidate(db_session)
    cand.training_cohort = {"n_rows": 2600, "bad_rate": 0.71}
    db_session.commit()

    payloads = [
        _health(client, auth),
        client.get("/api/v1/manager/ml/candidates", headers=auth).json(),
        client.get(f"/api/v1/manager/ml/candidates/{cand.id}",
                   headers=auth).json(),
    ]
    # Keys that would mean a borrower, an agent or a case had leaked through.
    forbidden = {"case_id", "agent_id", "customer_id", "loan_id", "entity_id",
                 "borrower_id", "case_number", "loan_account_number",
                 "phone_primary", "full_name", "manager_user_id"}
    for body in payloads:
        blob = json.dumps(body)
        found = {k for k in forbidden if re.search(rf'"{k}"\s*:', blob)}
        assert not found, f"a tenant identifier reached an ML route: {found}"

    # And the monitoring block really is aggregate: counts and metrics only.
    mon = payloads[0]["monitoring"]
    assert isinstance(mon["n_matured"], int)
    assert set(mon["performance"]) >= {"n", "gini_live"}
    assert not any(isinstance(v, list) and v and isinstance(v[0], dict)
                   and ("case_id" in v[0] or "loan_id" in v[0])
                   for v in mon.values())
