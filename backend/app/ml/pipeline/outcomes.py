# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. THE ONE PLACE the trained model's outcome is defined.
#
#   WHY IT IS SEPARATE FROM RepaymentService.attach_outcomes. That labeller
#   answers a different question with a different rule: REPAYMENT_FULL_RATIO
#   0.9 against what was still owed on as_of_date, graded REPAID / PARTIAL /
#   NO_PAYMENT. `recovery_risk` was trained and validated against
#
#       paid >= 0.8 * min(overdue_amount, emi_amount)
#
#   Labelling the model with the other rule would silently change the target it
#   was validated on — the metrics in ml/artifacts would then describe a
#   question nobody is asking any more. So the two are kept apart ON PURPOSE,
#   and this module is the only definition of the model's own label.
#
#   AND IT IS VERSIONED. OUTCOME_DEFINITION_VERSION is stamped on every row it
#   labels, for the same reason the scorecards carry *_VERSION: rows labelled
#   under two rules answer different questions, and a model trained across both
#   without filtering learns two targets at once. Change the rule, bump the
#   version, and the old rows stay readable as what they were.
#
#   THE DUPLICATION RISK IS REAL AND IS WHY THIS FILE EXISTS. The threshold and
#   the window appear here and NOWHERE ELSE — not in the task, not in the
#   monitor, not in the service. tests/test_model_outcomes.py asserts the
#   simulator's training rule and this scoring rule agree on the same inputs.
# ───────────────────────────────────────────────────────────────────────────
"""
The `recovery_risk` outcome: did this borrower make a material payment?

    from app.ml.pipeline.outcomes import attach_outcomes
    attach_outcomes(db, "recovery_risk")

THE RULE, in full
-----------------
    recovered  ==  paid_in_window >= 0.8 * min(overdue_amount, emi_amount)

    y = 0  recovered      (a material payment arrived)
    y = 1  not recovered  (it did not)

`overdue_amount` and `emi_amount` are the values FROZEN at prediction time, not
today's. Both are overwritten in place on `Loan`, so reading them now would
compare a payment window against a balance the payments themselves have already
changed — the same leak `RepaymentSnapshot` exists to prevent.

THE WINDOW
----------
    ( end of as_of_date , end of as_of_date + 30 days ]

Strictly after the observation day: a payment made on the day the features were
frozen happened BEFORE the prediction and is not evidence about what the
borrower did next. Inclusive of the 30th day. `REPAYMENT_OUTCOME_HORIZON_DAYS`
supplies the 30.

WHAT COUNTS AS A PAYMENT
------------------------
Only `PaymentStatus.VERIFIED`. PENDING_VERIFICATION is not yet money;
REJECTED never was; REVERSED was taken back. Reversal is represented by a
status change rather than a negative row, so no netting is required — and if
that ever changes, `test_reversed_payments_do_not_count` fails.

Deduplicated by `receipt_number`, which is UNIQUE in the schema. The dedupe is
therefore a guard rather than a fix, and it is cheap: if a double-recorded
payment ever reaches this table through a backfill, it will not inflate a label.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.case import Case, CaseStatus
from app.models.customer import CUSTOMER_TAG_DECEASED, Customer
from app.models.loan import Loan, LoanStatus
from app.models.model_prediction import ModelPrediction
from app.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)

#: Bump on ANY change to the threshold, the window, or the censoring rules.
OUTCOME_DEFINITION_VERSION = "recovery-outcome-1.0.0"

#: A payment is "material" at this share of one cycle's demand.
MATERIAL_PAYMENT_RATIO = 0.8

#: Baseline keys that must be present and positive for a label to be computable.
BASELINE_KEYS = ("overdue_amount", "emi_amount")

#: Prefix written by scripts/ingest_daily._close_case_recall. A recall is
#: detectable ONLY as free text — the schema records the bank action nowhere
#: else, which is a real gap worth closing with a `Case.closure_reason` enum.
#: Matched explicitly here rather than inferred, so the fragility is visible.
RECALL_NOTE_PREFIX = "RECALLED by bank"


class OutcomeStatus(str, Enum):
    """Every terminal state a prediction can reach. Nothing is implicit."""

    NOT_MATURED = "NOT_MATURED"                    # horizon has not elapsed
    RECOVERED = "RECOVERED"                        # y = 0
    NOT_RECOVERED = "NOT_RECOVERED"                # y = 1
    CENSORED_SETTLED = "CENSORED_SETTLED"
    CENSORED_WRITTEN_OFF = "CENSORED_WRITTEN_OFF"
    CENSORED_RECALLED = "CENSORED_RECALLED"
    CENSORED_DECEASED = "CENSORED_DECEASED"
    NO_BASELINE = "NO_BASELINE"                    # frozen inputs unusable
    NO_CASE = "NO_CASE"                            # the case has gone


#: Statuses that produce a 0/1 label. Everything else leaves actual_outcome NULL.
LABELLED = {OutcomeStatus.RECOVERED, OutcomeStatus.NOT_RECOVERED}

#: Censored: the borrower was removed from the collectable population by the
#: BANK, not by their own choice to pay or not. Counting these as "did not pay"
#: would teach the model that bank write-offs are borrower behaviour.
CENSORED = {
    OutcomeStatus.CENSORED_SETTLED, OutcomeStatus.CENSORED_WRITTEN_OFF,
    OutcomeStatus.CENSORED_RECALLED, OutcomeStatus.CENSORED_DECEASED,
}


@dataclass
class OutcomeResult:
    status: OutcomeStatus
    actual_outcome: int | None
    amount_paid: float = 0.0
    threshold: float | None = None
    n_payments: int = 0
    paid_after_window: float = 0.0
    definition_version: str = OUTCOME_DEFINITION_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def payment_window(as_of: date, horizon_days: int | None = None
                   ) -> tuple[datetime, datetime]:
    """( end of as_of , end of as_of + horizon ]  — both timezone-aware UTC.

    Half-open at the start so a payment made ON the observation day is excluded:
    it happened before the prediction and says nothing about what came next.
    """
    horizon = horizon_days or settings.REPAYMENT_OUTCOME_HORIZON_DAYS
    start = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    end = datetime.combine(as_of + timedelta(days=horizon), time.max,
                           tzinfo=timezone.utc)
    return start, end


def baseline_from(prediction: ModelPrediction) -> dict | None:
    """The frozen overdue/EMI, or None if they cannot be trusted.

    Prefers the dedicated `outcome_baseline` column. Falls back to the stored
    feature vector for rows written before that column existed — `overdue_amount`
    is a model feature, so it is there; `emi_amount` is not, so those rows
    resolve to NO_BASELINE rather than being labelled against a guess.
    """
    src = prediction.outcome_baseline or prediction.features or {}
    out = {}
    for k in BASELINE_KEYS:
        v = src.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Censoring
# ---------------------------------------------------------------------------

def censoring_status(case: Case | None, loan: Loan | None,
                     customer: Customer | None) -> OutcomeStatus | None:
    """Was this account removed from the collectable population by the bank?

    Checked in a fixed order, most specific first, so a written-off account that
    is also flagged deceased reports the write-off — the reason the money stopped
    being collectable, not a coincident flag.
    """
    if case is None:
        return OutcomeStatus.NO_CASE

    if case.status == CaseStatus.WRITTEN_OFF or (
            loan is not None and loan.status == LoanStatus.WRITTEN_OFF):
        return OutcomeStatus.CENSORED_WRITTEN_OFF

    if loan is not None and loan.status == LoanStatus.SETTLED:
        return OutcomeStatus.CENSORED_SETTLED
    notes = (case.resolution_notes or "")
    if notes.startswith("Bank settlement:"):
        return OutcomeStatus.CENSORED_SETTLED

    if notes.startswith(RECALL_NOTE_PREFIX):
        return OutcomeStatus.CENSORED_RECALLED

    if customer is not None and CUSTOMER_TAG_DECEASED in (customer.tags or []):
        return OutcomeStatus.CENSORED_DECEASED

    return None


# ---------------------------------------------------------------------------
# The evaluation
# ---------------------------------------------------------------------------

def evaluate(db: Session, prediction: ModelPrediction, *,
             as_of: date | None = None,
             horizon_days: int | None = None) -> OutcomeResult:
    """Resolve one prediction to an outcome, or explain why it cannot be."""
    horizon = horizon_days or settings.REPAYMENT_OUTCOME_HORIZON_DAYS
    today = as_of or datetime.now(timezone.utc).date()

    if prediction.as_of_date + timedelta(days=horizon) > today:
        return OutcomeResult(OutcomeStatus.NOT_MATURED, None)

    case = db.query(Case).filter(Case.id == prediction.case_id).first() \
        if prediction.case_id else None
    loan = db.query(Loan).filter(Loan.id == prediction.loan_id).first() \
        if prediction.loan_id else None
    customer = (db.query(Customer).filter(Customer.id == case.customer_id).first()
                if case is not None else None)

    censored = censoring_status(case, loan, customer)
    if censored is not None:
        return OutcomeResult(censored, None)

    baseline = baseline_from(prediction)
    if baseline is None:
        return OutcomeResult(OutcomeStatus.NO_BASELINE, None)

    start, end = payment_window(prediction.as_of_date, horizon)
    rows = (
        db.query(Payment)
        .filter(Payment.case_id == prediction.case_id,
                Payment.status == PaymentStatus.VERIFIED,
                Payment.payment_date > start,
                Payment.payment_date <= end)
        .all()
    )
    # Dedupe on the unique business key. A guard, not a fix — receipt_number is
    # UNIQUE in the schema — but a backfill that re-imported a day of payments
    # would otherwise inflate the label rather than fail.
    seen: set[str] = set()
    paid = 0.0
    for p in rows:
        key = p.receipt_number or p.id
        if key in seen:
            continue
        seen.add(key)
        paid += float(p.amount or 0.0)

    # LATE PAYMENTS DO NOT COUNT, and are recorded rather than discarded. Money
    # arriving on day 31 is a real recovery and a false negative for this label;
    # keeping the figure means the horizon can be argued about with evidence
    # instead of opinion.
    late = (
        db.query(Payment)
        .filter(Payment.case_id == prediction.case_id,
                Payment.status == PaymentStatus.VERIFIED,
                Payment.payment_date > end)
        .all()
    )
    paid_after = sum(float(p.amount or 0.0) for p in late)

    threshold = MATERIAL_PAYMENT_RATIO * min(baseline["overdue_amount"],
                                             baseline["emi_amount"])
    recovered = paid >= threshold
    return OutcomeResult(
        status=OutcomeStatus.RECOVERED if recovered else OutcomeStatus.NOT_RECOVERED,
        actual_outcome=0 if recovered else 1,
        amount_paid=round(paid, 2),
        threshold=round(threshold, 2),
        n_payments=len(seen),
        paid_after_window=round(paid_after, 2),
    )


# ---------------------------------------------------------------------------
# The batch
# ---------------------------------------------------------------------------

def attach_outcomes(db: Session, model_name: str = "recovery_risk", *,
                    as_of: date | None = None,
                    horizon_days: int | None = None,
                    commit: bool = True) -> dict:
    """Label every matured, unlabelled prediction for one model.

    Only rows whose horizon has elapsed are touched. `actual_outcome` stays NULL
    for censored and unusable rows — NULL means "not known", never "did not
    pay", and monitor.py counts on that distinction.
    """
    horizon = horizon_days or settings.REPAYMENT_OUTCOME_HORIZON_DAYS
    today = as_of or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=horizon)

    due = (
        db.query(ModelPrediction)
        .filter(ModelPrediction.model_name == model_name,
                ModelPrediction.actual_outcome.is_(None),
                ModelPrediction.outcome_status.is_(None),
                ModelPrediction.as_of_date <= cutoff)
        .all()
    )

    counts: dict[str, int] = {}
    labelled = 0
    for row in due:
        res = evaluate(db, row, as_of=today, horizon_days=horizon)
        counts[res.status.value] = counts.get(res.status.value, 0) + 1
        row.outcome_status = res.status.value
        row.outcome_definition_version = OUTCOME_DEFINITION_VERSION
        row.outcome_horizon_days = horizon
        row.outcome_attached_at = datetime.now(timezone.utc)
        if res.status in LABELLED:
            row.actual_outcome = res.actual_outcome
            labelled += 1

    if commit and due:
        db.commit()

    summary = {
        "model": model_name,
        "definition_version": OUTCOME_DEFINITION_VERSION,
        "horizon_days": horizon,
        "as_of": str(today),
        "cutoff": str(cutoff),
        "due": len(due),
        "labelled": labelled,
        "by_status": counts,
    }
    logger.info("ml.outcomes.attached %s", summary)
    return summary
