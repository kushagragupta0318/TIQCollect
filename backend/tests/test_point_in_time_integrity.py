# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-03. The leakage contract for the synthetic-history
# experiment, asserted as tests rather than described in a document.
#
# The synthetic book exists so the pipeline can be validated without real
# borrower outcomes. That is only worth anything if the historical rows it
# produces are honest — if a payment made after a snapshot can reach that
# snapshot's features, the whole exercise measures nothing except how well the
# model can read the answer.
#
# Four properties are pinned here, one per section:
#
#   1. borrower features are bounded by as_of
#   2. EB is bounded by as_of
#   3. the feature window and the label window do not overlap
#   4. recomputing the same historical date twice gives the same answer
#
# WHAT THESE DO NOT PROVE, stated because it is the crux of the design:
# build_features reads loan.dpd, loan.overdue_amount and loan.total_outstanding
# STRAIGHT OFF THE LOAN ROW, which is mutable and carries no history. No test
# can make those point-in-time after the fact. That is precisely why
# scripts/generate_synthetic_repayment_history.py simulates FORWARD and
# snapshots at the moment the state is current, instead of backfilling. The
# last section here pins that distinction so nobody later "simplifies" the
# generator into a backfill.
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster
from app.models.base import Base
from app.models.case import Case
from app.models.loan import DPDBucket, Loan, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTPStatus
from app.models.visit import VisitOutcome
from app.services.repayment_service import RepaymentService

SVC = RepaymentService(db=None)
AS_OF = date(2026, 5, 1)


def _loan(**over):
    base = dict(
        id="loan-1", customer_id="cust-1", dpd=62, loan_type=LoanType.PERSONAL,
        emi_amount=14000.0, overdue_amount=42000.0, outstanding_principal=380000.0,
        total_outstanding=421000.0, penal_charges=2100.0, last_payment_amount=8000.0,
        last_payment_date="2026-03-02", legal_status="NONE", settlement_status="NONE",
        npa_flag=False,
    )
    base.update(over)
    return NS(**base)


def _customer(**over):
    base = dict(id="cust-1", cibil_score=612, customer_segment="SALARIED",
                is_hostile=False, fraud_flag=False)
    base.update(over)
    return NS(**base)


def _case(target=14000.0):
    return NS(id="case-1", target_amount=target)


def _visit(when: date, outcome=VisitOutcome.PTP, met=True):
    return NS(check_in_time=datetime.combine(when, datetime.min.time()) + timedelta(hours=11),
              customer_met=met, outcome=outcome, case_id="case-1")


def _ptp(committed: date, status=PTPStatus.ACTIVE, updated: date | None = None):
    return NS(committed_date=committed, status=status, case_id="case-1",
              updated_at=(datetime.combine(updated, datetime.min.time())
                          if updated else None))


def _payment(when: date, amount=5000.0, status=PaymentStatus.VERIFIED):
    return NS(payment_date=datetime.combine(when, datetime.min.time()) + timedelta(hours=13),
              amount=amount, status=status, case_id="case-1")


# ── 1. Borrower features are bounded by as_of ────────────────────────────────

def test_a_payment_after_as_of_does_not_change_the_feature_vector():
    """The single most important property in the experiment.

    Two feature builds for the same date, differing only in whether a payment
    exists 5 days AFTER it. That payment is part of the label. If it moved a
    single feature, every metric downstream would be inflated.
    """
    args = dict(cases=[_case()], visits=[_visit(AS_OF - timedelta(days=10))],
                ptps=[], payments=[_payment(AS_OF - timedelta(days=20))])
    without = SVC.build_features(_loan(), _customer(), AS_OF, **args)

    with_future = dict(args)
    with_future["payments"] = args["payments"] + [_payment(AS_OF + timedelta(days=5), 9000.0)]
    after = SVC.build_features(_loan(), _customer(), AS_OF, **with_future)

    assert without == after


def test_a_visit_after_as_of_does_not_change_the_feature_vector():
    args = dict(cases=[_case()], visits=[_visit(AS_OF - timedelta(days=10))],
                ptps=[], payments=[])
    before = SVC.build_features(_loan(), _customer(), AS_OF, **args)

    args["visits"] = args["visits"] + [_visit(AS_OF + timedelta(days=3),
                                              VisitOutcome.PAID_FULL)]
    after = SVC.build_features(_loan(), _customer(), AS_OF, **args)

    assert before == after
    assert after["visits"] == 1


def test_a_promise_made_after_as_of_does_not_change_the_feature_vector():
    args = dict(cases=[_case()], visits=[], payments=[],
                ptps=[_ptp(AS_OF - timedelta(days=15), PTPStatus.HONORED)])
    before = SVC.build_features(_loan(), _customer(), AS_OF, **args)

    args["ptps"] = args["ptps"] + [_ptp(AS_OF + timedelta(days=9), PTPStatus.HONORED)]
    after = SVC.build_features(_loan(), _customer(), AS_OF, **args)

    assert before == after
    assert after["ptps_resolved"] == 1


def test_payment_recency_refuses_a_loan_column_that_post_dates_as_of():
    """Loan.last_payment_date is overwritten in place, so on any historical row
    it can name a payment that had not happened yet. It must be refused, and
    the refusal recorded rather than silently swallowed."""
    loan = _loan(last_payment_date=str(AS_OF + timedelta(days=12)))
    feats = SVC.build_features(loan, _customer(), AS_OF,
                               cases=[_case()], visits=[], ptps=[], payments=[])

    assert feats["days_since_last_payment"] is None
    assert feats["_payment_recency_source"] == "LOAN_COLUMN_REFUSED_FUTURE"


# ── 2. EB is bounded by as_of ────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_eb(db, payments: list[tuple[str, float, date]]):
    db.add(Loan(
        id="loan-1", loan_account_number="LN1", customer_id="cust-1",
        loan_type=LoanType.PERSONAL, bank_name="B", branch_code="BR",
        sanctioned_amount=1e5, disbursed_amount=1e5, outstanding_principal=8e4,
        total_outstanding=9e4, overdue_amount=1e4, emi_amount=5e3,
        disbursement_date="2025-01-01", maturity_date="2028-01-01",
        dpd=45, dpd_bucket=DPDBucket.BUCKET_2, interest_rate=12.0))
    db.add(Case(id="case-1", case_number="C1", customer_id="cust-1",
                loan_id="loan-1", target_amount=10000.0))
    for i, (agent, amount, when) in enumerate(payments):
        db.add(Payment(
            id=f"p{i}", case_id="case-1", agent_id=agent, amount=amount,
            mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
            receipt_number=f"R{i:06d}",
            payment_date=datetime.combine(when, datetime.min.time()) + timedelta(hours=12)))
    db.commit()


def test_eb_at_a_historical_date_ignores_everything_that_came_later(db):
    """Fitting EB as of T must give the same answer whether or not the ledger
    already contains the months after T. This is what makes eb_shrunk_win a
    legitimate training feature rather than a leak."""
    t = date(2026, 5, 1)
    _seed_eb(db, [("agent-A", 3000.0, t - timedelta(days=20))])
    before = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    snapshot = dict(before.agent_observations[("agent-A", "PERSONAL", "BUCKET_2")])

    # The future arrives.
    db.add(Payment(id="p-late", case_id="case-1", agent_id="agent-A", amount=7000.0,
                   mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="R-LATE",
                   payment_date=datetime.combine(t + timedelta(days=10),
                                                 datetime.min.time())))
    db.commit()

    after = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    assert after.agent_observations[("agent-A", "PERSONAL", "BUCKET_2")] == snapshot

    # ...and is visible once the clock moves past it.
    later = EmpiricalBayesAgentAdjuster().fit_from_db(
        db, as_of=t + timedelta(days=30), lookback_days=180)
    assert later.agent_observations[("agent-A", "PERSONAL", "BUCKET_2")]["recovered"] == 10000.0


# ── 3. Feature window and label window do not overlap ────────────────────────

def test_the_feature_window_and_the_label_window_share_no_day():
    """build_features admits `payment_date.date() <= as_of`; _infer_outcome
    counts `as_of_date < payment_date.date() <= as_of + horizon`. The boundary
    has to be exactly here — one day either way and a payment is either counted
    twice or lost."""
    horizon = settings.REPAYMENT_OUTCOME_HORIZON_DAYS
    on_the_day = _payment(AS_OF)
    next_day = _payment(AS_OF + timedelta(days=1))

    feats = SVC.build_features(_loan(), _customer(), AS_OF, cases=[_case()],
                               visits=[], ptps=[], payments=[on_the_day, next_day])
    # Only the as_of-day payment is a feature.
    assert feats["amount_paid_in_window"] == pytest.approx(on_the_day.amount)

    row = NS(as_of_date=AS_OF, loan_id="loan-1")
    outcome, amount = SVC._infer_outcome(
        row, [NS(id="case-1", target_amount=14000.0, status="ASSIGNED")],
        {"case-1": [on_the_day, next_day]})
    # Only the next-day payment is a label.
    assert amount == pytest.approx(next_day.amount)
    assert horizon >= 1


def test_eb_window_uses_the_same_boundary_as_the_feature_builder(db):
    """Three components have to agree on what "as of a date" means. Two of them
    agreeing is not enough."""
    t = date(2026, 5, 1)
    _seed_eb(db, [("agent-A", 4000.0, t), ("agent-A", 6000.0, t + timedelta(days=1))])
    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    obs = eb.agent_observations[("agent-A", "PERSONAL", "BUCKET_2")]
    # The as_of day itself is IN, exactly as build_features has it.
    assert obs["recovered"] == pytest.approx(4000.0)


# ── 4. Reconstruction is deterministic ───────────────────────────────────────

def test_rebuilding_the_same_historical_date_gives_an_identical_vector():
    args = dict(cases=[_case()],
                visits=[_visit(AS_OF - timedelta(days=d)) for d in (5, 30, 90)],
                ptps=[_ptp(AS_OF - timedelta(days=20), PTPStatus.HONORED)],
                payments=[_payment(AS_OF - timedelta(days=12))])
    first = SVC.build_features(_loan(), _customer(), AS_OF, **args)
    second = SVC.build_features(_loan(), _customer(), AS_OF, **args)
    assert first == second
    assert first["_as_of"] == AS_OF.isoformat()


def test_eb_refits_deterministically_for_a_historical_date(db):
    t = date(2026, 5, 1)
    _seed_eb(db, [("agent-A", 3000.0, t - timedelta(days=20)),
                  ("agent-B", 5000.0, t - timedelta(days=40))])
    a = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    b = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    assert a.agent_observations == b.agent_observations
    assert a.segment_priors == b.segment_priors
    assert a.global_prior == b.global_prior


# ── The limit of the above, pinned so it is not forgotten ────────────────────

def test_mutable_loan_columns_are_NOT_point_in_time_and_that_is_why_we_simulate():
    """This test asserts a LIMITATION, deliberately.

    dpd, overdue_amount and total_outstanding are read straight off the loan
    row. Change the row and the "historical" feature vector changes with it —
    no filter can prevent that, because the column holds one value and no
    history.

    That is the entire reason the synthetic generator walks forward in
    simulated time and snapshots while the state is current, rather than
    writing dated rows from today's book. If this test ever starts failing
    because someone made these columns historical, the generator can be
    simplified. Until then it must not be.
    """
    args = dict(cases=[_case()], visits=[], ptps=[], payments=[])
    at_62 = SVC.build_features(_loan(dpd=62), _customer(), AS_OF, **args)
    at_150 = SVC.build_features(_loan(dpd=150), _customer(), AS_OF, **args)

    assert at_62["dpd"] == 62
    assert at_150["dpd"] == 150
    assert at_62 != at_150, (
        "loan.dpd has become point-in-time; the forward simulation could now "
        "be replaced by a backfill"
    )


def test_the_leak_guard_still_refuses_this_systems_own_outputs():
    """_raise_if_leaked is the backstop for everything above."""
    with pytest.raises(ValueError, match="Label leakage"):
        SVC._raise_if_leaked({"dpd": 30, "recovery_potential": "HIGH"})
