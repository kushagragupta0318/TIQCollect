# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. The one place a model's definition lives: target, features,
#   split, and the acceptance gates it must clear. Everything else in
#   ml/pipeline reads a ModelSpec rather than carrying its own constants, so a
#   model cannot be trained under one definition and evaluated under another.
#
#   THE GATES ARE THE POINT. They are not reporting thresholds, they are
#   failures. `gini_suspicious` in particular exists because this codebase has
#   already produced and reported a perfect model: the shadow metadata written
#   2026-09-07 records A_scorecard at roc_auc 1.0000 (Gini 1.00) on a 40-row
#   test set, sitting beside a trained model at 0.4825 (Gini -0.04). In
#   collections a Gini above 0.60 is nearly always a leak, so it stops the
#   pipeline and demands a written justification rather than printing a happy
#   number.
# ───────────────────────────────────────────────────────────────────────────
"""
Model specifications and acceptance gates.

TARGET POLARITY, FIXED ONCE AND NEVER RE-DERIVED
------------------------------------------------
Every classification model here predicts the RISK event: y = 1 means the bad
thing happened (no material payment; the borrower was not contacted). So:

    higher score            = worse
    decile 1                = riskiest
    bad rate                = must DECREASE monotonically from decile 1 to 10

`evaluate.py` assumes exactly this. If a future model has the opposite polarity,
flip the target when building the frame — do not flip the evaluation, or half
the gates will silently invert.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


# ---------------------------------------------------------------------------
# Acceptance gates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Gates:
    """What a model must clear to be shippable. Every one of these fails loudly.

    The bands are the ones a credit-risk function would recognise for a
    behaviour scorecard on a delinquent retail book. They are deliberately
    two-sided: a model can fail for being too weak OR too strong, because the
    second is nearly always a leak wearing a good result's clothes.
    """

    gini_min: float = 0.25
    gini_target_low: float = 0.35
    gini_target_high: float = 0.55
    gini_suspicious: float = 0.60      # hard stop -> investigate leakage

    ks_min: float = 20.0
    ks_suspicious: float = 60.0

    # Rank order. A break is a decile whose bad rate is HIGHER than the decile
    # above it — i.e. the score got the order wrong at that cut.
    max_breaks_total: int = 1
    max_breaks_top5: int = 0

    # Top-decile lift, expressed as a SHARE OF THE ARITHMETIC MAXIMUM (which is
    # 1 / bad_rate). Absolute lift thresholds are unusable across books: at a
    # 70% bad rate the ceiling is 1.43x, so "suspicious above 5x" can never
    # fire, and "at least 1.2x" is nearly free. Measured here, the model reaches
    # 1.349x against a ceiling of 1.427x — 94.5% of what is achievable — which
    # an absolute threshold would have reported as an unimpressive 1.35.
    # lift_capture is the top decile's bad rate (see evaluate.py for the
    # identity). The upper bound is a WEAK check and is documented as one: its
    # only job is catching near-perfect separation of the riskiest decile. On a
    # book with a 70% base rate, 0.97 is unremarkable — a random decile is
    # already 0.70 — so the threshold sits just below 1.0 rather than pretending
    # to a precision it does not have. Gini and KS are the real leak gates.
    lift_capture_min: float = 0.55
    lift_capture_suspicious: float = 0.995

    # Overfit: train minus test Gini.
    max_train_test_gini_gap: float = 0.10

    # Population stability between development and out-of-time.
    psi_warn: float = 0.10
    psi_fail: float = 0.25

    # Single-feature information value.
    #
    # THE FAMILIAR "IV > 0.5 MEANS A LEAK" RULE IS AN APPLICATION-SCORECARD
    # RULE, and applying it here unchanged would have been wrong. Measured on
    # this book, eight features clear 0.5 — dpd 0.738, arrears_ratio 0.720,
    # dpd_bucket 0.695, paid_ratio_3m 0.642 — and every one of them is
    # legitimately that strong: they are all computed strictly before as_of,
    # while the outcome is drawn strictly after. On a BEHAVIOUR scorecard over
    # a delinquent book, days-past-due genuinely is the single best predictor
    # of next-cycle payment, and a 0.5 ceiling would discard the model's spine
    # to satisfy a heuristic imported from a different problem.
    #
    # So there are two levels. Above `iv_review` a feature is flagged and
    # listed in the model document for a human to sign off. Above `iv_max` it
    # is dropped outright — an IV that high is not a strong feature, it is the
    # answer.
    iv_min: float = 0.02
    iv_review: float = 0.50
    iv_max: float = 0.80

    # Multicollinearity and correlation pruning.
    vif_max: float = 5.0
    corr_max: float = 0.70

    # Binning shape.
    min_bin_fraction: float = 0.05
    min_bin_events: int = 30

    # No segment may be dead.
    segment_gini_min: float = 0.20


# ---------------------------------------------------------------------------
# Model specification
# ---------------------------------------------------------------------------

@dataclass
class ModelSpec:
    """Everything that defines one model. Serialised into every artifact."""

    name: str
    version: str
    description: str

    target: str
    time_col: str = "as_of_date"
    split_col: str = "month_index"
    id_cols: tuple[str, ...] = ("loan_id", "borrower_id")

    numeric_features: tuple[str, ...] = ()
    categorical_features: tuple[str, ...] = ()

    # Chronological split. Out-of-time is the LAST slice and is never used for
    # fitting, binning or selection — only for the final read.
    train_frac: float = 0.60
    valid_frac: float = 0.15          # remainder is out-of-time test

    # Expected direction of each feature's relationship with RISK.
    # +1 : higher value  -> higher risk      (e.g. dpd)
    # -1 : higher value  -> lower risk       (e.g. cibil_score)
    # A fitted coefficient that contradicts this is dropped however significant
    # it is, because a scorecard that says a higher CIBIL is riskier cannot be
    # defended to a credit committee.
    expected_sign: dict[str, int] = field(default_factory=dict)

    # Segments the model must work on, not just the book as a whole.
    segment_cols: tuple[str, ...] = ("dpd_bucket", "loan_type", "city")

    # Scorecard scaling. PDO = points to double the odds.
    pdo: int = 20
    base_score: int = 600
    base_odds: float = 50.0

    gates: Gates = field(default_factory=Gates)

    # Never features, whatever a frame happens to contain. TWO DIFFERENT RULES
    # share this one tuple, and conflating them cost an investigation on
    # 2026-09-11 — the comment here used to call both "the point-in-time ban",
    # which is true of only the first:
    #
    #   LEAKAGE, a timing violation. `y`, `recovered_amount`, `visit_made`,
    #   `customer_met`, `ptp_set`, `ptp_kept` and the `_willingness`/`_capacity`
    #   latents are drawn from the outcome window or from the generator's hidden
    #   state. They are the answer. A model reading them scores near-perfectly
    #   and is worth nothing.
    #
    #   SYSTEM OUTPUTS, an architectural rule. `risk_score`,
    #   `recovery_potential`, `repayment_likelihood`, `allocation_score` and
    #   `visit_priority_score` are this repo's own hand-weighted scores. These
    #   do NOT leak: they are computed from a backward-looking window that is
    #   disjoint from the target window. They are banned because they are
    #   redundant (a non-linear fit rebuilds `likelihood` from the champion's
    #   four features at R^2 0.845), because they double-count (adding one pulls
    #   dpd's coefficient from -0.797 to -0.664), and because a scorecard
    #   reweighting would move the model's inputs without a single borrower
    #   changing. Measured: every arm that forced one in lost ground, the best
    #   of them by -0.0001 OOT Gini.
    #
    # Both rules are kept, and the tuple stays exactly as it is. The distinction
    # matters when somebody asks "is this really necessary" — for the first
    # group the answer is arithmetic, for the second it is evidence, and only
    # the second can ever be revisited by measurement.
    forbidden: tuple[str, ...] = (
        "y", "recovered_amount", "visit_made", "customer_met",
        "ptp_set", "ptp_kept", "_willingness", "_capacity",
        "_willingness_anchor", "_capacity_anchor",
        "risk_score", "recovery_potential", "repayment_likelihood",
        "allocation_score", "visit_priority_score",
    )

    @property
    def all_features(self) -> list[str]:
        return list(self.numeric_features) + list(self.categorical_features)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gates"] = asdict(self.gates)
        return d


# ---------------------------------------------------------------------------
# The models this repo builds
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SERVING AVAILABILITY — the constraint that decides this list
# ---------------------------------------------------------------------------
# A feature the product cannot supply at scoring time is not a feature, it is a
# training artifact. The first fitted recovery model selected utilization_pct,
# other_lender_delinq and bounce_count_6m; NONE of the three exists anywhere in
# the live schema (checked against models/customer.py, loan.py and case.py), so
# the DecisionEngine would have been handed 3 of 6 inputs, fallen under its
# coverage floor and declined every request. The model would have looked
# excellent in its own document and scored nothing.
#
# So the candidate set below is exactly what the running product can produce
# today, from columns that exist or from history it already stores:
#
#   direct        Loan.dpd, .emi_amount, .sanctioned_amount, .interest_rate,
#                 .tenure_months, .outstanding_principal, .total_outstanding,
#                 .overdue_amount, .penal_charges, .loan_type, .branch_code,
#                 Customer.cibil_score, .city, .customer_segment
#   derived       arrears_ratio, penal_ratio, outstanding_to_sanction, age,
#                 months_on_book, is_secured, days_since_last_payment
#   from history  visits_3m/6m, contact_rate_6m, days_since_last_contact,
#                 distinct_agents_6m, ptp_set_6m, ptp_kept_6m, ptp_kept_ratio,
#                 paid_ratio_3m/6m/12m
#
# DELIBERATELY EXCLUDED, and why — every one of these is a field a real lender
# has and this product does not yet store. They are listed rather than dropped
# silently, because "extend the daily feed to carry these" is a concrete and
# cheap product decision, and the sweep in the model document shows what it
# would be worth:
#
#   bureau        utilization_pct, num_enquiries_6m, credit_vintage_months,
#                 num_open_loans, other_lender_delinq, thin_file
#   affordability monthly_income, dti_ratio
#   contactability address_vintage_months, phone_verified, mail_returned_count
#   origination   sourcing_channel, residence_type
#   payments      bounce_count_6m
#
# EXCLUDED FOR A DIFFERENT AND HARDER REASON — max_dpd_12m, times_30plus_12m,
# times_60plus_12m, months_since_first_delinq, consecutive_misses. These are
# delinquency HISTORY, and `Loan.dpd` is overwritten in place with no history
# anywhere in this schema (models/repayment_snapshot.py says so explicitly).
# They cannot be reconstructed for a past date, so a model trained on them
# could never be scored honestly in production even though the simulator can
# produce them perfectly. Adding them means adding a history table first.
FEED_ONLY_FEATURES = (
    "utilization_pct", "num_enquiries_6m", "credit_vintage_months",
    "num_open_loans", "other_lender_delinq", "thin_file",
    "monthly_income", "dti_ratio",
    "address_vintage_months", "phone_verified", "mail_returned_count",
    "sourcing_channel", "residence_type", "bounce_count_6m",
)
NO_HISTORY_FEATURES = (
    "max_dpd_12m", "times_30plus_12m", "times_60plus_12m",
    "months_since_first_delinq", "consecutive_misses",
)

_RECOVERY_NUMERIC = (
    # loan — direct columns
    "cibil_score", "sanction_amount", "emi_amount", "tenure_months",
    "interest_rate", "months_on_book", "is_secured",
    # delinquency — current state only
    "dpd", "outstanding_principal", "overdue_amount", "penal_charges",
    "total_outstanding", "arrears_ratio", "penal_ratio", "outstanding_to_sanction",
    # payment behaviour — from the payment ledger, which IS append-only
    "paid_ratio_3m", "paid_ratio_6m", "paid_ratio_12m", "days_since_last_payment",
    # contact / field — from the visit table
    "visits_3m", "visits_6m", "contact_rate_6m", "days_since_last_contact",
    "distinct_agents_6m",
    # promises — from the PTP table
    "ptp_set_6m", "ptp_kept_6m", "ptp_kept_ratio",
    # demographic
    "age",
)

_RECOVERY_CATEGORICAL = (
    "loan_type", "dpd_bucket", "city", "employment_type", "branch_code",
)

# Direction of each feature against RISK (y = 1 is bad).
_RECOVERY_SIGNS = {
    "cibil_score": -1, "credit_vintage_months": -1, "num_open_loans": +1,
    "num_enquiries_6m": +1, "other_lender_delinq": +1, "utilization_pct": +1,
    "thin_file": +1,
    "address_vintage_months": -1, "phone_verified": -1, "mail_returned_count": +1,
    "sanction_amount": 0, "emi_amount": 0, "tenure_months": 0,
    "interest_rate": +1, "months_on_book": 0, "is_secured": -1,
    "dpd": +1, "max_dpd_12m": +1, "times_30plus_12m": +1, "times_60plus_12m": +1,
    "months_since_first_delinq": +1, "consecutive_misses": +1,
    "outstanding_principal": 0, "overdue_amount": +1, "penal_charges": +1,
    "total_outstanding": 0, "arrears_ratio": +1, "penal_ratio": +1,
    "outstanding_to_sanction": +1,
    "paid_ratio_3m": -1, "paid_ratio_6m": -1, "paid_ratio_12m": -1,
    "days_since_last_payment": +1, "bounce_count_6m": +1,
    "visits_3m": 0, "visits_6m": 0, "contact_rate_6m": -1,
    "days_since_last_contact": +1, "distinct_agents_6m": 0,
    "ptp_set_6m": 0, "ptp_kept_6m": -1, "ptp_kept_ratio": -1,
    "age": 0, "monthly_income": -1, "dti_ratio": +1,
}

RECOVERY_RISK = ModelSpec(
    name="recovery_risk",
    # 1.1.0 — adds segment-wise probability calibration on overdue_amount.
    # A calibration layer changes what the number MEANS, so it is a model change
    # and gets a version, per the rule the two scorecards already follow. 1.0.0
    # remains on disk: it is the version the first allocator shadow was measured
    # against and the comparison is only readable if it survives.
    version="1.1.0",
    description=(
        "Probability that a delinquent account makes NO material payment in the "
        "next cycle. Behaviour scorecard: WOE + logistic regression, with a "
        "gradient-boosting challenger. Feeds the allocator's expected-recovery "
        "term and the manager priority surfaces."
    ),
    target="y",
    numeric_features=_RECOVERY_NUMERIC,
    categorical_features=_RECOVERY_CATEGORICAL,
    expected_sign=_RECOVERY_SIGNS,
)

# Contactability. Same machinery, different question and a different label —
# this is the model that gives route planning learned time windows instead of
# assumed ones. Trained only on rows where a visit was actually attempted,
# because "was the borrower met" is undefined where nobody went.
CONTACT_RISK = ModelSpec(
    name="contact_risk",
    version="1.0.0",
    description=(
        "Probability that an attempted visit does NOT meet the borrower. "
        "Population: rows with visit_made = 1. Intended to give route planning "
        "learned time windows instead of assumed ones."
    ),
    target="not_contacted",
    # contact_rate_6m IS included, and excluding it was a mistake worth
    # recording. It was left out on a reflex that "prior contact predicting
    # contact" smelled circular. It is not: the ratio is computed strictly
    # BEFORE as_of and the outcome is drawn strictly after, which makes it a
    # lagged behavioural feature of exactly the same kind as paid_ratio_6m in
    # the recovery model. Starved of it, the first run selected only three
    # features and scored Gini 0.168.
    numeric_features=_RECOVERY_NUMERIC,
    categorical_features=_RECOVERY_CATEGORICAL,
    expected_sign=_RECOVERY_SIGNS,
)

REGISTRY: dict[str, ModelSpec] = {
    RECOVERY_RISK.name: RECOVERY_RISK,
    CONTACT_RISK.name: CONTACT_RISK,
}

SplitName = Literal["train", "valid", "oot"]
