# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-21. Covers the outcome labeller in
# services/repayment_service.py — the half of the pipeline that turns a stored
# prediction into a training example.
#
# The censoring tests carry the weight here, and the harm they prevent is
# specific: a bank recall, a write-off or an administrative close means no money
# arrived for a reason that has nothing to do with the borrower's willingness to
# pay. Label those `y=0` and every model trained on the table learns to blame
# borrowers for the bank's own decisions — and it will underrate exactly the
# accounts that were pulled back for good reasons. Nothing about that failure is
# visible in an accuracy figure.
#
# Same no-DB style as the rest: the inference rule is a pure staticmethod, so it
# is exercised directly over SimpleNamespace stand-ins.
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.core.config import settings
from app.models.case import Case, CasePriority, CaseStatus
from app.models.repayment_snapshot import (
    CENSORED_OUTCOMES, OUTCOME_CENSORED, OUTCOME_NO_PAYMENT, OUTCOME_PARTIAL,
    OUTCOME_REPAID, POSITIVE_OUTCOMES,
)
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.customer import RiskCategory
from app.models.repayment_snapshot import RepaymentSnapshot
from app.services.repayment_service import RepaymentService, _CENSORING_STATUSES
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

SVC = RepaymentService(db=None)
AS_OF = date(2026, 7, 1)
HORIZON = settings.REPAYMENT_OUTCOME_HORIZON_DAYS


def snap(**over):
    base = dict(loan_id=test_id("l1"), case_id=test_id("c1"), as_of_date=AS_OF, outcome=None)
    base.update(over)
    return NS(**base)


def case(status=CaseStatus.IN_PROGRESS, target=10_000.0, cid=test_id("c1")):
    return NS(id=cid, status=status, target_amount=target)


def payment(*, days_after, amount, cid=test_id("c1"), status="VERIFIED"):
    """A received payment. VERIFIED by default: these tests are about WINDOWS,
    and the status rule has its own tests further down. Payment.status is NOT
    NULL on the real model, so a stub without one tests a shape that cannot
    exist — which is exactly how the status filter slipped in unnoticed."""
    return NS(case_id=cid, amount=amount, status=status,
              payment_date=datetime.combine(AS_OF + timedelta(days=days_after),
                                            datetime.min.time(), tzinfo=timezone.utc))


def infer(cases, payments):
    by_case = {}
    for p in payments:
        by_case.setdefault(p.case_id, []).append(p)
    return SVC._infer_outcome(snap(), cases, by_case)


# ── Positive outcomes ────────────────────────────────────────────────────────
def test_full_payment_in_window_is_repaid():
    outcome, amount = infer([case(target=10_000.0)],
                            [payment(days_after=5, amount=10_000.0)])
    assert outcome == OUTCOME_REPAID and amount == 10_000.0


def test_the_full_threshold_comes_from_settings():
    """Not a hardcoded 0.9 — the ratio is a business decision and must be
    tunable without editing the labeller."""
    ratio = settings.REPAYMENT_FULL_RATIO
    just_enough = 10_000.0 * ratio
    assert infer([case(target=10_000.0)],
                 [payment(days_after=3, amount=just_enough)])[0] == OUTCOME_REPAID
    assert infer([case(target=10_000.0)],
                 [payment(days_after=3, amount=just_enough - 1)])[0] == OUTCOME_PARTIAL


def test_part_payment_is_partial_not_a_failure():
    """Someone who paid a third of the target behaved differently from someone
    who paid nothing, and flattening the two throws away the distinction the
    scorecard is trying to learn."""
    outcome, amount = infer([case(target=10_000.0)],
                            [payment(days_after=10, amount=3_000.0)])
    assert outcome == OUTCOME_PARTIAL and amount == 3_000.0


def test_payments_across_several_cases_are_summed():
    cases = [case(cid=test_id("c1"), target=5_000.0), case(cid="c2", target=5_000.0)]
    outcome, amount = infer(cases, [payment(days_after=2, amount=5_000.0, cid=test_id("c1")),
                                    payment(days_after=4, amount=5_000.0, cid="c2")])
    assert outcome == OUTCOME_REPAID and amount == 10_000.0


# ── The window ───────────────────────────────────────────────────────────────
def test_payments_before_the_score_do_not_count():
    """The question is what the borrower did NEXT. A payment made before the
    score was computed is already baked into the features that produced it —
    counting it again as the outcome is the label leaking into itself."""
    assert infer([case()], [payment(days_after=-5, amount=10_000.0)])[0] == OUTCOME_NO_PAYMENT


def test_payment_on_the_score_date_itself_does_not_count():
    """The window is exclusive at the start: as_of belongs to the features."""
    assert infer([case()], [payment(days_after=0, amount=10_000.0)])[0] == OUTCOME_NO_PAYMENT


def test_payment_after_the_horizon_does_not_count():
    assert infer([case()],
                 [payment(days_after=HORIZON + 1, amount=10_000.0)])[0] == OUTCOME_NO_PAYMENT


def test_payment_on_the_last_day_of_the_horizon_counts():
    """Inclusive at the end. An off-by-one here silently mislabels every
    borrower who paid on the final day as a non-payer."""
    assert infer([case()],
                 [payment(days_after=HORIZON, amount=10_000.0)])[0] == OUTCOME_REPAID


# ── Censoring — the part that matters most ───────────────────────────────────
def test_closed_case_with_no_payment_is_censored_not_a_failure():
    """A case closed administratively is the bank's decision, not the
    borrower's refusal. Labelling it NO_PAYMENT teaches a model that the bank's
    own choice is the borrower's fault."""
    for status in _CENSORING_STATUSES:
        assert infer([case(status=status)], [])[0] == OUTCOME_CENSORED


def test_censored_outcomes_are_excluded_from_the_trainable_set():
    assert OUTCOME_CENSORED in CENSORED_OUTCOMES
    assert OUTCOME_CENSORED not in POSITIVE_OUTCOMES
    assert OUTCOME_NO_PAYMENT not in CENSORED_OUTCOMES     # a real negative


def test_an_open_case_with_no_payment_is_a_genuine_negative():
    """The borrower had the horizon and did nothing. That IS the signal."""
    assert infer([case(status=CaseStatus.IN_PROGRESS)], [])[0] == OUTCOME_NO_PAYMENT


def test_one_open_case_is_enough_to_stop_censoring():
    """Censoring requires that EVERY case was taken off the table. If one is
    still live, the borrower could have paid and chose not to."""
    cases = [case(cid=test_id("c1"), status=CaseStatus.CLOSED),
             case(cid="c2", status=CaseStatus.IN_PROGRESS)]
    assert infer(cases, [])[0] == OUTCOME_NO_PAYMENT


def test_payment_beats_censoring():
    """Money actually arrived. Whatever the case status says, that is not a
    censored observation."""
    assert infer([case(status=CaseStatus.CLOSED, target=10_000.0)],
                 [payment(days_after=3, amount=10_000.0)])[0] == OUTCOME_REPAID


# ── Degenerate inputs ────────────────────────────────────────────────────────
def test_no_cases_at_all_is_no_payment_not_a_crash():
    assert infer([], [])[0] == OUTCOME_NO_PAYMENT


def test_zero_target_with_money_received_is_partial_not_repaid():
    """Dividing by a zero target would either crash or declare every rupee a
    full recovery. Neither is acceptable, so it degrades to PARTIAL."""
    assert infer([case(target=0.0)],
                 [payment(days_after=2, amount=500.0)])[0] == OUTCOME_PARTIAL


# ── Recovery labelling: the multi-horizon revisit (2026-08-24) ───────────────
# The repayment labeller above visits a row ONCE and is finished. This one has to
# come back: a row scored today cannot know its 90-day recovery until 90 days
# have passed, but its 30-day figure is knowable long before that.
#
# Two harms have tests of their own below, and neither is visible in a count of
# labelled rows:
#   * retro-labelling a row written BEFORE recovery scoring existed attaches an
#     outcome to a prediction nobody made;
#   * treating a censored case as a recovery failure teaches a model to blame the
#     borrower for the bank's own recall.
RECOVERY_HORIZONS = settings.RECOVERY_OUTCOME_HORIZONS


def rec_snap(**over):
    """A snapshot row that DID carry a recovery prediction."""
    base = dict(loan_id=test_id("l1"), case_id=test_id("c1"), as_of_date=AS_OF, outcome=None,
                recovery_rate_90=0.42, recovery_labelled_through_days=0)
    base.update(over)
    return NS(**base)


def test_nothing_is_due_before_the_first_horizon_matures():
    """Labelling a three-day-old score "recovered nothing" records a failure about
    a borrower who has not had time to succeed."""
    due = SVC._recovery_horizons_due(rec_snap(), AS_OF + timedelta(days=29),
                                     RECOVERY_HORIZONS)
    assert due == []


def test_the_first_horizon_comes_due_on_the_day():
    due = SVC._recovery_horizons_due(rec_snap(), AS_OF + timedelta(days=30),
                                     RECOVERY_HORIZONS)
    assert due == [30]


def test_a_row_already_done_through_30_is_picked_up_again_at_60():
    """The revisit. Without it the 60- and 90-day figures never get written and
    the horizon split the feature was asked for does not exist."""
    row = rec_snap(recovery_labelled_through_days=30)
    assert SVC._recovery_horizons_due(row, AS_OF + timedelta(days=59),
                                      RECOVERY_HORIZONS) == []
    assert SVC._recovery_horizons_due(row, AS_OF + timedelta(days=60),
                                      RECOVERY_HORIZONS) == [60]


def test_a_row_done_through_60_is_picked_up_again_at_90():
    row = rec_snap(recovery_labelled_through_days=60)
    assert SVC._recovery_horizons_due(row, AS_OF + timedelta(days=90),
                                      RECOVERY_HORIZONS) == [90]


def test_a_finished_row_is_never_picked_up_again():
    """Once through 90 the row is done for good, however old it gets — otherwise
    it never leaves the scan index and the labeller's cost grows without bound."""
    row = rec_snap(recovery_labelled_through_days=90)
    assert SVC._recovery_horizons_due(row, AS_OF + timedelta(days=400),
                                      RECOVERY_HORIZONS) == []


def test_a_backlog_crosses_several_horizons_in_one_pass():
    """A worker down for a fortnight, or a row scored during a backlog, can cross
    two boundaries between runs. Labelling one horizon per night would take three
    more nights to catch up."""
    due = SVC._recovery_horizons_due(rec_snap(), AS_OF + timedelta(days=95),
                                     RECOVERY_HORIZONS)
    assert due == [30, 60, 90]


# ── The measurement window ──────────────────────────────────────────────────
def test_recovery_counts_only_money_arriving_after_the_score():
    """The question is what came back once the prediction was made, not what had
    already been collected. Counting the latter scores the scorecard on history it
    was handed rather than on anything it foresaw."""
    payments = {test_id("c1"): [payment(days_after=-3, amount=50_000.0),   # before the score
                       payment(days_after=10, amount=7_000.0)]}
    got = SVC._received_within(AS_OF, 30, [case()], payments)
    assert got == 7_000.0


def test_the_window_is_half_open_at_the_scoring_date():
    """A payment on the scoring date itself was already known when the score was
    computed, so it belongs to the features, not to the outcome. Matches
    _infer_outcome's window exactly — the two must not disagree about a day."""
    payments = {test_id("c1"): [payment(days_after=0, amount=5_000.0)]}
    assert SVC._received_within(AS_OF, 30, [case()], payments) == 0.0


def test_the_window_is_closed_at_the_horizon():
    payments = {test_id("c1"): [payment(days_after=30, amount=5_000.0)]}
    assert SVC._received_within(AS_OF, 30, [case()], payments) == 5_000.0
    assert SVC._received_within(AS_OF, 29, [case()], payments) == 0.0


def test_longer_horizons_are_supersets_of_shorter_ones():
    """Cumulative, not per-period. rate_90 is the share recovered BY 90 days, so
    the label it is scored against must be the same shape — a 60-day figure that
    excluded the first 30 days would be measuring something else entirely."""
    payments = {test_id("c1"): [payment(days_after=d, amount=1_000.0)
                       for d in (10, 40, 70)]}
    at_30 = SVC._received_within(AS_OF, 30, [case()], payments)
    at_60 = SVC._received_within(AS_OF, 60, [case()], payments)
    at_90 = SVC._received_within(AS_OF, 90, [case()], payments)
    assert at_30 == 1_000.0 and at_60 == 2_000.0 and at_90 == 3_000.0
    assert at_30 <= at_60 <= at_90


def test_no_payments_is_zero_not_none():
    """Zero is a real observation once the horizon has matured: money could have
    arrived and did not. NULL means the horizon has not come due, and the two must
    stay distinguishable in the training pull."""
    assert SVC._received_within(AS_OF, 30, [case()], {}) == 0.0


# ── Against a real database ─────────────────────────────────────────────────
@pytest.fixture()
def db():
    """SQLite, built through create_all — the same pattern as
    test_otp_service.py. This is what proves the recovery columns survive the
    JSON .with_variant fallback and that the scan predicate does what the pure
    tests above say it does."""
    engine = make_engine()
    create_schema(engine)
    session = make_session_factory(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _case_row(db, loan_id=test_id("l1"), case_id=test_id("case-1")):
    """A minimal Case, so a snapshot's loan is OBSERVABLE.

    Added 2026-08-24. Without it a loan has no case, payments are unreachable
    (Payment.case_id is NOT NULL) and the labeller now correctly treats the row
    as unobservable rather than recovering zero — which is right, but it is not
    what the horizon-advancement tests are trying to measure."""
    case_row = Case(
        id=case_id, case_number=f"C-{case_id}", customer_id=test_id("cu1"), loan_id=loan_id,
        status=CaseStatus.IN_PROGRESS, priority=CasePriority.MEDIUM,
        target_amount=10_000.0, collected_amount=0.0,
    )
    db.add(case_row)
    db.flush()
    return case_row


def _row(db, *, days_ago, rate_90=0.42, through=0, loan_id=test_id("l1")):
    row = RepaymentSnapshot(
        loan_id=loan_id, customer_id=test_id("cu1"), case_id=None,
        as_of_date=date.today() - timedelta(days=days_ago),
        trigger="MANUAL", source="SCORECARD", model_version="scorecard-1.1.0",
        likelihood=60.0, risk_score=40.0, band="UNCERTAIN",
        risk_category=RiskCategory.MEDIUM, evidence_coverage=0.7,
        features={}, contributions={},
        recovery_rate_90=rate_90, recovery_labelled_through_days=through,
    )
    db.add(row)
    db.flush()
    return row


def test_a_row_written_before_recovery_shipped_is_never_touched(db):
    """THE GUARD THAT MATTERS. Every snapshot written before this feature has NULL
    recovery fields. Retro-labelling them would attach a recovery outcome to a
    prediction that was never made, and those rows would then look like evidence
    the scorecard worked."""
    old = _row(db, days_ago=200, rate_90=None)
    result = RepaymentService(db).attach_recovery_outcomes()
    assert result["labelled"] == 0
    assert old.recovered_amount_30 is None
    assert old.recovery_labelled_through_days == 0


def test_the_scan_advances_a_row_through_each_horizon_in_turn(db):
    """Three passes on three different days: 30, then 60, then 90. The row leaves
    the scan index only when it is finished."""
    svc = RepaymentService(db)
    _case_row(db)
    row = _row(db, days_ago=95)

    svc.attach_recovery_outcomes(as_of=row.as_of_date + timedelta(days=30))
    assert row.recovery_labelled_through_days == 30
    assert row.recovered_amount_30 == 0.0 and row.recovered_amount_60 is None

    svc.attach_recovery_outcomes(as_of=row.as_of_date + timedelta(days=60))
    assert row.recovery_labelled_through_days == 60
    assert row.recovered_amount_60 == 0.0 and row.recovered_amount_90 is None

    svc.attach_recovery_outcomes(as_of=row.as_of_date + timedelta(days=90))
    assert row.recovery_labelled_through_days == 90
    assert row.recovered_amount_90 == 0.0

    # Finished: a fourth pass finds nothing to do.
    assert svc.attach_recovery_outcomes(
        as_of=row.as_of_date + timedelta(days=120))["labelled"] == 0


def test_a_matured_row_fills_every_horizon_at_once(db):
    _case_row(db)
    row = _row(db, days_ago=120)
    result = RepaymentService(db).attach_recovery_outcomes()
    assert result["labelled"] == 1
    assert row.recovery_labelled_through_days == 90
    assert row.recovered_amount_30 == 0.0
    assert row.recovered_amount_60 == 0.0
    assert row.recovered_amount_90 == 0.0


def test_the_recovery_pass_never_touches_the_repayment_outcome(db):
    """Censoring lives on `outcome`, and this pass must not write it. A row must
    not become uncensored because money happened to arrive after the bank pulled
    the case back."""
    _case_row(db)
    row = _row(db, days_ago=120)
    row.outcome = OUTCOME_CENSORED
    db.flush()

    RepaymentService(db).attach_recovery_outcomes()
    assert row.outcome == OUTCOME_CENSORED
    assert row.recovery_labelled_through_days == 90


def test_a_young_row_is_left_alone_by_the_scan(db):
    row = _row(db, days_ago=10)
    assert RepaymentService(db).attach_recovery_outcomes()["labelled"] == 0
    assert row.recovery_labelled_through_days == 0


# ── Phase 0: what counts as realised recovery (2026-08-24) ──────────────────
# Two defects found while designing the calibration phase, both latent rather
# than biting, and both cheaper to fix before the first labels are written on
# 2026-09-23 than to unpick from stored training data afterwards:
#
#   * _received_within summed payments filtered on DATE ONLY, so a REJECTED or
#     REVERSED receipt counted as money recovered. The ledger had neither status
#     at the time, so nothing was wrong yet — the first real reversal would have
#     become a training label saying the borrower paid.
#   * a loan with no case returned 0.0, because Payment.case_id is NOT NULL and
#     money is reachable only through a case. "We cannot see" was being recorded
#     as "nothing arrived".
#
# Approved rule: VERIFIED only.

from app.models.payment import PaymentStatus
from app.services.repayment_service import _RECOVERED_PAYMENT_STATUSES


def paid(*, days_after, amount, status=PaymentStatus.VERIFIED, cid=test_id("c1")):
    return NS(case_id=cid, amount=amount, status=status,
              payment_date=datetime.combine(AS_OF + timedelta(days=days_after),
                                            datetime.min.time(), tzinfo=timezone.utc))


def received(payments, cases=None, horizon=30):
    by_case = {}
    for p in payments:
        by_case.setdefault(p.case_id, []).append(p)
    return SVC._received_within(AS_OF, horizon,
                                [case()] if cases is None else cases, by_case)


# ── Which statuses count ────────────────────────────────────────────────────
def test_verified_payments_count():
    """The approved rule: money the bank confirmed landed."""
    assert received([paid(days_after=5, amount=10_000.0,
                          status=PaymentStatus.VERIFIED)]) == 10_000.0


def test_rejected_payments_do_not_count():
    """A rejected receipt is money that never arrived. Counting it would write a
    training label saying the borrower paid when they did not — the single most
    damaging thing that can go into a label."""
    assert received([paid(days_after=5, amount=10_000.0,
                          status=PaymentStatus.REJECTED)]) == 0.0


def test_reversed_payments_do_not_count():
    """A reversal is money that arrived and went back. Same reasoning."""
    assert received([paid(days_after=5, amount=10_000.0,
                          status=PaymentStatus.REVERSED)]) == 0.0


def test_pending_verification_does_not_count():
    """THE APPROVED BUSINESS RULE, decided 2026-08-24: VERIFIED only.

    PENDING is the state a payment passes through on its way to being REJECTED,
    so counting it would count some of the failures as successes. The accepted
    cost is a conservative bias where verification lags — a payment made on day
    29 and verified on day 33 is missing from the 30-day figure. That direction
    is right for a training label: it understates recovery rather than inventing
    it, and it shrinks at the longer horizons."""
    assert received([paid(days_after=5, amount=10_000.0,
                          status=PaymentStatus.PENDING_VERIFICATION)]) == 0.0


def test_only_verified_is_in_the_allowed_set():
    """Pinned against the constant, so widening the rule cannot happen quietly."""
    assert _RECOVERED_PAYMENT_STATUSES == {"VERIFIED"}


def test_a_mixed_ledger_counts_only_the_verified_money():
    """The realistic case: one window, four receipts, one real."""
    total = received([
        paid(days_after=3, amount=10_000.0, status=PaymentStatus.VERIFIED),
        paid(days_after=6, amount=25_000.0, status=PaymentStatus.PENDING_VERIFICATION),
        paid(days_after=9, amount=40_000.0, status=PaymentStatus.REJECTED),
        paid(days_after=12, amount=15_000.0, status=PaymentStatus.REVERSED),
    ])
    assert total == 10_000.0


def test_status_may_be_the_enum_or_its_plain_value():
    """ORM rows carry the enum, stand-ins carry the string. Both must behave the
    same, or a test proves something the production path does not do."""
    assert received([paid(days_after=5, amount=7_000.0, status="VERIFIED")]) == 7_000.0
    assert received([paid(days_after=5, amount=7_000.0, status="REJECTED")]) == 0.0


def test_the_window_still_bounds_verified_money():
    """The status filter must not have replaced the date filter."""
    assert received([paid(days_after=45, amount=10_000.0)], horizon=30) == 0.0
    assert received([paid(days_after=45, amount=10_000.0)], horizon=60) == 10_000.0


# ── Unobservable is not zero ────────────────────────────────────────────────
def test_a_loan_with_no_case_yields_no_observation():
    """Payment.case_id is NOT NULL, so a caseless loan has no reachable payments.
    _received_within sees no cases and can only return 0.0 — which is exactly why
    the LABELLER must not call it for such a loan. The guard lives in
    attach_recovery_outcomes; this test records why it has to."""
    assert received([paid(days_after=5, amount=10_000.0)], cases=[]) == 0.0


def test_caseless_rows_are_reported_unobservable_not_labelled_zero(db):
    """The fix. A matured row on a loan with no case must finish with its amounts
    still NULL and be counted as unobservable — never 0.0, which would enter the
    training set as "the borrower paid nothing"."""
    row = _row(db, days_ago=120)          # no cases exist for this loan_id
    result = RepaymentService(db).attach_recovery_outcomes()

    assert result["labelled"] == 0
    assert result["unobservable_closed"] == 1
    assert row.recovered_amount_30 is None
    assert row.recovered_amount_60 is None
    assert row.recovered_amount_90 is None


def test_an_immature_caseless_row_is_retried_not_closed(db):
    """A case can be allocated later, which makes the remaining horizons
    observable. So a caseless row is left in the scan while any horizon is still
    open, and only given up on once all three have matured."""
    row = _row(db, days_ago=35)           # 30 matured, 60 and 90 still open
    result = RepaymentService(db).attach_recovery_outcomes()

    assert result["unobservable_pending"] == 1
    assert result["unobservable_closed"] == 0
    assert row.recovery_labelled_through_days == 0   # still in the scan


def test_a_fully_matured_caseless_row_leaves_the_scan(db):
    """...but it must not be re-examined nightly forever once there is no chance
    left. The marker advances so the row drops out of the partial index, with the
    amounts still NULL to say "matured, never observable"."""
    row = _row(db, days_ago=200)
    svc = RepaymentService(db)
    svc.attach_recovery_outcomes()
    assert row.recovery_labelled_through_days == 90
    assert row.recovered_amount_90 is None

    # A second pass finds nothing left to do.
    assert svc.attach_recovery_outcomes()["examined"] == 0


def test_null_and_zero_are_distinguishable_after_labelling(db):
    """The three states the model comment promises. Without this distinction a
    validation query cannot tell "we could not see" from "nothing came back", and
    the scorecard's separation is flattered by every caseless LOW loan."""
    unobservable = _row(db, days_ago=200, loan_id=test_id("no-case-loan"))
    RepaymentService(db).attach_recovery_outcomes()

    # matured + NULL  ->  unobservable
    assert unobservable.recovery_labelled_through_days == 90
    assert unobservable.recovered_amount_90 is None
