"""The live-schema adapter: where a trained model meets real rows.

EVERY TEST HERE EXISTS BECAUSE THE BUG IT CATCHES SHIPPED. `recovery_risk` was
promoted, reported healthy on `GET /manager/ml/health`, and fed the allocator
NOTHING for a full cycle. Two faults, both invisible:

  1. `Loan.disbursement_date` is `String(10)`, not a Date. `build_features`
     raised `TypeError: unsupported operand type(s) for -: 'datetime.date' and
     'str'` on the first loan it touched. `_ml_recovery_probabilities` caught
     it, logged a warning, returned {} — correct degradation that hid a one-line
     type error.
  2. `Loan.cases` is declared `lazy="noload"`, so it is ALWAYS an empty list
     unless eager-loaded. Reading it produced no history features, and
     `ptp_kept_ratio` — the champion's third input, IV 0.2798 — was never
     supplied. The engine still scored, at 0.75 coverage, above its 0.60 floor.
     A wrong answer wearing a working one's clothes.

Neither raised. Neither showed on any dashboard. That is what these tests are
for: not "does it run" but "is it actually reading this schema".
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.base import Base
from app.models.case import Case, CasePriority, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.model_prediction import ModelPrediction
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.services.ml_scoring_service import MLScoringService, _as_date

test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    s = TestingSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def book(db):
    """One borrower with a loan, a case, and PTP history.

    Dates are STRINGS throughout, because that is what this schema stores —
    Customer.date_of_birth and every Loan date column are String(10).
    """
    # PTP.agent_id is NOT NULL, so the history this test is about cannot exist
    # without a real agent behind it.
    mgr = User(id=str(uuid.uuid4()), email="mgr_ml@tiqcollect.in", phone="9800009001",
               full_name="Manager ML", hashed_password="hash",
               role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True)
    usr = User(id=str(uuid.uuid4()), email="agent_ml@tiqcollect.in", phone="9800009002",
               full_name="Agent ML", hashed_password="hash",
               role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
    db.add_all([mgr, usr])
    db.flush()
    agent = Agent(
        id=str(uuid.uuid4()), user_id=usr.id, employee_code="EMPML",
        id_card_number="TIQML", agency_id="AG01", manager_user_id=mgr.id,
        gender="F", base_latitude=28.4595, base_longitude=77.0266,
        territory="Gurugram", languages_spoken=["HINDI"],
        specialization=AgentSpecialization.UNSECURED, max_cases_per_day=10,
        status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_2, ranking_score=80.0,
        lifetime_collection_rate=0.55,
    )
    db.add(agent)
    db.flush()

    cust = Customer(
        id=str(uuid.uuid4()), customer_ref="CUSTML", full_name="Meena Iyer",
        date_of_birth="1988-04-12", gender="F", pan_masked="ZZZZZ1111A",
        aadhaar_masked="111122223333", phone_primary="9900001111",
        address_line1="Sector 29, Gurugram", city="Gurugram", state="Haryana",
        pincode="122001", latitude=28.4595, longitude=77.0266,
        language_preference="HINDI", cibil_score=640, customer_segment="SALARIED",
    )
    loan = Loan(
        id=str(uuid.uuid4()), customer_id=cust.id, loan_account_number="LNML1",
        loan_type=LoanType.PERSONAL, bank_name="HDFC Bank", branch_code="GG01",
        sanctioned_amount=250000.0, disbursed_amount=250000.0,
        outstanding_principal=180000.0, total_outstanding=205000.0,
        overdue_amount=24000.0, penal_charges=1200.0, emi_amount=8000.0,
        interest_rate=16.5, tenure_months=36,
        disbursement_date="2022-01-15", maturity_date="2025-01-15",
        last_payment_date="2026-06-01",
        dpd=62, dpd_bucket=DPDBucket.BUCKET_3, status=LoanStatus.ACTIVE,
    )
    case = Case(
        id=str(uuid.uuid4()), case_number="CASEML1", customer_id=cust.id,
        loan_id=loan.id, agent_id=None, status=CaseStatus.UNASSIGNED,
        priority=CasePriority.HIGH, target_amount=24000, collected_amount=2000,
    )
    db.add_all([cust, loan, case])
    db.flush()

    now = datetime.now(timezone.utc)
    for i, status in enumerate([PTPStatus.HONORED, PTPStatus.HONORED,
                                PTPStatus.BROKEN, PTPStatus.PARTIALLY_HONORED]):
        p = PTP(id=str(uuid.uuid4()), case_id=case.id, agent_id=agent.id,
                committed_amount=5000.0,
                committed_date=(now - timedelta(days=30 * (i + 1))).date(),
                status=status)
        p.created_at = now - timedelta(days=30 * (i + 1))
        db.add(p)
    db.commit()
    return {"customer": cust, "loan": loan, "case": case, "agent": agent}


# ---------------------------------------------------------------------------
# 1. Date parsing — the schema stores ISO STRINGS
# ---------------------------------------------------------------------------

def test_as_date_accepts_every_shape_this_schema_holds():
    assert _as_date("2024-03-09") == date(2024, 3, 9)
    assert _as_date(date(2024, 3, 9)) == date(2024, 3, 9)
    assert _as_date(datetime(2024, 3, 9, 11, 30)) == date(2024, 3, 9)
    assert _as_date("2024-03-09T00:00:00") == date(2024, 3, 9)


def test_as_date_never_raises_on_bad_input():
    """One malformed row must not cost the whole book its scores."""
    for bad in (None, "", "not-a-date", "0000-00-00", 12345):
        assert _as_date(bad) is None


def test_the_schema_really_does_store_dates_as_strings():
    """If these ever become Date columns the coercion is dead weight — and this
    test says so rather than leaving it unexplained."""
    assert isinstance(Loan.__table__.c.disbursement_date.type.python_type(), str)
    assert isinstance(Customer.__table__.c.date_of_birth.type.python_type(), str)


def test_build_features_survives_string_dates(db, book):
    """THE FIRST SILENT FAILURE. Subtracting a str from a date raised TypeError
    on the first loan touched, every night, for a full cycle."""
    f = MLScoringService(db).build_features(book["loan"], as_of=date(2026, 9, 8))
    assert f["months_on_book"] > 0
    assert f["days_since_last_payment"] == 99.0        # 2026-06-01 -> 2026-09-08
    assert f["age"] == 38.0


# ---------------------------------------------------------------------------
# 2. Loan.cases is lazy="noload" — history must be QUERIED
# ---------------------------------------------------------------------------

def test_loan_cases_is_noload_so_the_guards_below_are_not_vacuous():
    from sqlalchemy import inspect as sa_inspect
    assert sa_inspect(Loan).relationships["cases"].lazy == "noload"


def test_the_relationship_really_is_empty_even_with_a_case_present(db, book):
    """Proves the trap concretely: the case exists and points at the loan, and
    `loan.cases` is still empty."""
    loan = db.query(Loan).filter(Loan.id == book["loan"].id).first()
    assert db.query(Case).filter(Case.loan_id == loan.id).count() == 1
    assert list(loan.cases or []) == []


def test_history_features_are_populated_from_a_query(db, book):
    """THE SECOND SILENT FAILURE. Reading loan.cases gave {} for every loan, so
    every history feature was absent and ptp_kept_ratio fell to its prior."""
    f = MLScoringService(db).build_features(book["loan"])
    assert f["ptp_set_6m"] >= 1, "PTP history was not read"
    assert f["ptp_kept_ratio"] is not None
    # 4 PTPs seeded, 3 of them kept (HONORED x2 + PARTIALLY_HONORED); only those
    # inside the 6-month window count.
    assert 0.0 < f["ptp_kept_ratio"] <= 1.0
    assert f["ptp_kept_ratio"] != 0.5 or f["ptp_set_6m"] == 0, \
        "0.5 is the no-evidence prior — history should have displaced it"


def test_no_code_path_reads_the_noload_relationship(db):
    """A TRIPWIRE, NOT PROOF — and the distinction matters.

    This greps source text, and a grep cannot tell whether a line is reachable.
    The proof that history features work is
    test_history_features_are_populated_from_a_query above, which runs the
    adapter against real rows. This exists only because `loan.cases` was read
    TWICE in one function — once for the case list, once for the paid_ratio
    denominator — and the second read survived the first fix."""
    import inspect

    from app.services import ml_scoring_service

    src = inspect.getsource(ml_scoring_service)
    assert "loan.cases" not in src, (
        "ml_scoring_service reads Loan.cases, which is lazy='noload' and always "
        "empty — query Case by loan_id instead")


# ---------------------------------------------------------------------------
# 3 & 4. All four model features, and full coverage
# ---------------------------------------------------------------------------

def _champion_features():
    import json

    from app.ml.pipeline import registry
    try:
        v = registry.resolve_version("recovery_risk")
    except FileNotFoundError:
        pytest.skip("no champion artifact")
    meta = json.loads((registry.version_dir("recovery_risk", v) / "metadata.json").read_text())
    return meta["selected_features"]


def _champion_spec():
    import json

    from app.ml.pipeline import registry
    v = registry.resolve_version("recovery_risk")
    meta = json.loads((registry.version_dir("recovery_risk", v) / "metadata.json").read_text())
    return meta.get("spec") or {}


def test_adapter_supplies_every_feature_the_champion_selected(db, book):
    feats = _champion_features()
    f = MLScoringService(db).build_features(book["loan"])
    # The engine's own rule: a key must be PRESENT; a null is a break only on a
    # feature the champion does not declare abstaining (2.2.0 declares three).
    abstaining = set(_champion_spec().get("abstaining_features") or ())
    missing = [x for x in feats if x not in f or (x not in abstaining and f.get(x) is None)]
    assert missing == [], f"adapter cannot supply: {missing}"


def test_ptp_kept_ratio_specifically_is_supplied(db, book):
    """Called out on its own because it is the feature that was silently absent
    in production while everything reported healthy: IV 0.2798, ablation delta
    -0.0116, the champion's third input."""
    if "ptp_kept_ratio" not in _champion_features():
        pytest.skip("ptp_kept_ratio is not in the current champion")
    f = MLScoringService(db).build_features(book["loan"])
    assert f["ptp_kept_ratio"] is not None


def test_scoring_reaches_full_coverage(db, book):
    """0.75 coverage still scores — it is above the 0.60 floor — which is
    exactly why the missing feature went unnoticed. Full coverage is the
    assertion, not merely 'it returned a number'."""
    r = MLScoringService(db).score(book["loan"])
    assert r.is_modelled is True
    assert r.missing_features == []
    assert r.feature_coverage == pytest.approx(1.0)
    assert 0.0 <= r.probability <= 1.0
    assert r.points is not None and r.band is not None


def test_score_ranks_a_worse_borrower_higher(db, book):
    """Sanity that the wiring is not merely returning a constant."""
    svc = MLScoringService(db)
    good = svc.score(book["loan"])
    book["loan"].dpd = 320
    book["loan"].overdue_amount = 150000.0
    book["customer"].cibil_score = 480
    db.flush()
    bad = svc.score(book["loan"])
    assert bad.probability > good.probability
    assert bad.points < good.points


# ---------------------------------------------------------------------------
# 5. Fallback when the model genuinely fails
# ---------------------------------------------------------------------------

def test_declines_rather_than_guessing_when_features_are_too_sparse(db):
    """A scorecard with one of four inputs is the intercept plus noise."""
    from app.ml.pipeline.engine import DecisionEngine

    e = DecisionEngine.get("recovery_risk")
    if e is None:
        pytest.skip("no champion artifact")
    r = e.score({"dpd": 90})
    assert r.is_modelled is False
    assert r.probability is None
    assert "features were supplied" in r.fallback_reason


def test_missing_artifact_degrades_to_an_honest_non_answer(db, book, monkeypatch):
    """No champion must mean 'not modelled', never a fabricated probability."""
    from app.ml.pipeline import engine as engine_mod

    monkeypatch.setattr(engine_mod.DecisionEngine, "get",
                        classmethod(lambda cls, *a, **k: None))
    r = MLScoringService(db).score(book["loan"])
    assert r.is_modelled is False
    assert r.probability is None
    assert "no champion artifact" in (r.fallback_reason or "")


def test_planner_falls_back_to_the_old_transform_when_scoring_raises(db, monkeypatch):
    """THE PAIRING, under failure. If scoring dies the planner must return {} AND
    keep log_current — `calibrated probability + log_current` measured -10.1%
    realised recovery, and `flat probability + log_rescaled` was never evaluated
    at all, so neither mixed corner may ever ship."""
    from app.core.config import settings
    from app.services import planner_service
    from app.services.planner_service import PlannerService

    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", True)

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("model exploded")

    monkeypatch.setattr(planner_service, "MLScoringService", _Boom, raising=False)
    svc = PlannerService(db, "mgr-1")
    assert svc._ml_recovery_probabilities([]) == {}
    assert svc._ml_prediction_rows == []


def test_scoring_disabled_returns_nothing_and_logs_nothing(db, book, monkeypatch):
    from app.core.config import settings
    from app.services.planner_service import PlannerService

    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", False)
    svc = PlannerService(db, "mgr-1")
    assert svc._ml_recovery_probabilities([book["case"]]) == {}
    assert db.query(ModelPrediction).count() == 0


# ---------------------------------------------------------------------------
# The feedback loop — predictions must actually be recorded
# ---------------------------------------------------------------------------

def test_scoring_a_case_list_records_a_prediction_row(db, book):
    """model_predictions sat at 0 rows while the model drove allocation, because
    the planner used score_many(), which records nothing. Without these rows the
    monitor has no input and the loop is not closed."""
    if not _champion_features():
        pytest.skip("no champion artifact")
    svc = MLScoringService(db)
    probs, rows = svc.score_cases_and_log([book["case"]])
    db.commit()

    assert probs, "no probability returned"
    assert len(rows) == 1
    saved = db.query(ModelPrediction).one()
    assert saved.model_name == "recovery_risk"
    assert saved.model_version
    assert saved.artifact_sha256
    assert saved.case_id == book["case"].id
    assert saved.loan_id == book["loan"].id
    assert saved.entity_type == "case"
    assert 0.0 <= saved.probability <= 1.0
    assert saved.is_modelled is True
    assert saved.as_of_date is not None
    assert saved.scored_at is not None
    assert saved.feature_coverage == pytest.approx(1.0)
    # THE FULL CANDIDATE VECTOR is stored, 2026-09-15 — every feature the
    # widest spec names, so a production retrain can select from all of it.
    # *(This read "Only the model's own inputs are stored — PSI is computed on
    # those" and asserted equality with the champion's four. That was the
    # limitation production_dataset.py recorded in its own header: a challenger
    # could only ever re-select among the incumbent's inputs.)* The champion's
    # inputs are still a subset, which is what the monitor reads.
    from app.ml.pipeline.config import LOGGED_FEATURES

    assert set(saved.features) >= set(_champion_features())
    assert set(saved.features) == set(LOGGED_FEATURES)
    assert len(saved.features) > len(_champion_features())
    # Not yet linked: the agent is unknown until the solve returns.
    assert saved.agent_id is None
    # Never counted as an outcome until the labeller attaches one.
    assert saved.actual_outcome is None


def test_prediction_logging_can_be_switched_off(db, book, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ML_LOG_PREDICTIONS", False)
    probs, rows = MLScoringService(db).score_cases_and_log([book["case"]])
    db.commit()
    assert probs, "scoring must still work with logging off"
    assert rows == []
    assert db.query(ModelPrediction).count() == 0


def test_every_case_on_a_shared_loan_is_scored(db, book):
    """THE THIRD SILENT FAILURE. Scoring de-duplicated by loan and kept only the
    first case, so a loan carrying two cases left the second unscored. Measured
    on a live pool of 933 cases over 814 distinct loans, 119 cases — 57 of the
    214 actually allocated, 27% — were ML-less while the run reported itself
    ML-driven. The model is a LOAN-level estimate; every case on that loan is
    entitled to it."""
    if not _champion_features():
        pytest.skip("no champion artifact")

    second = Case(
        id=str(uuid.uuid4()), case_number="CASEML2",
        customer_id=book["customer"].id, loan_id=book["loan"].id,
        agent_id=None, status=CaseStatus.UNASSIGNED,
        priority=CasePriority.MEDIUM, target_amount=12000, collected_amount=0,
    )
    db.add(second)
    db.commit()

    probs, rows = MLScoringService(db).score_cases_and_log([book["case"], second])
    db.commit()

    assert set(probs) == {book["case"].id, second.id}, \
        "a case sharing a loan with another was dropped"
    # Same loan, so the same borrower-level probability.
    assert probs[book["case"].id] == pytest.approx(probs[second.id])
    # And one prediction row per CASE, so decision linkage stays exact.
    assert db.query(ModelPrediction).count() == 2
    assert {r.case_id for r in rows} == {book["case"].id, second.id}


# ---------------------------------------------------------------------------
# ML_MODEL_VERSION is a rollout gate, so it has to actually gate
# ---------------------------------------------------------------------------

def test_the_configured_version_is_what_actually_gets_served(monkeypatch):
    """Setting `ML_MODEL_VERSION` must change which artifact serves scores.

    2026-09-09. It did not. `DecisionEngine.get` defaulted to the literal
    "champion" and all six call sites in ml_scoring_service omitted the
    argument, so the setting was read in exactly ONE place — the
    /manager/ml/health endpoint, which reported it as `configured_version`.
    Pinning it to 1.0.0 to roll back would have changed nothing except the
    health endpoint's claim about itself: it would have confirmed the rollback
    while 1.1.0 kept serving. Executed rather than grepped, because a gate that
    is believed and does nothing is worse than no gate.
    """
    from app.core.config import settings
    from app.ml.pipeline.engine import DecisionEngine

    DecisionEngine.clear_cache()
    if DecisionEngine.get("recovery_risk") is None:
        pytest.skip("no champion artifact")

    monkeypatch.setattr(settings, "ML_MODEL_VERSION", "1.0.0")
    DecisionEngine.clear_cache()
    pinned = DecisionEngine.get("recovery_risk")
    if pinned is None:
        pytest.skip("1.0.0 artifact not present")
    assert pinned.version == "1.0.0", (
        "ML_MODEL_VERSION does not reach the loader — the rollout gate is inert"
    )

    monkeypatch.setattr(settings, "ML_MODEL_VERSION", "champion")
    DecisionEngine.clear_cache()
    from app.ml.pipeline import registry
    assert DecisionEngine.get("recovery_risk").version == registry.pointer_version("recovery_risk")
    DecisionEngine.clear_cache()


def test_an_explicit_version_still_beats_the_setting(monkeypatch):
    """The training and comparison scripts pin a version deliberately; the
    setting is the DEFAULT, not an override of an explicit request."""
    from app.core.config import settings
    from app.ml.pipeline.engine import DecisionEngine

    monkeypatch.setattr(settings, "ML_MODEL_VERSION", "1.1.0")
    DecisionEngine.clear_cache()
    e = DecisionEngine.get("recovery_risk", "1.0.0")
    if e is None:
        pytest.skip("1.0.0 artifact not present")
    assert e.version == "1.0.0"
    DecisionEngine.clear_cache()


def test_health_reports_the_version_it_is_actually_serving(monkeypatch):
    """The health endpoint's `configured_version` and the engine's `version`
    must not be able to disagree — that disagreement was the whole defect."""
    from app.core.config import settings
    from app.ml.pipeline.engine import DecisionEngine, health_all

    monkeypatch.setattr(settings, "ML_MODEL_VERSION", "1.0.0")
    DecisionEngine.clear_cache()
    entry = next(m for m in health_all()["models"] if m["model"] == "recovery_risk")
    if not entry["loaded"]:
        pytest.skip("1.0.0 artifact not present")
    assert entry["version"] == settings.ML_MODEL_VERSION
    DecisionEngine.clear_cache()
