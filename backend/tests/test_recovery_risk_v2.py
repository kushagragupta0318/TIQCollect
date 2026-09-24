"""recovery_risk 2.0.0 — the widened feature space, and what it must hold to.

Three groups, each answering a different question:

  1. THE ARTIFACT. Does the committed 2.0.0 artifact pass its own gates, was it
     compared against the model that is actually deployed on IDENTICAL rows,
     and is the champion pointer still 1.1.0? Read from the artifact and its
     V2_REPORT.json, never recomputed — a test that retrained the model would
     be asserting on whatever it got.
  2. THE CONTRACT. Are the new features servable — does the adapter produce
     every feature 2.0.0 selected, does the engine treat an abstaining feature
     as the Missing bin rather than a missing input, do the simulator and the
     adapter agree on the vocabulary (adverse outcomes, hardship reasons)?
  3. THE PIPELINE FIX. `expected_sign` is now enforced at binning. A feature
     whose empirical direction contradicts the business expectation must come
     out with no information, and every selected 2.0.0 feature's bins must
     run in the direction its sign says.

Nothing here touches champion.txt, and one test fails if anything did.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline import registry
from app.ml.pipeline.binning import WOEBinner
from app.ml.pipeline.config import (
    CANDIDATE_SPECS, LOGGED_FEATURES, RECOVERY_RISK, RECOVERY_RISK_V2,
)
from app.ml.pipeline.engine import DecisionEngine
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

V2 = "2.0.0"


def _dir() -> Path | None:
    d = registry.version_dir("recovery_risk", V2)
    return d if (d / "metadata.json").exists() else None


v2_required = pytest.mark.skipif(
    _dir() is None, reason="no 2.0.0 artifact; run scripts/train_recovery_risk_v2.py")


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((_dir() / "metadata.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> dict:
    p = _dir() / "V2_REPORT.json"
    if not p.exists():
        pytest.skip("no V2_REPORT.json beside the artifact")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 0. The spec itself
# ---------------------------------------------------------------------------

def test_v2_is_a_new_major_version_and_1_1_0_is_untouched():
    """A factor-definition change is a model change. 2.0.0 is a separate spec
    object; the 1.1.0 spec — the deployed definition, and the baseline every
    allocator measurement is expressed against — keeps its version, features,
    and gates exactly."""
    assert RECOVERY_RISK_V2.version == V2
    assert RECOVERY_RISK_V2.name == RECOVERY_RISK.name == "recovery_risk"
    assert RECOVERY_RISK.version == "1.1.0"
    assert len(RECOVERY_RISK.all_features) == 33
    assert RECOVERY_RISK.training_panel == "book_simulator"
    assert RECOVERY_RISK.abstaining_features == ()
    assert RECOVERY_RISK_V2.gates == RECOVERY_RISK.gates, "a gate moved"
    assert RECOVERY_RISK_V2.target == RECOVERY_RISK.target
    assert RECOVERY_RISK_V2.forbidden == RECOVERY_RISK.forbidden
    assert (RECOVERY_RISK_V2.name, V2) in CANDIDATE_SPECS


def test_v2_widens_rather_than_replaces_the_candidate_set():
    assert set(RECOVERY_RISK.numeric_features) < set(RECOVERY_RISK_V2.numeric_features)
    assert RECOVERY_RISK_V2.categorical_features == RECOVERY_RISK.categorical_features
    assert RECOVERY_RISK_V2.training_panel == "ledger"
    new = set(RECOVERY_RISK_V2.numeric_features) - set(RECOVERY_RISK.numeric_features)
    assert len(new) == 20, sorted(new)


def test_every_v2_feature_has_an_expected_sign_and_no_forbidden_column():
    for f in RECOVERY_RISK_V2.numeric_features:
        assert f in RECOVERY_RISK_V2.expected_sign, f
    assert not set(RECOVERY_RISK_V2.all_features) & set(RECOVERY_RISK_V2.forbidden)


def test_abstaining_features_are_a_subset_of_the_candidates():
    assert set(RECOVERY_RISK_V2.abstaining_features) <= set(RECOVERY_RISK_V2.numeric_features)


def test_logged_vector_covers_both_specs():
    """The prediction log carries the WIDEST candidate set so a retrain can
    select from all of it, and the champion's own inputs are inside it."""
    assert set(LOGGED_FEATURES) >= set(RECOVERY_RISK.all_features)
    assert set(LOGGED_FEATURES) >= set(RECOVERY_RISK_V2.all_features)
    assert len(LOGGED_FEATURES) == len(set(LOGGED_FEATURES))


# ---------------------------------------------------------------------------
# 1. The artifact
# ---------------------------------------------------------------------------

@v2_required
def test_v2_artifact_passed_every_gate(meta):
    assert meta["gate_summary"] == "PASS"
    assert not any(g["result"] == "FAIL" for g in meta["gates"])
    assert meta["spec"]["version"] == V2
    assert meta["spec"]["training_panel"] == "ledger"
    assert meta["champion_kind"] == "scorecard"


@v2_required
def test_v2_selects_between_seven_and_twelve_features_from_its_own_candidates(meta):
    sel = meta["selected_features"]
    assert 7 <= len(sel) <= 12, sel
    assert set(sel) <= set(RECOVERY_RISK_V2.all_features)
    # At least one feature the 1.x spec could never have selected, or the
    # whole exercise widened nothing.
    assert set(sel) - set(RECOVERY_RISK.all_features), sel


@v2_required
def test_v2_gini_is_in_the_believable_band(meta):
    g = meta["metrics"]["oot"]["gini"]
    gates = meta["spec"]["gates"]
    assert gates["gini_min"] <= g < gates["gini_suspicious"], g
    assert meta["metrics"]["oot"]["rank_order"]["n_breaks"] == 0


@v2_required
def test_v2_records_the_abstaining_features_it_was_fitted_with(meta):
    assert set(meta["spec"]["abstaining_features"]) == set(RECOVERY_RISK_V2.abstaining_features)


@v2_required
def test_v2_beat_the_deployed_model_on_identical_rows(report):
    """The only apples-to-apples read there is: both models scored on the
    same out-of-time frame through the production path, judged by the same
    gate the automated retrain uses."""
    cmp = report["incumbent_comparison"]
    assert cmp["incumbent_version"] == "1.1.0"
    assert cmp["challenger_version"] == V2
    assert cmp["passed"] and cmp["verdict"] == "challenger_better"
    assert cmp["gini_uplift"] > cmp["uplift_tolerance"]
    assert cmp["challenger"]["ks"] >= cmp["incumbent"]["ks"]
    assert cmp["challenger"]["brier"] <= cmp["incumbent"]["brier"]
    assert cmp["n_oot"] >= 10_000


@v2_required
def test_v2_no_degrade_table_has_no_fail_and_every_check_passed(report):
    assert report["no_degrade_passed"]
    assert all(r["result"] != "FAIL" for r in report["no_degrade"])
    assert report["overall_passed"]
    gated = [c for c in report["checks_7_to_10"] if c["status"] in ("PASS", "FAIL")]
    assert gated and all(c["status"] == "PASS" for c in gated)


@v2_required
def test_v2_was_not_promoted_by_its_own_training(report):
    """Promotion is a separate human act. Two independent reads: what the
    script recorded, and what the pointer says now."""
    assert report["champion_after_run"] == "1.1.0"          # what the run recorded, then
    assert registry.pointer_version("recovery_risk") != "2.0.0"  # and it never became champion


@v2_required
def test_v2_selected_bins_run_in_the_direction_the_spec_expects(meta):
    """The pipeline fix, proven on the artifact rather than asserted: every
    selected numeric feature with a stated sign has bin event rates that are
    monotone in that direction (excluding Special / Missing rows)."""
    bins = pd.read_csv(_dir() / "eda" / "binning_tables.csv")
    signs = RECOVERY_RISK_V2.expected_sign
    checked = 0
    for f in meta["selected_features"]:
        sign = signs.get(f, 0)
        if sign == 0 or f not in bins.feature.unique():
            continue
        body = bins[(bins.feature == f)
                    & ~bins.Bin.astype(str).isin(["Special", "Missing", "Totals", "nan"])]
        rates = pd.to_numeric(body["Event rate"], errors="coerce").dropna().to_numpy()
        if len(rates) < 2:
            continue
        d = np.diff(rates)
        ok = bool(np.all(d >= -1e-9)) if sign > 0 else bool(np.all(d <= 1e-9))
        assert ok, f"{f}: sign {sign} but event rates {rates.round(3)}"
        checked += 1
    assert checked >= 3
    assert meta["binning_trends_forced"] == WOEBinner.trends_from_signs(signs)


# ---------------------------------------------------------------------------
# 2. The contract: the new features are servable
# ---------------------------------------------------------------------------

def test_simulator_and_adapter_agree_on_the_vocabulary():
    from app.ml.simulation.ledger import simulator as sim
    from app.services import ml_scoring_service as svc

    assert {o.value for o in svc.ADVERSE_VISIT_OUTCOMES} == set(sim.ADVERSE_VISIT_OUTCOMES)
    assert {r.value for r in svc.HARDSHIP_REASONS} == set(sim.HARDSHIP_REASONS)
    assert svc.PARTIAL_PAYMENT_RATIO == 0.9


def test_the_engine_treats_an_abstaining_feature_as_the_missing_bin():
    """Present-but-null on a declared-abstaining feature is NOT missing input;
    an absent key always is. The first 2.0.0 run declined 3,202 honest rows
    before this distinction existed."""
    eng = DecisionEngine.__new__(DecisionEngine)
    eng.selected = ["a", "b", "c", "d"]
    eng.abstaining = {"c", "d"}
    cov, missing = eng._coverage({"a": 1.0, "b": 2.0, "c": None, "d": float("nan")})
    assert missing == [] and cov == 1.0
    cov, missing = eng._coverage({"a": 1.0, "b": None, "c": None, "d": 0.5})
    assert missing == ["b"] and cov == 0.75
    cov, missing = eng._coverage({"a": 1.0, "b": 2.0})
    assert missing == ["c", "d"] and cov == 0.5, "an absent key is a break"


def test_a_1x_engine_keeps_its_old_coverage_arithmetic():
    """No abstaining list on a 1.x artifact, so cibil_score None still counts
    as missing — the behaviour tests/test_batch_scoring_parity.py pins."""
    eng = DecisionEngine.get("recovery_risk", "1.1.0")
    if eng is None:
        pytest.skip("no 1.1.0 artifact")
    assert eng.abstaining == set()
    cov, missing = eng._coverage({"dpd": 1.0, "overdue_amount": 2.0,
                                  "ptp_kept_ratio": 0.5, "cibil_score": None})
    assert missing == ["cibil_score"] and cov == 0.75


@v2_required
def test_the_adapter_supplies_every_feature_v2_selected(meta):
    """Built against the real schema in SQLite, with a customer, a loan, a
    case, visits with outcomes, calls, promises and payments — the v2 version
    of tests/test_ml_scoring_adapter.py's coverage test."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
    from app.models.base import Base
    from app.models.call_log import CallLog, CallOutcome
    from app.models.case import Case, CasePriority, CaseStatus
    from app.models.customer import Customer
    from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
    from app.models.payment import Payment, PaymentMode, PaymentStatus
    from app.models.ptp import PTP, PTPStatus
    from app.models.user import User, UserRole
    from app.models.visit import DefaultReason, Visit, VisitOutcome
    from app.services.ml_scoring_service import MLScoringService

    engine = make_engine()
    create_schema(bind=engine)
    db = make_session_factory(bind=engine)()
    now = datetime.now(timezone.utc)

    user = User(id=str(uuid.uuid4()), email="a@x.test", phone="9000000001",
                full_name="A", hashed_password="h", role=UserRole.FIELD_AGENT,
                is_active=True, is_verified=True)
    db.add(user); db.flush()
    agent = Agent(id=str(uuid.uuid4()), user_id=user.id, employee_code="E1",
                  id_card_number="IC1", agency_id="AG", manager_user_id=user.id,
                  gender="M", base_latitude=28.4, base_longitude=77.0,
                  territory="Gurugram", languages_spoken=["HINDI"],
                  specialization=AgentSpecialization.BOTH, max_cases_per_day=10,
                  status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                  ranking_score=50.0, lifetime_collection_rate=0.5)
    cust = Customer(id=str(uuid.uuid4()), customer_ref="C1", full_name="B",
                    date_of_birth="1985-05-05", gender="M", pan_masked="A",
                    aadhaar_masked="1", phone_primary="9900000001",
                    address_line1="x", city="Gurugram", state="HR", pincode="122001",
                    latitude=28.4, longitude=77.0, customer_segment="SALARIED",
                    cibil_score=640, is_hostile=True, fraud_flag=False)
    loan = Loan(id=str(uuid.uuid4()), customer_id=cust.id, loan_account_number="L1",
                loan_type=LoanType.PERSONAL, bank_name="HDFC", branch_code="BR01",
                sanctioned_amount=250000.0, disbursed_amount=250000.0,
                outstanding_principal=180000.0, total_outstanding=205000.0,
                overdue_amount=24000.0, penal_charges=1200.0, emi_amount=8000.0,
                interest_rate=16.5, tenure_months=36, disbursement_date="2024-01-15",
                maturity_date="2027-01-15", last_payment_date="2026-08-01",
                dpd=62, dpd_bucket=DPDBucket.BUCKET_3, status=LoanStatus.ACTIVE)
    case = Case(id=str(uuid.uuid4()), case_number="CS1", customer_id=cust.id,
                loan_id=loan.id, agent_id=agent.id, status=CaseStatus.ASSIGNED,
                priority=CasePriority.HIGH, target_amount=24000, collected_amount=0)
    db.add_all([agent, cust, loan, case]); db.flush()
    for i, (met, out, reason) in enumerate([
            (True, VisitOutcome.RTP, None), (True, VisitOutcome.PTP, DefaultReason.JOB_LOSS),
            (False, VisitOutcome.NOT_AVAILABLE, None), (True, VisitOutcome.DISPUTE, None)]):
        db.add(Visit(id=str(uuid.uuid4()), case_id=case.id, agent_id=agent.id,
                     check_in_latitude=28.4, check_in_longitude=77.0,
                     check_in_time=now - timedelta(days=10 * (i + 1)),
                     distance_from_customer_metres=40.0, customer_met=met,
                     outcome=out, default_reason=reason))
    for i, (out, intent) in enumerate([(CallOutcome.ANSWERED, True), (CallOutcome.NO_ANSWER, None),
                                       (CallOutcome.ANSWERED, False), (CallOutcome.BUSY, None)]):
        db.add(CallLog(id=str(uuid.uuid4()), case_id=case.id, agent_id=agent.id,
                       customer_id=cust.id, called_at=now - timedelta(days=5 * (i + 1)),
                       outcome=out, payment_intent_signalled=intent))
    for i, st in enumerate([PTPStatus.HONORED, PTPStatus.BROKEN, PTPStatus.RESCHEDULED]):
        p = PTP(id=str(uuid.uuid4()), case_id=case.id, agent_id=agent.id,
                committed_amount=6000.0, committed_date=(now - timedelta(days=30 * (i + 1))).date(),
                status=st)
        p.created_at = now - timedelta(days=30 * (i + 1) + 5)
        db.add(p)
    for i, amt in enumerate([8000.0, 3000.0, 8000.0, 2500.0]):
        db.add(Payment(id=str(uuid.uuid4()), case_id=case.id, agent_id=agent.id,
                       amount=amt, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                       receipt_number=f"R{i}", payment_date=now - timedelta(days=25 * (i + 1))))
    db.commit()

    feats = MLScoringService(db).build_features(loan)
    # Every candidate key is emitted, always.
    assert set(LOGGED_FEATURES) <= set(feats)
    # Every feature 2.0.0 selected has a real value on this borrower.
    missing = [f for f in meta["selected_features"] if feats.get(f) is None]
    assert missing == [], missing
    # And the new channels read what was written.
    assert feats["rtp_visits_6m"] == 1.0 and feats["dispute_visits_6m"] == 1.0
    assert feats["hardship_visits_6m"] == 1.0
    assert feats["calls_3m"] == 4.0 and feats["call_answer_rate_6m"] == 0.5
    assert feats["no_answer_streak"] == 0.0            # newest call was answered
    assert feats["last_call_intent"] == 1.0
    assert feats["intent_calls_3m"] == 1.0
    assert feats["ptp_broken_6m"] == 1.0 and feats["ptp_rescheduled_6m"] == 1.0
    assert feats["ptp_amount_to_emi"] == 0.75
    assert feats["payments_6m"] == 4.0
    assert feats["partial_payment_share_6m"] == 0.5
    assert feats["payment_gap_cv_12m"] == 0.0       # evenly spaced 25 days
    assert feats["last_payment_to_emi"] == 1.0
    assert feats["is_hostile"] == 1 and feats["fraud_flag"] == 0

    eng = DecisionEngine.get("recovery_risk", V2)
    assert eng is not None
    r = eng.score(feats)
    assert r.is_modelled and r.feature_coverage == 1.0 and r.missing_features == []
    assert 0.0 < r.probability < 1.0 and r.points is not None
    db.close()


# ---------------------------------------------------------------------------
# 3. The pipeline fix: expected_sign is enforced
# ---------------------------------------------------------------------------

def test_a_forced_trend_neutralises_a_wrong_direction_feature():
    rng = np.random.default_rng(0)
    n = 20_000
    x = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(0.3 + 1.2 * x)))).astype(int)
    X = pd.DataFrame({"x": x})
    auto = WOEBinner(["x"], []).fit(X, y).iv_["x"]
    right = WOEBinner(["x"], [], trends={"x": "ascending"}).fit(X, y).iv_["x"]
    wrong = WOEBinner(["x"], [], trends={"x": "descending"}).fit(X, y).iv_.get("x", 0.0)
    assert right == pytest.approx(auto)
    assert auto > 0.5
    assert wrong < 0.02, "a feature binned against its business direction must carry no IV"


def test_trends_from_signs_maps_the_polarity_correctly():
    t = WOEBinner.trends_from_signs({"up": 1, "down": -1, "free": 0})
    assert t == {"up": "ascending", "down": "descending"}
