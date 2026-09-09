# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Labels every matured prediction under BOTH definitions and
#   reports where they disagree, changing neither.
#
#   WHY. The decision to standardise on one label and retrain should rest on a
#   measured disagreement rate, not on which labeller happened to be written
#   first. This produces that measurement the day outcomes mature.
#
#   IT CALLS THE REAL REPAYMENT RULE. `RepaymentService._infer_outcome` is
#   invoked, not reimplemented — a second copy of a rule this repo has already
#   been bitten by (two risk_score formulas, two recovery_potential writers,
#   seven DPD-bucket spellings) would drift the moment either side changed, and
#   the comparison would then measure the drift instead of the difference.
#   `_build_repayment_inputs` mirrors `attach_outcomes`'s query shape for the
#   same reason: feeding the real function a differently-filtered input would
#   compare against a labeller variant that does not exist in production.
#
#   IT AFFECTS NOTHING. It writes only `ModelPrediction.label_comparison`, never
#   `actual_outcome`. No allocator path reads it, monitor.py does not read it,
#   and no metric under ml/artifacts is computed from it.
# ───────────────────────────────────────────────────────────────────────────
"""
Dual labelling: the model's rule and the repayment labeller's, side by side.

    from app.ml.pipeline.label_comparison import compare_all
    report = compare_all(db, "recovery_risk")

THREE THINGS THE CODE SAYS THAT THE NAMES DO NOT
------------------------------------------------
Each was read out of the source, not assumed, and each changes how the
disagreement numbers should be read.

1.  **The 90% ratio does not gate the positive class.** `_infer_outcome`
    returns PARTIAL whenever `received > 0`, and the trainer's
    `POSITIVE_OUTCOMES = {REPAID, PARTIAL}` maps BOTH to 1. So once binarised,
    the repayment bar is `any payment > 0`. `REPAYMENT_FULL_RATIO` (0.9) only
    splits REPAID from PARTIAL — two labels that are the same class. "The 90%
    rule" sounds stricter than 0.8x a cycle; binarised it is far looser.

2.  **The two sides count different money.** `attach_outcomes` builds
    `payments_by_case` with **no status filter at all**, and `_infer_outcome`
    does not filter either — so a REJECTED or REVERSED receipt counts as money
    received. `_RECOVERED_PAYMENT_STATUSES` exists but is applied only in
    `_received_within`, the recovery pass. The model rule requires
    `PaymentStatus.VERIFIED`. Reproduced here rather than corrected, because
    the brief was to compare the labellers as they stand; surfaced as the
    `payment_status` disagreement cause so the size of it is visible.

3.  **The two sides censor on different evidence.** `_infer_outcome` censors
    only when NOTHING was received AND every case is CLOSED / WRITTEN_OFF /
    ESCALATED. `outcomes.censoring_status` censors on a write-off, a
    settlement, a recall note or a DECEASED tag regardless of what was paid.
    Neither is a superset of the other.

THE TWO RULES, AS THEY ACTUALLY BINARISE
----------------------------------------
    model      recovered <=> VERIFIED paid on THIS CASE
                             >= 0.8 * min(overdue, emi)   [both frozen at
                                                           prediction time]

    repayment  recovered <=> payments of ANY STATUS on ANY CASE of the loan
                             > 0

WHY DISAGREEMENT SHOULD BE ONE-DIRECTIONAL. The model's countable money is a
subset of the repayment rule's (narrower status filter, narrower case scope) and
its bar is higher (a positive threshold vs. zero), so

    model positive  =>  repayment positive

Every model-positive row should be repayment-positive, and disagreement should
run one way: repayment says recovered, the model does not.
`test_model_positive_implies_repayment_positive` asserts that on generated books
rather than trusting the algebra, and `Cause.DIRECTION_ANOMALY` catches it in
production data if it ever stops holding.

APPLES TO APPLES
----------------
One horizon, one `as_of_date`, both sides. `_infer_outcome` reads
`settings.REPAYMENT_OUTCOME_HORIZON_DAYS` directly off settings and takes no
horizon argument, so a `horizon_days` override that differed from settings would
silently move only the model's window. `shared_horizon()` is therefore the only
source, and a conflicting override raises rather than skewing the comparison.
Both sides count payments strictly after the observation day through day
`horizon`: the repayment rule uses `start < payment_date.date() <= end`, the
model uses `end-of-day(as_of) < payment_date <= end-of-day(as_of + horizon)`,
and those select the same payments.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.pipeline.outcomes import (
    MATERIAL_PAYMENT_RATIO, OutcomeResult, OutcomeStatus, baseline_from,
    evaluate, payment_window,
)
from app.models.case import Case
from app.models.model_prediction import ModelPrediction
from app.models.payment import Payment, PaymentStatus
from app.models.repayment_snapshot import CENSORED_OUTCOMES, POSITIVE_OUTCOMES

logger = logging.getLogger(__name__)

#: Bumped when the COMPARISON changes, independently of either label's version.
COMPARISON_VERSION = "label-comparison-1.0.0"

#: What each side reduces to once binarised. Spelled out in every stored row so
#: nobody reads a `repayment.recovered` count as "repaid 90% of what was owed".
REPAYMENT_BINARY_RULE = "any_payment_gt_0_any_status_any_case_of_loan"
MODEL_BINARY_RULE = "verified_paid_this_case_ge_0.8x_min(overdue,emi)"

#: Model statuses that yield no label because the bank removed the account.
CENSORED_MODEL_STATUSES = frozenset({
    OutcomeStatus.CENSORED_SETTLED, OutcomeStatus.CENSORED_WRITTEN_OFF,
    OutcomeStatus.CENSORED_RECALLED, OutcomeStatus.CENSORED_DECEASED,
})


class Cause:
    """Why one row disagrees. Assigned by elimination over measured quantities."""

    CENSOR_MODEL_ONLY = "censoring_model_only"
    CENSOR_REPAYMENT_ONLY = "censoring_repayment_only"
    PAYMENT_STATUS = "payment_status"         # the only money was not VERIFIED
    PAYMENT_SCOPE = "payment_scope"           # money landed on a sibling case
    THRESHOLD = "threshold"                   # money arrived, below the bar
    DIRECTION_ANOMALY = "direction_anomaly"   # model positive, repayment negative
    OTHER = "other"


#: Every cause the report emits, ZERO-FILLED. A `Counter` omits what never
#: happened, which would make "no payment_status disagreements" and "the
#: comparison never looked" the same output — and the second is what a stale or
#: half-wired report looks like. The distinction is the whole reason this module
#: reports counts instead of a verdict, so the keys are always present.
ALL_CAUSES = (
    Cause.THRESHOLD, Cause.PAYMENT_SCOPE, Cause.PAYMENT_STATUS,
    Cause.CENSOR_MODEL_ONLY, Cause.CENSOR_REPAYMENT_ONLY,
    Cause.DIRECTION_ANOMALY, Cause.OTHER,
)

#: The two censoring causes, rolled up. Censoring is not a disagreement about
#: whether the borrower paid — it is one side declining to answer — so it is
#: counted apart from `disagreement_pct` rather than inside it.
CENSORING_CAUSES = (Cause.CENSOR_MODEL_ONLY, Cause.CENSOR_REPAYMENT_ONLY)


def shared_horizon(horizon_days: int | None = None) -> int:
    """The one horizon both sides use.

    `_infer_outcome` hardcodes `settings.REPAYMENT_OUTCOME_HORIZON_DAYS`, so an
    override is only honourable if it matches. Raising beats silently comparing
    a 30-day repayment label against a 60-day model label.
    """
    configured = settings.REPAYMENT_OUTCOME_HORIZON_DAYS
    if horizon_days is not None and horizon_days != configured:
        raise ValueError(
            f"horizon_days={horizon_days} would apply to the model side only: "
            f"RepaymentService._infer_outcome reads "
            f"settings.REPAYMENT_OUTCOME_HORIZON_DAYS ({configured}) directly "
            f"and takes no horizon argument. Change the setting instead."
        )
    return configured


@dataclass
class ComparisonResult:
    """One prediction under both rules, plus the evidence for the attribution."""

    model_status: str
    model_outcome: int | None
    repayment_status: str
    repayment_outcome: int | None
    agrees: bool | None
    cause: str | None
    # Measured sums, all inside the SAME window. Stored because an attribution
    # is only as trustworthy as the numbers it was derived from.
    paid_case_verified: float
    paid_case_all_statuses: float
    paid_loan_verified: float
    paid_loan_all_statuses: float
    model_threshold: float | None
    repayment_denominator: float | None
    repayment_received: float
    as_of_date: str
    horizon_days: int
    comparison_version: str = COMPARISON_VERSION
    model_binary_rule: str = MODEL_BINARY_RULE
    repayment_binary_rule: str = REPAYMENT_BINARY_RULE

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# The repayment side, through the real function
# ---------------------------------------------------------------------------

def _build_repayment_inputs(db: Session, loan_id: str | None):
    """`(cases, payments_by_case)` shaped exactly as `attach_outcomes` builds them.

    NO PAYMENT STATUS FILTER, deliberately — see note 2 in the module docstring.
    Production's labeller counts every payment row regardless of status, and a
    comparison against a VERIFIED-only variant of it would be measuring a
    labeller that does not run anywhere.
    """
    if not loan_id:
        return [], {}
    cases = db.query(Case).filter(Case.loan_id == loan_id).all()
    payments_by_case: dict[str, list[Payment]] = {}
    if cases:
        rows = (db.query(Payment)
                .filter(Payment.case_id.in_([c.id for c in cases])).all())
        for p in rows:
            payments_by_case.setdefault(p.case_id, []).append(p)
    return cases, payments_by_case


def _repayment_label(prediction: ModelPrediction, cases, payments_by_case):
    """`(outcome, received, denominator)` from RepaymentService's own rule.

    A SimpleNamespace stands in for the snapshot row because `_infer_outcome`
    reads exactly one attribute from it, `as_of_date`. If it ever reads another
    this raises AttributeError immediately — the correct failure, since a silent
    second implementation is what this module exists to avoid.
    """
    from app.services.repayment_service import RepaymentService

    row = SimpleNamespace(as_of_date=prediction.as_of_date)
    outcome, amount = RepaymentService._infer_outcome(row, cases, payments_by_case)

    # The denominator, recomputed for REPORTING ONLY. The label above is the
    # real function's; this exists so both sides' arithmetic can be read together.
    start = prediction.as_of_date
    denom = 0.0
    for case in cases:
        paid_by_start = sum(
            float(p.amount or 0.0) for p in payments_by_case.get(case.id, [])
            if p.payment_date and p.payment_date.date() <= start)
        outstanding = float(case.target_amount or 0.0) - paid_by_start
        if outstanding > 0:
            denom += outstanding
    return outcome, float(amount or 0.0), round(denom, 2)


def _utc(dt: datetime | None) -> datetime | None:
    """Make a stored timestamp comparable against the aware window bounds.

    `Payment.payment_date` is `DateTime(timezone=True)`, but that is a request
    the backend may decline: Postgres returns an aware datetime, SQLite — which
    the test suite builds the schema on — drops the offset and returns a naive
    one. `outcomes.evaluate` never meets this because its window filter is
    evaluated in SQL; this module filters in Python so it can report the same
    payments under four different lenses in one pass, and so it has to say what
    a naive value means. UTC, matching how they are written.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def _sum_in_window(payments_by_case, case_ids, start, end, *,
                   verified_only: bool) -> float:
    """Deduped payment total inside the shared window."""
    seen: set[str] = set()
    total = 0.0
    for cid in case_ids:
        for p in payments_by_case.get(cid, []):
            if verified_only and p.status != PaymentStatus.VERIFIED:
                continue
            when = _utc(p.payment_date)
            if when is None or not (start < when <= end):
                continue
            key = p.receipt_number or p.id
            if key in seen:
                continue
            seen.add(key)
            total += float(p.amount or 0.0)
    return round(total, 2)


def _attribute(model_outcome: int, rep_outcome: int, threshold: float | None,
               paid_case_v: float, paid_loan_v: float,
               paid_loan_a: float) -> str:
    """Which structural difference produced this disagreement.

    By ELIMINATION over measured sums, in the order the differences bite: a row
    whose only money was non-VERIFIED cannot be explained by case scope or by
    the threshold, because under the model's filter no money arrived at all.
    """
    # Both conventions: 1 = not recovered, 0 = recovered.
    if model_outcome == 0 and rep_outcome == 1:
        # Should be unreachable — the model's countable money is a subset of the
        # repayment rule's and its bar is higher. Flagged, never explained away.
        return Cause.DIRECTION_ANOMALY
    if paid_loan_v <= 0 < paid_loan_a:
        return Cause.PAYMENT_STATUS
    if paid_case_v <= 0 < paid_loan_v:
        return Cause.PAYMENT_SCOPE
    if threshold is not None and 0 < paid_case_v < threshold:
        return Cause.THRESHOLD
    return Cause.OTHER


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------

def compare(db: Session, prediction: ModelPrediction, *,
            as_of: date | None = None,
            horizon_days: int | None = None) -> ComparisonResult:
    """Label one prediction under both rules. Writes nothing."""
    horizon = shared_horizon(horizon_days)
    today = as_of or datetime.now(timezone.utc).date()

    # THE STORED LABEL WINS, when there is one. `attach_outcomes` has already
    # committed `actual_outcome` and `outcome_status` for this row; re-deriving
    # them here can reach a DIFFERENT answer, because `evaluate` reads state
    # that keeps moving — a write-off recorded after the row matured would make
    # the comparison call it censored while the attached label says otherwise.
    # The comparison's job is to compare the two LABELS, so it has to compare
    # the one that exists rather than a fresh opinion about it.
    #
    # 2026-09-09, found in the Phase 4 replay: labelling every cohort at the end
    # of the book let late lifecycle events censor early predictions, which
    # dropped the comparable bad rate from 0.70 to 0.51 and fired the monitor's
    # retrain trigger on a healthy book.
    if prediction.outcome_status:
        model = OutcomeResult(
            status=OutcomeStatus(prediction.outcome_status),
            actual_outcome=prediction.actual_outcome,
        )
    else:
        model = evaluate(db, prediction, as_of=today, horizon_days=horizon)

    cases, payments_by_case = _build_repayment_inputs(db, prediction.loan_id)
    rep_raw, rep_received, rep_denom = _repayment_label(
        prediction, cases, payments_by_case)

    # ── The evidence, all inside the one shared window ──────────────────────
    start, end = payment_window(prediction.as_of_date, horizon)
    this_case = [prediction.case_id] if prediction.case_id else []
    loan_cases = [c.id for c in cases]
    paid_case_v = _sum_in_window(payments_by_case, this_case, start, end,
                                 verified_only=True)
    paid_case_a = _sum_in_window(payments_by_case, this_case, start, end,
                                 verified_only=False)
    paid_loan_v = _sum_in_window(payments_by_case, loan_cases, start, end,
                                 verified_only=True)
    paid_loan_a = _sum_in_window(payments_by_case, loan_cases, start, end,
                                 verified_only=False)

    baseline = baseline_from(prediction)
    threshold = (MATERIAL_PAYMENT_RATIO * min(baseline["overdue_amount"],
                                              baseline["emi_amount"])
                 if baseline else None)

    model_censored = model.status in CENSORED_MODEL_STATUSES
    rep_censored = rep_raw in CENSORED_OUTCOMES
    rep_outcome = None if (rep_censored or rep_raw is None) else (
        0 if rep_raw in POSITIVE_OUTCOMES else 1)

    agrees: bool | None = None
    cause: str | None = None
    if model.actual_outcome is not None and rep_outcome is not None:
        agrees = model.actual_outcome == rep_outcome
        if not agrees:
            cause = _attribute(model.actual_outcome, rep_outcome, threshold,
                               paid_case_v, paid_loan_v, paid_loan_a)
    elif model_censored and not rep_censored:
        cause = Cause.CENSOR_MODEL_ONLY
    elif rep_censored and not model_censored:
        cause = Cause.CENSOR_REPAYMENT_ONLY

    return ComparisonResult(
        model_status=model.status.value,
        model_outcome=model.actual_outcome,
        repayment_status=str(rep_raw) if rep_raw is not None else "NONE",
        repayment_outcome=rep_outcome,
        agrees=agrees,
        cause=cause,
        paid_case_verified=paid_case_v,
        paid_case_all_statuses=paid_case_a,
        paid_loan_verified=paid_loan_v,
        paid_loan_all_statuses=paid_loan_a,
        model_threshold=round(threshold, 2) if threshold is not None else None,
        repayment_denominator=rep_denom,
        repayment_received=round(rep_received, 2),
        as_of_date=str(prediction.as_of_date),
        horizon_days=horizon,
    )


# ---------------------------------------------------------------------------
# The batch and the report
# ---------------------------------------------------------------------------

def compare_all(db: Session, model_name: str = "recovery_risk", *,
                as_of: date | None = None, horizon_days: int | None = None,
                store: bool = True, commit: bool = True,
                max_examples: int = 10) -> dict:
    """Compare every MATURED prediction under both rules.

    Writes only `label_comparison`. `actual_outcome` is never touched and
    nothing downstream reads the result — this is evidence for a decision, not
    an input to one.
    """
    horizon = shared_horizon(horizon_days)
    today = as_of or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=horizon)

    rows = (db.query(ModelPrediction)
            .filter(ModelPrediction.model_name == model_name,
                    ModelPrediction.as_of_date <= cutoff)
            .all())

    model_counts: Counter = Counter()
    rep_counts: Counter = Counter()
    cause_counts: Counter = Counter()
    both_labelled = agree = disagree = 0
    # The UNION, not the sum: a row censored by both sides is one censored row.
    # Deriving it from `cause_breakdown` would have counted only the rows
    # censored by exactly ONE side, since a mutual censor produces no cause.
    censored_either = 0
    examples: list[dict] = []

    for row in rows:
        res = compare(db, row, as_of=today, horizon_days=horizon)
        model_counts[res.model_status] += 1
        rep_counts[res.repayment_status] += 1
        if (res.model_status.startswith("CENSORED")
                or res.repayment_status in CENSORED_OUTCOMES):
            censored_either += 1
        if res.cause:
            cause_counts[res.cause] += 1
        if res.agrees is not None:
            both_labelled += 1
            if res.agrees:
                agree += 1
            else:
                disagree += 1
                if len(examples) < max_examples:
                    examples.append({"prediction_id": row.id,
                                     "case_id": row.case_id,
                                     "loan_id": row.loan_id,
                                     **res.to_dict()})
        if store:
            row.label_comparison = res.to_dict()

    if store and commit and rows:
        db.commit()

    model_labelled = (model_counts.get(OutcomeStatus.RECOVERED.value, 0)
                      + model_counts.get(OutcomeStatus.NOT_RECOVERED.value, 0))
    rep_recovered = sum(v for k, v in rep_counts.items() if k in POSITIVE_OUTCOMES)
    rep_censored = sum(v for k, v in rep_counts.items() if k in CENSORED_OUTCOMES)
    rep_not_recovered = sum(
        v for k, v in rep_counts.items()
        if k not in POSITIVE_OUTCOMES and k not in CENSORED_OUTCOMES
        and k != "NONE")

    report = {
        "model": model_name,
        "comparison_version": COMPARISON_VERSION,
        "as_of": str(today),
        "horizon_days": horizon,
        "definitions": {
            "model": MODEL_BINARY_RULE,
            "repayment": REPAYMENT_BINARY_RULE,
            "note": "REPAYMENT_FULL_RATIO (0.9) splits REPAID from PARTIAL and "
                    "POSITIVE_OUTCOMES contains both, so it does not gate the "
                    "positive class.",
        },
        "total_matured": len(rows),
        "model_definition": {
            "labelled": model_labelled,
            "recovered": model_counts.get(OutcomeStatus.RECOVERED.value, 0),
            "not_recovered": model_counts.get(OutcomeStatus.NOT_RECOVERED.value, 0),
            "censored": sum(v for k, v in model_counts.items()
                            if k.startswith("CENSORED")),
            "unusable": (model_counts.get(OutcomeStatus.NO_BASELINE.value, 0)
                         + model_counts.get(OutcomeStatus.NO_CASE.value, 0)),
            "by_status": dict(model_counts),
        },
        "repayment_definition": {
            "labelled": rep_recovered + rep_not_recovered,
            "recovered": rep_recovered,
            "not_recovered": rep_not_recovered,
            "censored": rep_censored,
            "by_status": dict(rep_counts),
        },
        "comparable_rows": both_labelled,
        "agree": agree,
        "disagree": disagree,
        # Denominator is COMPARABLE rows, not matured ones: a row only one side
        # labelled cannot agree or disagree, and folding it into either number
        # would dilute the rate with rows that were never in the question.
        "disagreement_pct": round(100.0 * disagree / both_labelled, 2)
        if both_labelled else None,
        "censored_either_side": censored_either,
        "censoring_pct": round(100.0 * censored_either / len(rows), 2)
        if rows else None,
        "cause_breakdown": {c: cause_counts.get(c, 0) for c in ALL_CAUSES},
        "examples": examples,
    }
    logger.info("ml.label_comparison model=%s version=%s disagree=%s/%s causes=%s",
                model_name, COMPARISON_VERSION, disagree, both_labelled,
                dict(cause_counts))
    return report
