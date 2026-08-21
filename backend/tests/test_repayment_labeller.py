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
from app.models.case import CaseStatus
from app.models.repayment_snapshot import (
    CENSORED_OUTCOMES, OUTCOME_CENSORED, OUTCOME_NO_PAYMENT, OUTCOME_PARTIAL,
    OUTCOME_REPAID, POSITIVE_OUTCOMES,
)
from app.services.repayment_service import RepaymentService, _CENSORING_STATUSES

SVC = RepaymentService(db=None)
AS_OF = date(2026, 7, 1)
HORIZON = settings.REPAYMENT_OUTCOME_HORIZON_DAYS


def snap(**over):
    base = dict(loan_id="l1", case_id="c1", as_of_date=AS_OF, outcome=None)
    base.update(over)
    return NS(**base)


def case(status=CaseStatus.IN_PROGRESS, target=10_000.0, cid="c1"):
    return NS(id=cid, status=status, target_amount=target)


def payment(*, days_after, amount, cid="c1"):
    return NS(case_id=cid, amount=amount,
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
    cases = [case(cid="c1", target=5_000.0), case(cid="c2", target=5_000.0)]
    outcome, amount = infer(cases, [payment(days_after=2, amount=5_000.0, cid="c1"),
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
    cases = [case(cid="c1", status=CaseStatus.CLOSED),
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
