"""The dual-labelling comparison, cause by cause.

Two labellers answer "did this recover?" in this repo and they do not agree. The
point of this file is that every claim made about HOW they differ is executed
against a database rather than argued from the source:

  * `test_the_repayment_rule_binarises_to_any_payment` runs the real
    `_infer_outcome` on a payment of one rupee against a debt of twenty-four
    thousand and shows the result lands in `POSITIVE_OUTCOMES`. The 0.9 ratio
    does not gate the positive class; it only splits REPAID from PARTIAL, and
    the trainer maps both to 1.
  * `test_the_repayment_rule_counts_rejected_payments` runs it on a REJECTED
    receipt and shows the money is counted. `_RECOVERED_PAYMENT_STATUSES` is
    applied in `_received_within`, not here.
  * `test_comparison_calls_the_real_repayment_labeller` replaces
    `RepaymentService._infer_outcome` and fails if the comparison did not go
    through it — the executable version of "no second copy of the rule".

NO SOURCE-TEXT CHECKS. A structural grep is what let the planner-lock scope bug
through: the string existed, in the wrong function, and the assertion passed.
Every test below runs the code.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.ml.pipeline import label_comparison as lc
from app.ml.pipeline.outcomes import MATERIAL_PAYMENT_RATIO, OutcomeStatus
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.base import Base
from app.models.case import Case, CasePriority, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.model_prediction import ModelPrediction
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.repayment_snapshot import (
    OUTCOME_CENSORED, OUTCOME_NO_PAYMENT, OUTCOME_PARTIAL, POSITIVE_OUTCOMES,
)
from app.models.user import User, UserRole
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

HORIZON = settings.REPAYMENT_OUTCOME_HORIZON_DAYS
AS_OF = date(2026, 7, 1)
TODAY = AS_OF + timedelta(days=HORIZON + 10)     # comfortably matured

OVERDUE = 24000.0
EMI = 8000.0
THRESHOLD = MATERIAL_PAYMENT_RATIO * min(OVERDUE, EMI)      # 6400.0

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
    """One loan with TWO cases — the second is what makes the loan-vs-case
    scope difference measurable rather than theoretical."""
    mgr = User(id=str(uuid.uuid4()), email="m@t.in", phone="9800000001",
               full_name="M", hashed_password="h", role=UserRole.AGENCY_MANAGER,
               is_active=True, is_verified=True)
    usr = User(id=str(uuid.uuid4()), email="a@t.in", phone="9800000002",
               full_name="A", hashed_password="h", role=UserRole.FIELD_AGENT,
               is_active=True, is_verified=True)
    db.add_all([mgr, usr]); db.flush()
    agent = Agent(id=str(uuid.uuid4()), user_id=usr.id, employee_code="E1",
                  id_card_number="T1", agency_id="AG", manager_user_id=mgr.id,
                  gender="M", base_latitude=28.4, base_longitude=77.0,
                  territory="Gurugram", languages_spoken=["HINDI"],
                  specialization=AgentSpecialization.BOTH, max_cases_per_day=10,
                  status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                  ranking_score=50.0, lifetime_collection_rate=0.5)
    cust = Customer(id=str(uuid.uuid4()), customer_ref="C1", full_name="B",
                    date_of_birth="1990-01-01", gender="M", pan_masked="A1234B",
                    aadhaar_masked="1111", phone_primary="9900000001",
                    address_line1="x", city="Gurugram", state="HR",
                    pincode="122001", latitude=28.4, longitude=77.0,
                    language_preference="HINDI")
    loan = Loan(id=str(uuid.uuid4()), customer_id=cust.id,
                loan_account_number="L1", loan_type=LoanType.PERSONAL,
                branch_code="B1", sanctioned_amount=200000.0,
                disbursed_amount=200000.0, outstanding_principal=150000.0,
                total_outstanding=170000.0, overdue_amount=OVERDUE,
                emi_amount=EMI, interest_rate=15.0, tenure_months=36,
                disbursement_date="2022-01-01", maturity_date="2025-01-01",
                dpd=60, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    case = Case(id=str(uuid.uuid4()), case_number="CS1", customer_id=cust.id,
                loan_id=loan.id, agent_id=agent.id, status=CaseStatus.ASSIGNED,
                priority=CasePriority.HIGH, target_amount=OVERDUE,
                collected_amount=0)
    sibling = Case(id=str(uuid.uuid4()), case_number="CS2", customer_id=cust.id,
                   loan_id=loan.id, agent_id=agent.id, status=CaseStatus.ASSIGNED,
                   priority=CasePriority.MEDIUM, target_amount=OVERDUE,
                   collected_amount=0)
    db.add_all([agent, cust, loan, case, sibling]); db.commit()
    return {"agent": agent, "customer": cust, "loan": loan,
            "case": case, "sibling": sibling}


def _prediction(db, world, *, as_of=AS_OF, overdue=OVERDUE, emi=EMI,
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


def _pay(db, world, when: datetime, amount: float, *, case_key="case",
         status=PaymentStatus.VERIFIED):
    p = Payment(id=str(uuid.uuid4()), case_id=world[case_key].id,
                agent_id=world["agent"].id, amount=amount,
                mode=PaymentMode.CASH, status=status,
                receipt_number=f"RC{uuid.uuid4().hex[:10]}",
                payment_date=when)
    db.add(p); db.commit()
    return p


def _at(day_offset: int, hour: int = 12) -> datetime:
    return datetime.combine(AS_OF + timedelta(days=day_offset),
                            datetime.min.time(),
                            tzinfo=timezone.utc).replace(hour=hour)


def _infer(db, world, prediction):
    """The repayment side alone, through the real function."""
    cases, by_case = lc._build_repayment_inputs(db, world["loan"].id)
    return lc._repayment_label(prediction, cases, by_case)


# ---------------------------------------------------------------------------
# What the repayment rule ACTUALLY does, run rather than read
# ---------------------------------------------------------------------------

def test_the_repayment_rule_binarises_to_any_payment(db, world):
    """One rupee against ₹24,000 owed is a POSITIVE label once binarised.

    This is the finding that most changes how the disagreement numbers read.
    `REPAYMENT_FULL_RATIO` is 0.9, which sounds stricter than the model's 0.8 —
    but it only separates REPAID from PARTIAL, and `POSITIVE_OUTCOMES` holds
    both, so the trainer's bar is `received > 0`.
    """
    p = _prediction(db, world)
    _pay(db, world, _at(5), 1.0)

    outcome, received, denom = _infer(db, world, p)
    assert outcome == OUTCOME_PARTIAL
    assert outcome in POSITIVE_OUTCOMES          # ← the bar, binarised
    assert received == 1.0
    assert denom > 0                             # 0.9 * denom was nowhere near met


def test_the_repayment_rule_counts_rejected_payments(db, world):
    """A REJECTED receipt is counted as money received.

    `attach_outcomes` builds `payments_by_case` with no status filter and
    `_infer_outcome` does not filter either — `_RECOVERED_PAYMENT_STATUSES`
    guards `_received_within`, the recovery pass, and nothing else. Asserted
    here so the comparison's `payment_status` cause has a demonstrated
    mechanism behind it rather than an inferred one.
    """
    p = _prediction(db, world)
    _pay(db, world, _at(5), 9000.0, status=PaymentStatus.REJECTED)

    outcome, received, _ = _infer(db, world, p)
    assert outcome in POSITIVE_OUTCOMES
    assert received == 9000.0


def test_comparison_calls_the_real_repayment_labeller(db, world, monkeypatch):
    """No second copy of the rule: replace it and the comparison must notice."""
    from app.services.repayment_service import RepaymentService

    calls: list = []
    # A staticmethod accessed through the class is a plain function.
    original = RepaymentService._infer_outcome

    def spy(row, cases, payments_by_case):
        calls.append(row.as_of_date)
        return original(row, cases, payments_by_case)

    monkeypatch.setattr(RepaymentService, "_infer_outcome", staticmethod(spy))

    p = _prediction(db, world)
    lc.compare(db, p, as_of=TODAY)
    assert calls == [AS_OF], "the comparison did not go through _infer_outcome"


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------

def test_a_material_payment_agrees(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(5), THRESHOLD)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_status == OutcomeStatus.RECOVERED.value
    assert r.model_outcome == 0 and r.repayment_outcome == 0
    assert r.agrees is True and r.cause is None


def test_no_payment_at_all_agrees(db, world):
    p = _prediction(db, world)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.repayment_status == OUTCOME_NO_PAYMENT
    assert r.model_outcome == 1 and r.repayment_outcome == 1
    assert r.agrees is True and r.cause is None


# ---------------------------------------------------------------------------
# The three disagreement causes, each isolated
# ---------------------------------------------------------------------------

def test_a_payment_below_the_bar_disagrees_on_threshold(db, world):
    """₹500 on a ₹6,400 bar: positive to the repayment rule, negative to the
    model. Nothing else differs — same case, VERIFIED, inside the window."""
    p = _prediction(db, world)
    _pay(db, world, _at(5), 500.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.agrees is False
    assert r.cause == lc.Cause.THRESHOLD
    assert r.model_outcome == 1 and r.repayment_outcome == 0
    assert r.paid_case_verified == 500.0
    assert r.model_threshold == pytest.approx(THRESHOLD)


def test_money_on_a_sibling_case_disagrees_on_scope(db, world):
    """The payment clears the bar in size but lands on the loan's OTHER case.
    The model asks about one case; the repayment rule sums the whole loan."""
    p = _prediction(db, world)
    _pay(db, world, _at(5), 20000.0, case_key="sibling")

    r = lc.compare(db, p, as_of=TODAY)
    assert r.agrees is False
    assert r.cause == lc.Cause.PAYMENT_SCOPE
    assert r.paid_case_verified == 0.0
    assert r.paid_loan_verified == 20000.0


def test_a_rejected_payment_disagrees_on_status(db, world):
    """The only money is a REJECTED receipt: invisible to the model, counted by
    the repayment rule. Attributed to status ahead of scope or threshold —
    under the model's filter no money arrived at all, so neither of those can
    be the explanation."""
    p = _prediction(db, world)
    _pay(db, world, _at(5), 20000.0, status=PaymentStatus.REVERSED)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.agrees is False
    assert r.cause == lc.Cause.PAYMENT_STATUS
    assert r.paid_loan_verified == 0.0
    assert r.paid_loan_all_statuses == 20000.0


# ---------------------------------------------------------------------------
# Censoring, which is asymmetric in both directions
# ---------------------------------------------------------------------------

def test_a_written_off_case_that_paid_is_censored_by_the_model_only(db, world):
    """The model censors on the write-off regardless of what arrived. The
    repayment rule reaches its censoring branch only when NOTHING was received,
    so it labels this positive."""
    p = _prediction(db, world)
    world["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    _pay(db, world, _at(5), 9000.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_status == OutcomeStatus.CENSORED_WRITTEN_OFF.value
    assert r.model_outcome is None
    assert r.repayment_outcome == 0
    assert r.agrees is None
    assert r.cause == lc.Cause.CENSOR_MODEL_ONLY


def test_a_closed_case_with_no_payment_is_censored_by_the_repayment_rule_only(
        db, world):
    """CLOSED is in `_CENSORING_STATUSES` but is not one of the model's
    censoring signals (write-off, settlement, recall note, DECEASED tag), so
    the model calls it a genuine non-recovery."""
    p = _prediction(db, world)
    for key in ("case", "sibling"):
        world[key].status = CaseStatus.CLOSED
    db.commit()

    r = lc.compare(db, p, as_of=TODAY)
    assert r.repayment_status == OUTCOME_CENSORED
    assert r.repayment_outcome is None
    assert r.model_status == OutcomeStatus.NOT_RECOVERED.value
    assert r.model_outcome == 1
    assert r.agrees is None
    assert r.cause == lc.Cause.CENSOR_REPAYMENT_ONLY


# ---------------------------------------------------------------------------
# Apples to apples: one window, one as_of, both sides
# ---------------------------------------------------------------------------

def test_both_sides_ignore_a_payment_one_day_past_the_horizon(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(HORIZON + 1), 20000.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_outcome == 1 and r.repayment_outcome == 1
    assert r.agrees is True
    assert r.repayment_received == 0.0
    assert r.paid_case_verified == 0.0


def test_both_sides_ignore_a_payment_on_the_observation_day(db, world):
    """Money that arrived ON as_of_date happened before the prediction and says
    nothing about what came next. Both rules are half-open at the start."""
    p = _prediction(db, world)
    _pay(db, world, _at(0, hour=23), 20000.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_outcome == 1 and r.repayment_outcome == 1
    assert r.repayment_received == 0.0


def test_the_last_day_of_the_horizon_counts_for_both(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(HORIZON, hour=23), 20000.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_outcome == 0 and r.repayment_outcome == 0
    assert r.repayment_received == 20000.0


def test_a_conflicting_horizon_override_raises(db, world):
    """`_infer_outcome` reads the horizon off settings and takes no argument, so
    an override would move only the model's window. Refused rather than
    silently producing a 30-vs-60-day comparison."""
    with pytest.raises(ValueError, match="model side only"):
        lc.shared_horizon(HORIZON + 30)
    assert lc.shared_horizon(HORIZON) == HORIZON
    assert lc.shared_horizon(None) == HORIZON


def test_both_labels_record_the_same_as_of_and_horizon(db, world):
    p = _prediction(db, world)
    _pay(db, world, _at(5), 500.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.as_of_date == str(AS_OF)
    assert r.horizon_days == HORIZON


# ---------------------------------------------------------------------------
# The direction property
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("amount", [0.0, 1.0, 500.0, THRESHOLD - 1, THRESHOLD,
                                    THRESHOLD + 1, 50000.0])
@pytest.mark.parametrize("status", [PaymentStatus.VERIFIED,
                                    PaymentStatus.REJECTED,
                                    PaymentStatus.PENDING_VERIFICATION])
@pytest.mark.parametrize("case_key", ["case", "sibling"])
def test_model_positive_implies_repayment_positive(db, world, amount, status,
                                                   case_key):
    """The model's countable money is a subset of the repayment rule's and its
    bar is higher, so the model can never be the more generous of the two.

    Asserted over the grid rather than argued from the algebra, and mirrored at
    runtime by `Cause.DIRECTION_ANOMALY` so production data that breaks it is
    flagged instead of quietly attributed to something else.
    """
    p = _prediction(db, world)
    if amount > 0:
        _pay(db, world, _at(5), amount, case_key=case_key, status=status)

    r = lc.compare(db, p, as_of=TODAY)
    if r.model_outcome == 0:
        assert r.repayment_outcome == 0
    assert r.cause != lc.Cause.DIRECTION_ANOMALY


# ---------------------------------------------------------------------------
# The batch: what it writes, and what it must not touch
# ---------------------------------------------------------------------------

def test_compare_all_never_writes_actual_outcome(db, world):
    """The whole safety property in one assertion: the comparison is evidence,
    not an input. `actual_outcome` is what the model is judged on and this pass
    leaves it exactly as it found it."""
    p = _prediction(db, world)
    p.actual_outcome = 1
    p.outcome_status = OutcomeStatus.NOT_RECOVERED.value
    db.commit()
    _pay(db, world, _at(5), 500.0)          # would be POSITIVE to the other rule

    lc.compare_all(db, "recovery_risk", as_of=TODAY)
    db.refresh(p)

    assert p.actual_outcome == 1
    assert p.outcome_status == OutcomeStatus.NOT_RECOVERED.value
    assert p.label_comparison["repayment_outcome"] == 0
    assert p.label_comparison["cause"] == lc.Cause.THRESHOLD


def test_compare_all_skips_unmatured_predictions(db, world):
    """A prediction whose horizon has not elapsed is not comparable, and a
    labeller that counts it would record 'did not pay' about a borrower who has
    not had time to."""
    _prediction(db, world, as_of=TODAY - timedelta(days=2))

    report = lc.compare_all(db, "recovery_risk", as_of=TODAY)
    assert report["total_matured"] == 0
    assert report["comparable_rows"] == 0
    assert report["disagreement_pct"] is None


def test_compare_all_counts_and_attributes_a_mixed_book(db, world):
    """Four predictions, one of each shape, with the counts asserted end to end."""
    agree_p = _prediction(db, world)                     # material payment
    _pay(db, world, _at(1), THRESHOLD)

    # A second loan whose only money is below the bar.
    other = _second_loan(db, world, "L2", "CS3")
    thresh_p = _prediction(db, other)
    _pay(db, other, _at(2), 200.0)

    # A third whose money landed on the sibling case.
    third = _second_loan(db, world, "L3", "CS4", with_sibling=True)
    scope_p = _prediction(db, third)
    _pay(db, third, _at(3), 20000.0, case_key="sibling")

    # A fourth that was written off — censored by the model only.
    fourth = _second_loan(db, world, "L4", "CS5")
    cens_p = _prediction(db, fourth)
    fourth["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    _pay(db, fourth, _at(4), 9000.0)

    report = lc.compare_all(db, "recovery_risk", as_of=TODAY)

    assert report["total_matured"] == 4
    assert report["comparable_rows"] == 3          # the censored row is not comparable
    assert report["agree"] == 1
    assert report["disagree"] == 2
    assert report["disagreement_pct"] == pytest.approx(66.67, abs=0.01)
    assert report["cause_breakdown"] == {
        lc.Cause.THRESHOLD: 1,
        lc.Cause.PAYMENT_SCOPE: 1,
        lc.Cause.PAYMENT_STATUS: 0,
        lc.Cause.CENSOR_MODEL_ONLY: 1,
        lc.Cause.CENSOR_REPAYMENT_ONLY: 0,
        lc.Cause.DIRECTION_ANOMALY: 0,
        lc.Cause.OTHER: 0,
    }
    assert report["censored_either_side"] == 1
    assert report["censoring_pct"] == pytest.approx(25.0)
    assert report["model_definition"]["labelled"] == 3
    assert report["model_definition"]["recovered"] == 1
    assert report["model_definition"]["censored"] == 1
    # Every side of the loan paid something except none — the repayment rule
    # calls three of the four recovered, which is the whole point of measuring.
    assert report["repayment_definition"]["recovered"] == 4
    assert len(report["examples"]) == 2
    assert {e["prediction_id"] for e in report["examples"]} == {
        thresh_p.id, scope_p.id}
    assert agree_p.id and cens_p.id                # both were visited


def test_every_cause_is_reported_even_at_zero(db, world):
    """A `Counter` omits what never happened, which would make 'no
    payment_status disagreements' and 'the comparison never ran' the same
    output — and the second is what a stale or half-wired report looks like.
    All four categories the decision rests on must be present as numbers."""
    _prediction(db, world)
    report = lc.compare_all(db, "recovery_risk", as_of=TODAY)

    assert set(report["cause_breakdown"]) == set(lc.ALL_CAUSES)
    assert all(v == 0 for v in report["cause_breakdown"].values())
    for cause in (lc.Cause.THRESHOLD, lc.Cause.PAYMENT_SCOPE,
                  lc.Cause.PAYMENT_STATUS, lc.Cause.CENSOR_MODEL_ONLY,
                  lc.Cause.CENSOR_REPAYMENT_ONLY):
        assert cause in report["cause_breakdown"]


def test_a_row_censored_by_both_sides_is_counted_once(db, world):
    """`censoring_pct` is a union. Summing the two censoring CAUSES would miss
    it entirely — a mutual censor produces no cause at all, because neither
    side is the odd one out."""
    p = _prediction(db, world)
    for key in ("case", "sibling"):
        world[key].status = CaseStatus.WRITTEN_OFF
    db.commit()

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_status == OutcomeStatus.CENSORED_WRITTEN_OFF.value
    assert r.repayment_status == OUTCOME_CENSORED
    assert r.cause is None                      # neither side is the outlier

    report = lc.compare_all(db, "recovery_risk", as_of=TODAY)
    assert report["censored_either_side"] == 1
    assert report["censoring_pct"] == pytest.approx(100.0)
    assert sum(report["cause_breakdown"][c] for c in lc.CENSORING_CAUSES) == 0


def test_disagreement_pct_is_over_comparable_rows_not_matured_ones(db, world):
    """A row only one side labelled cannot agree or disagree. Counting it in
    the denominator would dilute the rate with rows never in the question."""
    _prediction(db, world)                       # comparable, agrees
    censored = _second_loan(db, world, "L9", "CS9")
    _prediction(db, censored)
    censored["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    _pay(db, censored, _at(3), 9000.0)           # model censors, repayment does not

    report = lc.compare_all(db, "recovery_risk", as_of=TODAY)
    assert report["total_matured"] == 2
    assert report["comparable_rows"] == 1
    assert report["disagree"] == 0
    assert report["disagreement_pct"] == 0.0     # 0 of 1, not 0 of 2


def test_the_report_names_both_binary_rules(db, world):
    """The counts are unreadable without them: `repayment.recovered` means
    'some money arrived', not 'repaid 90% of what was owed'."""
    _prediction(db, world)
    report = lc.compare_all(db, "recovery_risk", as_of=TODAY)

    assert report["definitions"]["model"] == lc.MODEL_BINARY_RULE
    assert report["definitions"]["repayment"] == lc.REPAYMENT_BINARY_RULE
    assert "does not gate the positive class" in report["definitions"]["note"]


def test_a_prediction_with_no_baseline_is_not_compared(db, world):
    """No frozen overdue/EMI means no model threshold, so there is no model
    label to disagree with — reported as unusable rather than guessed at."""
    p = _prediction(db, world, baseline=False)
    p.features = {"dpd": 60.0}
    db.commit()
    _pay(db, world, _at(5), 500.0)

    r = lc.compare(db, p, as_of=TODAY)
    assert r.model_status == OutcomeStatus.NO_BASELINE.value
    assert r.model_outcome is None
    assert r.agrees is None
    assert r.model_threshold is None


# ---------------------------------------------------------------------------
# The nightly wiring
# ---------------------------------------------------------------------------

def test_the_nightly_digest_keeps_the_counts_and_drops_the_examples(db, world):
    """The task's return value goes to the Celery result backend and the log
    line. Per-row detail is already on `model_predictions.label_comparison`,
    which is where it should be read from."""
    from app.workers.tasks.model_outcomes import _comparison_digest

    _prediction(db, world)
    _pay(db, world, _at(5), 500.0)

    digest = _comparison_digest(db, "recovery_risk")
    assert "examples" not in digest
    assert digest["disagree"] == 1
    assert digest["cause_breakdown"][lc.Cause.THRESHOLD] == 1
    assert set(digest["cause_breakdown"]) == set(lc.ALL_CAUSES)


def test_a_broken_comparison_does_not_fail_the_outcome_task(db, world,
                                                            monkeypatch):
    """The comparison is a monitoring read-out beside the labeller, not part of
    it. Labels that were computed and committed correctly must not be reported
    as a failed run because a diagnostic raised — and the failure must still be
    visible in the returned summary rather than vanishing."""
    import app.workers.tasks.model_outcomes as task_mod

    _prediction(db, world)
    monkeypatch.setattr(task_mod, "SessionLocal", lambda: db, raising=False)
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(task_mod, "_comparison_digest",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    result = task_mod.attach_model_outcomes.apply(
        args=("recovery_risk",)).get()

    assert result["label_comparison"] == {"error": "RuntimeError"}
    assert result["labelled"] >= 1          # the real labelling still happened


# ---------------------------------------------------------------------------
# One writer of the label, and one only
# ---------------------------------------------------------------------------

def test_only_one_module_writes_the_outcome_label():
    """A tripwire, and labelled as one: it reads source, it does not run it.

    `monitor.py` used to carry a second `attach_outcomes` that set
    `actual_outcome` WITHOUT `outcome_definition_version` or `outcome_status`,
    so a caller reaching for it would have written labels with no record of
    which rule produced them. It was dead, which is why nothing failed; it was
    removed on 2026-09-09.

    A structural check cannot prove behaviour — that lesson cost a `NameError`
    in production when `assert "..." in source` passed against the wrong
    function. What it CAN do is notice a third copy being added, which is what
    found the seventh DPD-bucket spelling after reading found six. The
    behavioural guarantees live in `tests/test_model_outcomes.py`.
    """
    import ast
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    writers = set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `<anything>.actual_outcome = ...`
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Attribute)
                            and tgt.attr == "actual_outcome"):
                        writers.add(str(path.relative_to(app_dir)).replace("\\", "/"))

    assert writers == {"ml/pipeline/outcomes.py"}, (
        f"`actual_outcome` must be written in exactly one place. Found: "
        f"{sorted(writers)}. If a new writer is legitimate it must also stamp "
        f"outcome_definition_version and outcome_status, or the column stops "
        f"being filterable by which rule produced it."
    )


# ---------------------------------------------------------------------------
# Helper for the multi-loan book
# ---------------------------------------------------------------------------

def _second_loan(db, world, lan: str, case_no: str, *, with_sibling=False):
    loan = Loan(id=str(uuid.uuid4()), customer_id=world["customer"].id,
                loan_account_number=lan, loan_type=LoanType.PERSONAL,
                branch_code="B1", sanctioned_amount=200000.0,
                disbursed_amount=200000.0, outstanding_principal=150000.0,
                total_outstanding=170000.0, overdue_amount=OVERDUE,
                emi_amount=EMI, interest_rate=15.0, tenure_months=36,
                disbursement_date="2022-01-01", maturity_date="2025-01-01",
                dpd=60, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    case = Case(id=str(uuid.uuid4()), case_number=case_no,
                customer_id=world["customer"].id, loan_id=loan.id,
                agent_id=world["agent"].id, status=CaseStatus.ASSIGNED,
                priority=CasePriority.HIGH, target_amount=OVERDUE,
                collected_amount=0)
    rows = [loan, case]
    out = {"agent": world["agent"], "customer": world["customer"],
           "loan": loan, "case": case}
    if with_sibling:
        sib = Case(id=str(uuid.uuid4()), case_number=case_no + "B",
                   customer_id=world["customer"].id, loan_id=loan.id,
                   agent_id=world["agent"].id, status=CaseStatus.ASSIGNED,
                   priority=CasePriority.MEDIUM, target_amount=OVERDUE,
                   collected_amount=0)
        rows.append(sib)
        out["sibling"] = sib
    db.add_all(rows); db.commit()
    return out
