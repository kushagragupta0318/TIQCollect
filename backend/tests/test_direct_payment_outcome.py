"""A borrower who paid the bank directly must not read as a failure.

THE DEFECT, traced 2026-09-09. `ingest_daily` handled `bank_action=PAID_DIRECT`
by closing the case as PAID with a resolution note and creating no `Payment` row
— verified, zero `Payment(` constructions in that script. But
`ml/pipeline/outcomes.py` derives the model's label exclusively from VERIFIED
`Payment` rows, and `censoring_status` does not censor `CaseStatus.PAID`
(correctly: PAID is the success outcome, not a withdrawal from the collectable
population). So the row was labelled with `paid = 0`: NOT_RECOVERED, y = 1.

That is not a coverage gap, it is a biased target. The model would have been
monitored on "did an agent collect it" rather than "did the borrower pay".

THE FIX DOES NOT TOUCH THE OUTCOME DEFINITION. `outcomes.py` is unchanged; the
rule is still `paid_in_window >= 0.8 * min(overdue_amount, emi_amount)` over
VERIFIED payments in the window. The direct payment is simply recorded in the
ledger the labeller already reads, credited to nobody — `agent_id IS NULL`,
`mode = BANK_DIRECT` — because attributing it to an agent would inflate that
agent's collections, leaderboard and `affinity_score`, which feeds
`eb_multiplier` and therefore the allocator.

Latent rather than manifest when found: 0 of 323 PAID cases on the live book
lacked a verified payment, because no real bank feed had run against it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.case import CaseStatus
from app.models.loan import LoanStatus
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.ml.pipeline.outcomes import OutcomeStatus, evaluate
from scripts.ingest_daily import _record_direct_payment, process_row

from tests.test_model_outcomes import (  # noqa: F401
    AS_OF, HORIZON, TODAY, _at, _pay, _prediction, db, setup_db, world,
)

# world's loan: overdue 24,000, emi 8,000 -> the bar is 0.8 * min(...) = 6,400.
BAR = 6_400.0


def _direct(db, world, *, amount, paid_on=None, case=None):
    """Exactly what ingest_daily does when the bank reports PAID_DIRECT."""
    out = _record_direct_payment(db, case or world["case"], amount=amount,
                                 paid_on=paid_on or "")
    db.commit()
    return out


def _ingest(db, world, **over):
    """One CSV row through the real `process_row`, for the existing fixture."""
    row = {
        "customer_ref": world["customer"].customer_ref,
        "loan_account_number": world["loan"].loan_account_number,
        "case_number": world["case"].case_number,
        "bank_action": "ACTIVE", "dpd": "0",
        "total_outstanding": "170000", "overdue_amount": "0",
        "cibil_score": "700",
    }
    row.update(over)
    res = process_row(row, db, dry_run=False, today=TODAY)
    db.commit()
    return res


def _label(db, pred):
    return evaluate(db, pred, as_of=TODAY, horizon_days=HORIZON)


# ---------------------------------------------------------------------------
# 1. The defect itself
# ---------------------------------------------------------------------------

def test_a_direct_payer_used_to_read_as_a_failure_and_now_does_not(db, world):
    pred = _prediction(db, world)

    # Before: the bank closes the case, no payment row exists.
    world["case"].status = CaseStatus.PAID
    world["case"].resolution_notes = "Closed: customer paid bank directly"
    db.commit()
    was = _label(db, pred)
    assert was.status is OutcomeStatus.NOT_RECOVERED and was.actual_outcome == 1, (
        "the defect this test exists for is not reproducible; if the labeller "
        "now censors PAID, this file needs rewriting rather than passing")

    # After: the same closure also records the money.
    assert _direct(db, world, amount=24_000.0,
                   paid_on=str(AS_OF + timedelta(days=5))) == "recorded"
    now = _label(db, pred)
    assert now.status is OutcomeStatus.RECOVERED
    assert now.actual_outcome == 0
    assert now.amount_paid == 24_000.0
    assert now.n_payments == 1


def test_the_recorded_payment_is_credited_to_nobody(db, world):
    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF + timedelta(days=5)))
    p = db.query(Payment).one()
    assert p.agent_id is None, "a bank payment was credited to an agent"
    assert p.visit_id is None
    assert p.mode is PaymentMode.BANK_DIRECT
    assert p.status is PaymentStatus.VERIFIED
    assert p.receipt_number and p.bank_reference.startswith("BANK-DIRECT-")


# ---------------------------------------------------------------------------
# 2. PAID_DIRECT that is NOT a recovery still labels correctly
# ---------------------------------------------------------------------------

def test_a_token_direct_payment_below_the_bar_is_not_recovered(db, world):
    """The threshold is preserved, not bypassed. Being a bank payment does not
    make it a material one."""
    pred = _prediction(db, world)
    _direct(db, world, amount=BAR - 1, paid_on=str(AS_OF + timedelta(days=5)))
    r = _label(db, pred)
    assert r.status is OutcomeStatus.NOT_RECOVERED
    assert r.actual_outcome == 1
    assert r.amount_paid == BAR - 1
    assert r.threshold == BAR


def test_a_direct_payment_outside_the_window_does_not_count(db, world):
    """`last_payment_date` is used precisely so an old payment the bank is only
    now reporting cannot manufacture a recovery in today's window."""
    pred = _prediction(db, world)
    _direct(db, world, amount=24_000.0,
            paid_on=str(AS_OF - timedelta(days=3)))     # before the prediction
    r = _label(db, pred)
    assert r.status is OutcomeStatus.NOT_RECOVERED
    assert r.amount_paid == 0.0

    late = _prediction(db, world)
    _direct(db, world, amount=24_000.0,
            paid_on=str(AS_OF + timedelta(days=HORIZON + 1)))   # day 31
    r2 = _label(db, late)
    assert r2.status is OutcomeStatus.NOT_RECOVERED
    assert r2.paid_after_window == 24_000.0, "day 31 must still be recorded"


def test_a_payment_on_the_observation_day_is_excluded_as_before(db, world):
    """Half-open at the start. Unchanged by this fix, and easy to break."""
    pred = _prediction(db, world)
    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF))
    assert _label(db, pred).amount_paid == 0.0


def test_no_amount_means_no_row_rather_than_an_invented_one(db, world):
    """A PAID_DIRECT with no `payment_amount` and no prior arrears. Inventing a
    figure to make the label look right is worse than leaving it unlabelled."""
    assert _direct(db, world, amount=0.0) == "no_amount"
    assert db.query(Payment).count() == 0
    pred = _prediction(db, world)
    assert _label(db, pred).status is OutcomeStatus.NOT_RECOVERED


# ---------------------------------------------------------------------------
# 3. The agent-collected path is untouched
# ---------------------------------------------------------------------------

def test_the_normal_verified_payment_path_is_unchanged(db, world):
    pred = _prediction(db, world)
    _pay(db, world, _at(5), BAR)
    r = _label(db, pred)
    assert r.status is OutcomeStatus.RECOVERED
    assert r.actual_outcome == 0
    assert r.amount_paid == BAR
    assert r.n_payments == 1
    p = db.query(Payment).one()
    assert p.agent_id == world["agent"].id and p.mode is PaymentMode.CASH


def test_a_pending_or_reversed_direct_payment_would_still_not_count(db, world):
    """The VERIFIED filter is the labeller's, not this fix's, and still binds."""
    pred = _prediction(db, world)
    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF + timedelta(days=5)))
    db.query(Payment).one().status = PaymentStatus.REVERSED
    db.commit()
    assert _label(db, pred).amount_paid == 0.0


# ---------------------------------------------------------------------------
# 4. No double counting
# ---------------------------------------------------------------------------

def test_an_agent_collection_and_a_direct_payment_sum_they_do_not_double(db, world):
    """The realistic shape: the agent collected part, the borrower cleared the
    rest with the bank. The bank's arrears figure is already net of what the
    agent brought in, so the two rows sum to what was actually paid."""
    pred = _prediction(db, world)
    _pay(db, world, _at(4), 4_000.0)                       # agent, below the bar
    _direct(db, world, amount=2_400.0,                     # remaining arrears
            paid_on=str(AS_OF + timedelta(days=6)))

    r = _label(db, pred)
    assert r.n_payments == 2
    assert r.amount_paid == 6_400.0, "the two rows did not sum to what was paid"
    assert r.amount_paid == BAR
    assert r.status is OutcomeStatus.RECOVERED


def test_re_ingesting_the_same_file_cannot_write_the_payment_twice(db, world):
    """`_close_case_paid` returns early on an already-resolved case and the
    caller only records on `auto_closed_paid`, so a re-run is a no-op."""
    from scripts.ingest_daily import _close_case_paid

    case = world["case"]
    assert _close_case_paid(case) == "auto_closed_paid"
    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF + timedelta(days=5)))

    second = _close_case_paid(case)             # the file arrives again
    assert second == "already_paid"
    assert second != "auto_closed_paid", "a re-run would have written a second row"
    assert db.query(Payment).count() == 1

    pred = _prediction(db, world)
    r = _label(db, pred)
    assert r.amount_paid == 24_000.0 and r.n_payments == 1


# ---------------------------------------------------------------------------
# 5. Censoring is unchanged
# ---------------------------------------------------------------------------

def test_a_written_off_case_is_still_censored_even_with_a_direct_payment(db, world):
    pred = _prediction(db, world)
    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF + timedelta(days=5)))
    world["case"].status = CaseStatus.WRITTEN_OFF
    db.commit()
    r = _label(db, pred)
    assert r.status is OutcomeStatus.CENSORED_WRITTEN_OFF
    assert r.actual_outcome is None


def test_settlement_is_still_censored_and_writes_no_payment_row(db, world):
    """SETTLED is a bank-approved reduction, not the borrower repaying. It was
    already CENSORED_SETTLED and this fix deliberately leaves it alone.

    Executed through `process_row`, not asserted from source text — a slice of
    the file cannot tell which branch a call sits in, which is exactly how the
    `PlanInProgressError` scope bug survived its own structural assertion.
    """
    pred = _prediction(db, world)
    _ingest(db, world, bank_action="SETTLED", settlement_amount="9000")

    assert db.query(Payment).count() == 0, "a settlement wrote a payment row"
    assert _label(db, pred).status is OutcomeStatus.CENSORED_SETTLED


def test_a_deceased_or_recalled_case_censors_as_before(db, world):
    pred = _prediction(db, world)
    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF + timedelta(days=5)))
    world["case"].resolution_notes = "RECALLED by bank. Reason: LEGAL"
    db.commit()
    assert _label(db, pred).status is OutcomeStatus.CENSORED_RECALLED


# ---------------------------------------------------------------------------
# 6. The allocator must not move
# ---------------------------------------------------------------------------

def test_a_direct_payment_is_not_evidence_about_any_agent(db, world):
    """`empirical_bayes` groups by agent_id with no agent filter, and its
    `segment_totals` is the prior EVERY agent's `eb_multiplier` is divided by.
    A NULL-agent row leaking in would move `prob_recovery` for every case in
    the segment — which is why the guard is in the query, not in a caller."""
    from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster

    def fit():
        return EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=TODAY)

    # A real collection first, so the priors are not trivially empty on both
    # sides — a comparison of two empty dicts would prove nothing.
    _pay(db, world, _at(4), 12_000.0)
    before = fit()
    assert before.agent_observations, "nothing to compare; the fixture is inert"

    _direct(db, world, amount=24_000.0, paid_on=str(AS_OF + timedelta(days=5)))
    after = fit()

    assert after.segment_priors == before.segment_priors, (
        "a payment nobody collected moved the segment prior")
    assert after.agent_observations == before.agent_observations
    assert after.global_prior == before.global_prior
    assert None not in {k[0] for k in after.agent_observations}


def test_an_agent_collection_still_does_reach_empirical_bayes(db, world):
    """The guard must exclude NULL agents and nothing else — otherwise it would
    silently switch agent competency off."""
    from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster

    adj = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=TODAY)
    assert adj.agent_observations == {}

    _pay(db, world, _at(4), 12_000.0)
    adj2 = EmpiricalBayesAgentAdjuster().fit_from_db(db, as_of=TODAY)
    assert adj2.agent_observations, "the guard excluded real agent collections too"
    assert any(k[0] == world["agent"].id for k in adj2.agent_observations)


# ---------------------------------------------------------------------------
# 7. The ingest wiring, not just the helper
# ---------------------------------------------------------------------------

def test_process_row_records_the_arrears_it_clears(db, world):
    """The real wiring: a PAID_DIRECT row through `process_row` must close the
    case AND leave the money in the ledger, with the arrears the loan carried
    BEFORE this file zeroed them."""
    pred = _prediction(db, world)
    assert world["loan"].overdue_amount == 24_000.0

    res = _ingest(db, world, bank_action="PAID_DIRECT",
                  last_payment_date=str(AS_OF + timedelta(days=6)))
    assert res["action_case"] == "auto_closed_paid"
    assert res["direct_payment"] == "recorded"

    assert world["case"].status is CaseStatus.PAID
    assert world["loan"].overdue_amount == 0.0        # the file zeroed it
    p = db.query(Payment).one()
    assert p.amount == 24_000.0, "the arrears were read after being zeroed"
    assert p.agent_id is None and p.mode is PaymentMode.BANK_DIRECT
    assert _label(db, pred).status is OutcomeStatus.RECOVERED


def test_the_banks_own_figure_wins_over_the_inferred_arrears(db, world):
    """If the feed reports what was paid, that beats our inference from DPD."""
    _ingest(db, world, bank_action="PAID_DIRECT", payment_amount="15000",
            last_payment_date=str(AS_OF + timedelta(days=6)))
    assert db.query(Payment).one().amount == 15_000.0


def test_process_row_is_idempotent_on_a_second_ingest(db, world):
    """The same file arriving twice must not double the borrower's payment."""
    pred = _prediction(db, world)
    first = _ingest(db, world, bank_action="PAID_DIRECT",
                    last_payment_date=str(AS_OF + timedelta(days=6)))
    second = _ingest(db, world, bank_action="PAID_DIRECT",
                     last_payment_date=str(AS_OF + timedelta(days=6)))

    assert first["direct_payment"] == "recorded"
    assert second["action_case"] == "already_paid"
    assert "direct_payment" not in second
    assert db.query(Payment).count() == 1
    r = _label(db, pred)
    assert r.n_payments == 1 and r.amount_paid == 24_000.0


def test_an_ordinary_active_row_writes_no_payment(db, world):
    """Only a bank-confirmed direct payment does. An ACTIVE update must not."""
    _ingest(db, world, bank_action="ACTIVE", dpd="75", overdue_amount="30000")
    assert db.query(Payment).count() == 0
    assert world["case"].status is CaseStatus.ASSIGNED
