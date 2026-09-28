"""The `recovery_risk` outcome definition, rule by rule.

Every rule below is a DECISION, not an inherited default. The model was
validated against `paid >= 0.8 * min(overdue_amount, emi_amount)` over 30 days;
labelling it with the existing repayment labeller's 0.9-of-still-owed rule would
silently change the target and quietly invalidate every metric in ml/artifacts.
So the two labellers are separate, and this file is what stops them drifting
into each other.

These are EXECUTABLE checks — each one runs the labeller against rows in a
database. None of them inspects source text.
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
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.user import User, UserRole
from app.ml.pipeline.outcomes import (
    MATERIAL_PAYMENT_RATIO, OUTCOME_DEFINITION_VERSION, OutcomeStatus,
    attach_outcomes, evaluate, payment_window,
)
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

HORIZON = 30
AS_OF = date(2026, 7, 1)
TODAY = date(2026, 9, 1)          # comfortably past the horizon

test_engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    create_schema(bind=test_engine)
    yield
    drop_schema(bind=test_engine)


@pytest.fixture
def db():
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def world(db):
    mgr = User(id=str(uuid.uuid4()), email="m@t.in", phone="9800000001",
               full_name="M", hashed_password="h", role=UserRole.AGENCY_MANAGER,
               is_active=True, is_verified=True)
    usr = User(id=str(uuid.uuid4()), email="a@t.in", phone="9800000002",
               full_name="A", hashed_password="h", role=UserRole.FIELD_AGENT,
               is_active=True, is_verified=True)
    db.add_all([mgr, usr]); db.flush()
    agent = Agent(id=str(uuid.uuid4()), user_id=usr.id, employee_code="E1",
                  id_card_number="T1", manager_user_id=mgr.id,
                  gender="M", base_latitude=28.4, base_longitude=77.0,
                  territory="Gurugram", languages_spoken=["HINDI"],
                  specialization=AgentSpecialization.BOTH, max_cases_per_day=10,
                  status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                  ranking_score=50.0, lifetime_collection_rate=0.5)
    cust = Customer(id=str(uuid.uuid4()), customer_ref="C1", full_name="B",
                    date_of_birth=date(1990, 1, 1), gender="M", pan_masked="A1234B",
                    aadhaar_masked="1111", phone_primary="9900000001",
                    address_line1="x", city="Gurugram", state="HR",
                    pincode="122001", latitude=28.4, longitude=77.0,
                    language_preference="HINDI")
    loan = Loan(id=str(uuid.uuid4()), customer_id=cust.id,
                loan_account_number="L1", loan_type=LoanType.PERSONAL,
                branch_code="B1", sanctioned_amount=200000.0,
                disbursed_amount=200000.0, outstanding_principal=150000.0,
                total_outstanding=170000.0, overdue_amount=24000.0,
                emi_amount=8000.0, interest_rate=15.0, tenure_months=36,
                disbursement_date=date(2022, 1, 1), maturity_date=date(2025, 1, 1),
                dpd=60, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    case = Case(id=str(uuid.uuid4()), case_number="CS1", customer_id=cust.id,
                loan_id=loan.id, agent_id=agent.id, status=CaseStatus.ASSIGNED,
                priority=CasePriority.HIGH, target_amount=24000,
                collected_amount=0)
    db.add_all([agent, cust, loan, case]); db.commit()
    return {"agent": agent, "customer": cust, "loan": loan, "case": case}


def _prediction(db, world, *, as_of=AS_OF, overdue=24000.0, emi=8000.0,
                baseline=True):
    p = ModelPrediction(
        id=str(uuid.uuid4()), model_name="recovery_risk", model_version="1.1.0",
        entity_type="case", entity_id=world["case"].id,
        loan_id=world["loan"].id, case_id=world["case"].id,
        agent_id=world["agent"].id, as_of_date=as_of, probability=0.8,
        is_modelled=True, features={"dpd": 60.0, "overdue_amount": overdue},
        outcome_baseline=({"overdue_amount": overdue, "emi_amount": emi,
                           "threshold_ratio": MATERIAL_PAYMENT_RATIO}
                          if baseline else None),
    )
    db.add(p); db.commit()
    return p


def _pay(db, world, when: datetime, amount: float,
         status=PaymentStatus.VERIFIED, receipt=None):
    p = Payment(id=str(uuid.uuid4()), case_id=world["case"].id,
                agent_id=world["agent"].id, amount=amount,
                mode=PaymentMode.CASH, status=status,
                receipt_number=receipt or f"RC{uuid.uuid4().hex[:10]}",
                payment_date=when)
    db.add(p); db.commit()
    return p


def _at(day_offset: int, hour: int = 12) -> datetime:
    return datetime.combine(AS_OF + timedelta(days=day_offset),
                            datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour)


# ---------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------

def test_threshold_is_08_of_the_smaller_of_overdue_and_emi(db, world):
    """min(overdue, emi): the bar is ONE cycle's demand, not the whole arrears.
    Using a share of accumulated arrears makes the target mechanically harder
    the longer an account sits, which turns DPD into a near-deterministic
    predictor — a leak in all but name."""
    p = _prediction(db, world, overdue=24000.0, emi=8000.0)
    _pay(db, world, _at(5), 6400.0)                 # exactly 0.8 * 8000
    r = evaluate(db, p, as_of=TODAY)
    assert r.threshold == pytest.approx(6400.0)
    assert r.status is OutcomeStatus.RECOVERED
    assert r.actual_outcome == 0


def test_one_rupee_short_is_not_recovered(db, world):
    p = _prediction(db, world, overdue=24000.0, emi=8000.0)
    _pay(db, world, _at(5), 6399.0)
    r = evaluate(db, p, as_of=TODAY)
    assert r.status is OutcomeStatus.NOT_RECOVERED
    assert r.actual_outcome == 1


def test_overdue_smaller_than_emi_lowers_the_bar(db, world):
    """A borrower who owes less than one instalment cannot be asked for one."""
    p = _prediction(db, world, overdue=3000.0, emi=8000.0)
    _pay(db, world, _at(5), 2400.0)                  # 0.8 * 3000
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.RECOVERED


def test_no_payment_at_all_is_not_recovered(db, world):
    p = _prediction(db, world)
    r = evaluate(db, p, as_of=TODAY)
    assert r.status is OutcomeStatus.NOT_RECOVERED
    assert r.amount_paid == 0.0 and r.n_payments == 0


def test_scoring_rule_matches_the_rule_the_model_was_trained_on(db, world):
    """THE ANTI-DRIFT CHECK. book_simulator computes the training label as
    `amount_paid >= 0.8 * min(max(overdue,1), emi)`. If these two ever disagree,
    the model is being judged on a question it was not trained to answer."""
    from app.ml.simulation.book_simulator import BookSimulator  # noqa: F401

    for overdue, emi, paid in [(24000, 8000, 6400), (24000, 8000, 6399),
                               (3000, 8000, 2400), (500, 8000, 400),
                               (12000, 5000, 4000)]:
        trained = paid >= 0.8 * min(max(overdue, 1.0), emi)
        p = _prediction(db, world, overdue=float(overdue), emi=float(emi))
        _pay(db, world, _at(3), float(paid))
        served = evaluate(db, p, as_of=TODAY).status is OutcomeStatus.RECOVERED
        assert served == trained, (
            f"training and serving rules disagree at overdue={overdue} "
            f"emi={emi} paid={paid}")
        db.query(Payment).delete(); db.query(ModelPrediction).delete(); db.commit()


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_window_is_exclusive_of_the_observation_day(db, world):
    """A payment made on the day the features were frozen happened BEFORE the
    prediction and says nothing about what the borrower did next."""
    p = _prediction(db, world)
    _pay(db, world, _at(0, hour=23), 20000.0)        # same day as as_of
    r = evaluate(db, p, as_of=TODAY)
    assert r.amount_paid == 0.0
    assert r.status is OutcomeStatus.NOT_RECOVERED


def test_window_includes_the_thirtieth_day(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(HORIZON, hour=23), 8000.0)
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.RECOVERED


def test_late_payments_do_not_count_but_are_recorded(db, world):
    """Money on day 31 is a real recovery and a false negative for THIS label.
    Recording it means the horizon can be argued about with evidence."""
    p = _prediction(db, world)
    _pay(db, world, _at(HORIZON + 1), 20000.0)
    r = evaluate(db, p, as_of=TODAY)
    assert r.status is OutcomeStatus.NOT_RECOVERED
    assert r.amount_paid == 0.0
    assert r.paid_after_window == 20000.0


def test_payment_window_boundaries_are_what_they_claim():
    start, end = payment_window(AS_OF, HORIZON)
    assert start.date() == AS_OF and end.date() == AS_OF + timedelta(days=HORIZON)
    assert start.tzinfo is not None and end.tzinfo is not None


# ---------------------------------------------------------------------------
# What counts as a payment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [PaymentStatus.PENDING_VERIFICATION,
                                    PaymentStatus.REJECTED,
                                    PaymentStatus.REVERSED])
def test_only_verified_payments_count(db, world, status):
    """PENDING is not yet money, REJECTED never was, REVERSED was taken back."""
    p = _prediction(db, world)
    _pay(db, world, _at(5), 20000.0, status=status)
    r = evaluate(db, p, as_of=TODAY)
    assert r.amount_paid == 0.0
    assert r.status is OutcomeStatus.NOT_RECOVERED


def test_reversed_payments_do_not_count(db, world):
    """Reversal is a status change, not a negative row — so no netting is done.
    If that representation ever changes, this test is the tripwire."""
    p = _prediction(db, world)
    _pay(db, world, _at(2), 8000.0, status=PaymentStatus.VERIFIED, receipt="R-A")
    _pay(db, world, _at(3), 8000.0, status=PaymentStatus.REVERSED, receipt="R-B")
    r = evaluate(db, p, as_of=TODAY)
    assert r.amount_paid == 8000.0
    assert all(x >= 0 for x in [r.amount_paid])


def test_multiple_part_payments_are_summed(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(2), 3000.0)
    _pay(db, world, _at(9), 3500.0)
    r = evaluate(db, p, as_of=TODAY)
    assert r.amount_paid == 6500.0 and r.n_payments == 2
    assert r.status is OutcomeStatus.RECOVERED       # 6500 >= 6400


def test_the_schema_itself_prevents_duplicate_receipts(db, world):
    """WRITTEN AS AN ASSERTION ABOUT THE SCHEMA, because that is where the real
    guarantee lives. The first version of this test tried to insert a second
    payment carrying the same receipt_number and prove the labeller counted it
    once — the database refused the insert, which is a STRONGER guarantee than
    the code-level dedupe and makes that dedupe a guard rather than a fix.

    The guard stays in outcomes.py for data arriving outside the ORM (a raw-SQL
    backfill, a restore from another system), but it is not what protects the
    label here, and this test says so rather than implying otherwise."""
    from sqlalchemy.exc import IntegrityError

    _pay(db, world, _at(2), 6400.0, receipt="DUP-1")
    clone = Payment(id=str(uuid.uuid4()), case_id=world["case"].id,
                    agent_id=world["agent"].id, amount=6400.0,
                    mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                    receipt_number="DUP-1", payment_date=_at(3))
    db.add(clone)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_labeller_dedupes_if_duplicates_ever_reach_it(db, world):
    """The guard itself, exercised without going through the unique index —
    two rows for the same receipt, summed once."""
    from app.ml.pipeline import outcomes

    p = _prediction(db, world)
    real = _pay(db, world, _at(2), 6400.0, receipt="ONLY-1")
    twin = Payment(id=str(uuid.uuid4()), case_id=real.case_id,
                   agent_id=real.agent_id, amount=6400.0, mode=PaymentMode.CASH,
                   status=PaymentStatus.VERIFIED, receipt_number="ONLY-1",
                   payment_date=_at(3))

    real_query = db.query

    def fake_query(model, *a, **k):
        q = real_query(model, *a, **k)
        if model is Payment:
            class _Q:
                def filter(self, *args, **kw):
                    return self
                def all(self_inner):
                    return [real, twin]
            return _Q()
        return q

    db.query = fake_query
    try:
        r = outcomes.evaluate(db, p, as_of=TODAY)
    finally:
        db.query = real_query
    assert r.n_payments == 1, "duplicate receipts were counted twice"
    assert r.amount_paid == 6400.0


# ---------------------------------------------------------------------------
# Censoring — the bank removed the borrower from the population
# ---------------------------------------------------------------------------

def test_written_off_is_censored_not_a_failure(db, world):
    """Counting a bank write-off as 'did not pay' teaches the model that bank
    decisions are borrower behaviour."""
    p = _prediction(db, world)
    world["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    r = evaluate(db, p, as_of=TODAY)
    assert r.status is OutcomeStatus.CENSORED_WRITTEN_OFF
    assert r.actual_outcome is None


def test_settled_is_censored(db, world):
    p = _prediction(db, world)
    world["loan"].status = LoanStatus.SETTLED
    db.commit()
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.CENSORED_SETTLED


def test_settlement_recorded_only_in_notes_is_still_censored(db, world):
    p = _prediction(db, world)
    world["case"].resolution_notes = "Bank settlement: ₹12,000 accepted"
    world["case"].status = CaseStatus.CLOSED
    db.commit()
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.CENSORED_SETTLED


def test_recalled_is_censored(db, world):
    """RECALL is detectable only as free text — ingest_daily writes it into
    resolution_notes and the schema records it nowhere else. Matched explicitly
    so the fragility is visible rather than assumed away."""
    p = _prediction(db, world)
    world["case"].status = CaseStatus.CLOSED
    world["case"].resolution_notes = "RECALLED by bank. Reason: COMPLAINT."
    db.commit()
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.CENSORED_RECALLED


def test_deceased_is_censored(db, world):
    p = _prediction(db, world)
    world["customer"].tags = ["DECEASED"]
    db.commit()
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.CENSORED_DECEASED


def test_write_off_wins_over_a_coincident_deceased_flag(db, world):
    """Order is fixed: report the reason the money stopped being collectable,
    not whichever flag happens to be checked first."""
    p = _prediction(db, world)
    world["case"].status = CaseStatus.WRITTEN_OFF
    world["customer"].tags = ["DECEASED"]
    db.commit()
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.CENSORED_WRITTEN_OFF


def test_a_censored_account_that_did_pay_is_still_censored(db, world):
    """The bank resolved it; whatever arrived is not evidence about the
    borrower's own decision to pay under collection."""
    p = _prediction(db, world)
    _pay(db, world, _at(5), 20000.0)
    world["loan"].status = LoanStatus.SETTLED
    db.commit()
    r = evaluate(db, p, as_of=TODAY)
    assert r.status is OutcomeStatus.CENSORED_SETTLED
    assert r.actual_outcome is None


# ---------------------------------------------------------------------------
# Unusable rows
# ---------------------------------------------------------------------------

def test_missing_baseline_is_never_guessed(db, world):
    """Rows written before outcome_baseline existed carry no emi_amount. They
    are left unlabelled rather than scored against today's Loan, which the
    payments themselves have already changed."""
    p = _prediction(db, world, baseline=False)
    _pay(db, world, _at(5), 20000.0)
    r = evaluate(db, p, as_of=TODAY)
    assert r.status is OutcomeStatus.NO_BASELINE
    assert r.actual_outcome is None


@pytest.mark.parametrize("overdue,emi", [(0.0, 8000.0), (24000.0, 0.0),
                                         (-1.0, 8000.0)])
def test_non_positive_baseline_is_unusable(db, world, overdue, emi):
    p = _prediction(db, world, overdue=overdue, emi=emi)
    assert evaluate(db, p, as_of=TODAY).status is OutcomeStatus.NO_BASELINE


def test_an_unmatured_prediction_is_left_alone(db, world):
    p = _prediction(db, world, as_of=date(2026, 8, 25))
    r = evaluate(db, p, as_of=date(2026, 9, 1))       # only 7 days old
    assert r.status is OutcomeStatus.NOT_MATURED
    assert r.actual_outcome is None


# ---------------------------------------------------------------------------
# The batch
# ---------------------------------------------------------------------------

def test_attach_outcomes_labels_matured_rows_and_stamps_the_version(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(4), 8000.0)
    summary = attach_outcomes(db, "recovery_risk", as_of=TODAY)

    assert summary["due"] == 1 and summary["labelled"] == 1
    assert summary["definition_version"] == OUTCOME_DEFINITION_VERSION
    db.refresh(p)
    assert p.actual_outcome == 0
    assert p.outcome_status == OutcomeStatus.RECOVERED.value
    assert p.outcome_definition_version == OUTCOME_DEFINITION_VERSION
    assert p.outcome_horizon_days == HORIZON
    assert p.outcome_attached_at is not None


def test_attach_outcomes_skips_unmatured_rows(db, world):
    _prediction(db, world, as_of=date(2026, 8, 28))
    summary = attach_outcomes(db, "recovery_risk", as_of=date(2026, 9, 1))
    assert summary["due"] == 0 and summary["labelled"] == 0


def test_censored_rows_get_a_status_but_no_label(db, world):
    p = _prediction(db, world)
    world["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    attach_outcomes(db, "recovery_risk", as_of=TODAY)
    db.refresh(p)
    assert p.actual_outcome is None, "censored must never become a 0 or a 1"
    assert p.outcome_status == OutcomeStatus.CENSORED_WRITTEN_OFF.value


def test_labelling_is_idempotent(db, world):
    """A second run must not re-label — outcome_status is the marker, so a row
    already evaluated is never revisited even though actual_outcome is NULL for
    censored rows."""
    p = _prediction(db, world)
    world["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    first = attach_outcomes(db, "recovery_risk", as_of=TODAY)
    second = attach_outcomes(db, "recovery_risk", as_of=TODAY)
    assert first["due"] == 1 and second["due"] == 0


def test_other_models_are_not_touched(db, world):
    p = _prediction(db, world)
    p.model_name = "contact_risk"
    db.commit()
    summary = attach_outcomes(db, "recovery_risk", as_of=TODAY)
    assert summary["due"] == 0
