# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-03. Covers ml/empirical_bayes.py.
#
# This file exists because of three defects that all shared one property: they
# were INVISIBLE. `lookback_days` was accepted and ignored, so no caller could
# tell the window was not applied. There was no `as_of`, so nothing forced a
# historical fit to be honest. And the aggregation grouped by target_amount as
# a stand-in for case identity, so two cases with the same target silently
# merged and the recovery rate came out higher than reality. None of the three
# raised, logged, or failed a test — they just returned a slightly wrong number
# that everything downstream believed.
#
# So these are the tests you cannot write after the fact: each one asserts a
# property that is cheap to break and expensive to notice.
#
# Same in-memory SQLite style as test_otp_service.py. The EB query touches only
# payments -> cases -> loans, and SQLite does not enforce foreign keys by
# default, so agents and customers need not exist for these fixtures.
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster
from app.models.base import Base
from app.models.case import Case
from app.models.loan import DPDBucket, Loan, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus

AS_OF = date(2026, 9, 3)
SEG = ("PERSONAL", "BUCKET_2")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


_seq = {"n": 0}


def _next() -> int:
    _seq["n"] += 1
    return _seq["n"]


def make_loan(db, loan_id: str, loan_type=LoanType.PERSONAL, bucket=DPDBucket.BUCKET_2) -> Loan:
    n = _next()
    loan = Loan(
        id=loan_id, loan_account_number=f"LN{n:06d}", customer_id="cust-1",
        loan_type=loan_type, bank_name="Test Bank", branch_code="BR01",
        sanctioned_amount=100000.0, disbursed_amount=100000.0,
        outstanding_principal=80000.0, total_outstanding=90000.0,
        overdue_amount=10000.0, emi_amount=5000.0,
        disbursement_date="2025-01-01", maturity_date="2028-01-01",
        dpd=45, dpd_bucket=bucket, interest_rate=12.0,
    )
    db.add(loan)
    return loan


def make_case(db, case_id: str, loan_id: str, target: float) -> Case:
    n = _next()
    case = Case(
        id=case_id, case_number=f"CASE{n:06d}", customer_id="cust-1",
        loan_id=loan_id, target_amount=target,
    )
    db.add(case)
    return case


def make_payment(db, case_id: str, agent_id: str, amount: float, when: date,
                 status=PaymentStatus.VERIFIED) -> Payment:
    n = _next()
    pay = Payment(
        id=f"pay-{n}", case_id=case_id, agent_id=agent_id, amount=amount,
        mode=PaymentMode.CASH, status=status, receipt_number=f"RCP{n:08d}",
        payment_date=datetime.combine(when, datetime.min.time()) + timedelta(hours=12),
    )
    db.add(pay)
    return pay


def rate_for(eb: EmpiricalBayesAgentAdjuster, agent_id: str, seg=SEG) -> float:
    """The agent's OWN observed rate for a segment, before shrinkage.

    Read from agent_observations rather than through get_segment_multiplier so
    the assertions are about what was aggregated, not about what shrinkage then
    did to it — min_sample_threshold would otherwise hide most of these cases
    behind the segment prior.
    """
    obs = eb.agent_observations.get((agent_id, seg[0], seg[1]))
    return obs["recovered"] / obs["target"] if obs else 0.0


# ── Test 1 — the lookback window is actually applied ─────────────────────────

def test_payments_older_than_the_lookback_are_excluded(db):
    """The defect this replaces: lookback_days was in the signature and nowhere
    in the query, so every payment ever recorded was read."""
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    # Inside a 30-day window ending 2026-09-03.
    make_payment(db, "case-1", "agent-A", 3000.0, AS_OF - timedelta(days=10))
    # Well outside it.
    make_payment(db, "case-1", "agent-A", 7000.0, AS_OF - timedelta(days=200))
    db.commit()

    narrow = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=30)
    wide = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=365)

    assert rate_for(narrow, "agent-A") == pytest.approx(0.30)
    assert rate_for(wide, "agent-A") == pytest.approx(1.00)


def test_lookback_boundary_is_inclusive_at_the_start(db):
    """A payment exactly `lookback_days` before as_of is IN the window.

    Pinned because an off-by-one here is silent: the rate moves slightly and
    nothing complains.
    """
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    make_payment(db, "case-1", "agent-A", 5000.0, AS_OF - timedelta(days=30))
    db.commit()

    inside = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=30)
    outside = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=29)

    assert rate_for(inside, "agent-A") == pytest.approx(0.50)
    assert rate_for(outside, "agent-A") == 0.0


def test_lookback_days_must_be_positive(db):
    with pytest.raises(ValueError, match="lookback_days"):
        EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=0)


# ── Test 2 — nothing after as_of may influence the fit ───────────────────────

def test_payments_after_as_of_have_zero_influence(db):
    """The whole point of `as_of`. A payment made after the snapshot date is
    part of the LABEL; letting it into the feature makes the model look better
    in evaluation than it can ever be in production."""
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    t = date(2026, 8, 1)
    make_payment(db, "case-1", "agent-A", 2000.0, t - timedelta(days=5))    # before T
    make_payment(db, "case-1", "agent-A", 8000.0, t + timedelta(days=5))    # after T
    db.commit()

    at_t = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    assert rate_for(at_t, "agent-A") == pytest.approx(0.20)


def test_as_of_day_itself_is_visible_and_the_next_day_is_not(db):
    """The boundary convention, pinned against RepaymentService.

    build_features admits `payment_date.date() <= as_of`, and _infer_outcome
    opens the label window at `as_of_date <` — strictly after. Matching that
    exactly is what stops one payment being counted as both a feature and a
    label.
    """
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    t = date(2026, 8, 1)
    make_payment(db, "case-1", "agent-A", 4000.0, t)                      # on T
    make_payment(db, "case-1", "agent-A", 6000.0, t + timedelta(days=1))  # T + 1
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=t, lookback_days=180)
    assert rate_for(eb, "agent-A") == pytest.approx(0.40)


def test_as_of_as_a_datetime_is_a_strict_upper_bound(db):
    """A caller with a real timestamp gets literal `< as_of`, not end-of-day."""
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    day = date(2026, 8, 1)
    make_payment(db, "case-1", "agent-A", 3000.0, day)   # stored at 12:00
    db.commit()

    before = EmpiricalBayesAgentAdjuster().fit_from_db(
        db, as_of=datetime(2026, 8, 1, 9, 0), lookback_days=180)
    after = EmpiricalBayesAgentAdjuster().fit_from_db(
        db, as_of=datetime(2026, 8, 1, 18, 0), lookback_days=180)

    assert rate_for(before, "agent-A") == 0.0
    assert rate_for(after, "agent-A") == pytest.approx(0.30)


def test_as_of_is_mandatory_and_keyword_only(db):
    """Structural, not stylistic. There must be no call shape that silently
    produces a non-point-in-time fit — the way the old signature did."""
    with pytest.raises(TypeError):
        EmpiricalBayesAgentAdjuster().fit_from_db(db)          # type: ignore[call-arg]
    with pytest.raises(TypeError):
        EmpiricalBayesAgentAdjuster().fit_from_db(db, AS_OF)   # type: ignore[misc]
    with pytest.raises(TypeError):
        EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of="2026-09-03")  # type: ignore[arg-type]


# ── Test 3 — cases with identical targets stay separate observations ─────────

def test_identical_targets_remain_separate_cases(db):
    """Two cases, same target, same agent, same segment.

    Under the old grouping these merged into ONE row: 4000 + 3000 recovered
    against a single 5000 target, i.e. 140%. Correctly, it is 7000 / 10000.
    """
    make_loan(db, "loan-1")
    make_loan(db, "loan-2")
    make_case(db, "case-A", "loan-1", target=5000.0)
    make_case(db, "case-B", "loan-2", target=5000.0)
    make_payment(db, "case-A", "agent-A", 4000.0, AS_OF - timedelta(days=5))
    make_payment(db, "case-B", "agent-A", 3000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)

    obs = eb.agent_observations[("agent-A", *SEG)]
    assert obs["target"] == pytest.approx(10000.0)
    assert obs["recovered"] == pytest.approx(7000.0)
    assert rate_for(eb, "agent-A") == pytest.approx(0.70)
    # Two cases is two pieces of evidence about this agent.
    assert obs["n"] == 2


def test_several_payments_on_one_case_are_one_observation(db):
    """n drives the shrinkage weight, so it has to mean something.

    A case settled in three instalments tells us once whether this agent
    recovers on work like this — not three times. Counting payments (as the old
    code did) overstated the evidence and shrank toward the prior too little.
    """
    make_loan(db, "loan-1")
    make_case(db, "case-A", "loan-1", target=9000.0)
    for _ in range(3):
        make_payment(db, "case-A", "agent-A", 3000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)
    obs = eb.agent_observations[("agent-A", *SEG)]

    assert obs["n"] == 1
    assert obs["target"] == pytest.approx(9000.0)      # counted ONCE, not 3x
    assert obs["recovered"] == pytest.approx(9000.0)


# ── Test 4 — the >100% regression ────────────────────────────────────────────

def test_duplicate_targets_cannot_manufacture_over_100_percent(db):
    """Regression for the 15-of-315 impossible cells found on the live book.

    `collected <= target` is an enforced invariant, so no genuine recovery rate
    can exceed 1.0. Any cell above it was an artefact of duplicate-target
    grouping. This asserts the arithmetic makes it impossible, rather than that
    something clamped it afterwards — a clamp would hide the same bug.
    """
    make_loan(db, "loan-1")
    make_loan(db, "loan-2")
    make_loan(db, "loan-3")
    # Three cases sharing one target value — exactly what the EMI rescale
    # produced by rounding targets to the nearest 10.
    for i, loan in enumerate(("loan-1", "loan-2", "loan-3"), start=1):
        make_case(db, f"case-{i}", loan, target=5000.0)
        make_payment(db, f"case-{i}", "agent-A", 5000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)

    obs = eb.agent_observations[("agent-A", *SEG)]
    assert obs["target"] == pytest.approx(15000.0)     # 3 targets, not 1
    assert obs["recovered"] == pytest.approx(15000.0)
    assert rate_for(eb, "agent-A") == pytest.approx(1.0)

    for key, cell in eb.agent_observations.items():
        assert cell["recovered"] <= cell["target"] + 1e-6, f"{key} exceeds 100%"
    for key, prior in eb.segment_priors.items():
        assert prior <= 1.0, f"segment prior {key} exceeds 100%"


def test_partial_recovery_across_duplicate_targets_is_not_inflated(db):
    """The realistic shape of the bug: full recovery on one case and partial on
    another used to read as ~150% for the pair. It is 75%."""
    make_loan(db, "loan-1")
    make_loan(db, "loan-2")
    make_case(db, "case-A", "loan-1", target=4000.0)
    make_case(db, "case-B", "loan-2", target=4000.0)
    make_payment(db, "case-A", "agent-A", 4000.0, AS_OF - timedelta(days=5))
    make_payment(db, "case-B", "agent-A", 2000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)
    assert rate_for(eb, "agent-A") == pytest.approx(0.75)


# ── Test 5 — a later as_of picks up the payments in between ──────────────────

def test_a_later_as_of_includes_the_intervening_payments(db):
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    early, late = date(2026, 8, 1), date(2026, 9, 1)
    make_payment(db, "case-1", "agent-A", 2000.0, early - timedelta(days=1))
    make_payment(db, "case-1", "agent-A", 5000.0, early + timedelta(days=10))
    db.commit()

    at_early = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=early, lookback_days=180)
    at_late = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=late, lookback_days=180)

    assert rate_for(at_early, "agent-A") == pytest.approx(0.20)
    assert rate_for(at_late, "agent-A") == pytest.approx(0.70)


# ── Housekeeping the old implementation got wrong ────────────────────────────

def test_refitting_the_same_instance_does_not_double_count(db):
    """fit_from_db accumulated into self.agent_observations without clearing.

    Harmless while every caller fitted once. Not harmless for the backfill,
    which fits the same adjuster at one as_of after another.
    """
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    make_payment(db, "case-1", "agent-A", 6000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster()
    eb.fit_from_db(db, as_of=AS_OF, lookback_days=180)
    first = dict(eb.agent_observations[("agent-A", *SEG)])
    eb.fit_from_db(db, as_of=AS_OF, lookback_days=180)

    assert eb.agent_observations[("agent-A", *SEG)] == first


def test_only_verified_payments_count(db):
    """Unchanged behaviour, pinned because the window filter now sits beside it
    and a future edit to one could drop the other."""
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    make_payment(db, "case-1", "agent-A", 4000.0, AS_OF - timedelta(days=5))
    make_payment(db, "case-1", "agent-A", 6000.0, AS_OF - timedelta(days=5),
                 status=PaymentStatus.PENDING_VERIFICATION)
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)
    assert rate_for(eb, "agent-A") == pytest.approx(0.40)


def test_evidence_count_is_reported_separately(db):
    """A model handed shrunk_win without this cannot tell a measured rate from
    the segment average returned in its place."""
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    make_payment(db, "case-1", "agent-A", 4000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)

    assert eb.get_segment_evidence("agent-A", *SEG) == 1
    assert eb.get_segment_evidence("agent-NOBODY", *SEG) == 0


def test_shrinkage_still_falls_back_to_the_prior_under_the_threshold(db):
    """The shrinkage contract itself is unchanged by these fixes."""
    make_loan(db, "loan-1")
    make_case(db, "case-1", "loan-1", target=10000.0)
    make_payment(db, "case-1", "agent-A", 10000.0, AS_OF - timedelta(days=5))
    db.commit()

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=180)
    shrunk, prior, mult = eb.get_segment_multiplier("agent-A", *SEG)

    # One observation is below min_sample_threshold=5, so the agent's own
    # perfect rate is not used: the prior is returned and the multiplier is
    # neutral.
    assert shrunk == prior
    assert mult == 1.0


def test_the_fitted_window_is_recorded(db):
    """So a backfilled row can state the window it was computed over instead of
    leaving a later reader to assume it."""
    eb = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=AS_OF, lookback_days=30)
    start, cutoff = eb.fitted_window

    assert eb.fitted_as_of == AS_OF
    assert start == datetime(2026, 8, 4)          # 2026-09-03 minus 30 days
    assert cutoff == datetime(2026, 9, 4)         # exclusive: end of as_of day
