# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-24 (later, v1.1.0) — PAYMENT_MOMENTUM abstains when nothing arrived in
#   the behaviour window. Found by the first dry run against the live book, not by
#   reading the code: 227 loans (43%) were taking a positive momentum score having
#   received nothing, on the strength of a stale last_payment_amount. The fix is a
#   one-line guard; no weight, threshold, band edge or BASE_RATE moved. Full
#   reasoning at _payment_momentum. 19 loans changed band as a result.
#
# 2026-08-24 — New file. Loan.recovery_potential was HIGH/MEDIUM/LOW drawn from
#   random.choices (seed_data.py:481-509) and from a second, separately-weighted
#   random() UPDATE in scripts/add_recovery_potential.py. Two writers, both
#   random, disagreeing with each other — and nothing in the codebase ever read
#   the column, so nobody noticed. This module replaces both.
#
#   WE COMPUTE THIS. The brief that prompted the work said "the code currently
#   suggests both" — that we calculate it, and that the bank sends it. The code
#   does say both, but the two sides are not evenly matched:
#     * models/loan.py:97 called it a "daily recovery tag pushed by Command
#       Centre". Nothing has ever pushed it. That comment has been corrected.
#     * SOURCE_COMMAND_CENTER (repayment_snapshot.py:50) and
#       REPAYMENT_SCORER="command_center" are deliberate placeholders for a tier
#       that was never built — their own changelogs say so.
#     * Against that: the bank's daily feed carries 45 columns
#       (scripts/sample_daily_feed.csv) and recovery potential is not one of
#       them, and ingest_daily.py has no recovery path at all.
#   So the seam stays open (RECOVERY_SCORER, recovery_source on every snapshot)
#   and the answer today is: we compute it.
#
#   This is a SCORECARD, not a model. Hand-chosen weights over arithmetic. No
#   training, no fitting, no held-out set — and therefore NO AUC, NO Gini and no
#   accuracy claim anywhere, in code, on the wire or in the UI. The same rule
#   ml/repayment_scorecard.py states for itself, for the same reason
#   LLMResult.ai_generated exists in core/llm.py. The snapshots this produces are
#   what could make a model possible later; they are not evidence of one now.
#
#   NO ABSOLUTE-RUPEE FACTOR, deliberately. An earlier draft scored
#   total_outstanding directly. That is wrong: this predicts a RATE, and a
#   ₹2,00,000 loan and a ₹20,000 loan can both recover 40%. Feeding rupees into
#   a rate conflates "how much is at stake" with "what share comes back", and
#   the label then quietly means both at once. Every factor here is a normalised
#   ratio or a category. The money question is answered separately and honestly
#   by expected_recoverable_amount() below, which the manager surface sums across
#   the book — a rate cannot be summed, an amount can.
#
#   INDEPENDENT OF THE REPAYMENT SCORE, deliberately. The obvious shortcut is
#   recovery ≈ P(pay) × collectable share. It was rejected three times over:
#   repayment_service._FORBIDDEN_FEATURE_KEYS bans this system's own outputs as
#   inputs; a monotone transform of the likelihood could never produce the single
#   most useful thing this label says (a hostile borrower on a secured loan is
#   UNLIKELY to pay and HIGH to recover); and "ageing, security, penal drag and
#   legal posture" is explainable to a manager in one sentence where "the
#   repayment score times a haircut" invites "then why two numbers?".
#   Shared INPUTS across two scorecards are normal. Shared OUTPUTS fed back in
#   are banned.
#
#   That last line used to read "Shared OUTPUTS fed back in are the LEAK", and
#   leak is the wrong word — corrected 2026-09-11 rather than deleted, because
#   the wrong word was doing real work: it implied a timing violation and so
#   made the rule look self-evident, which stopped anyone measuring it. Measured
#   now: these scores read a strictly backward window and the model's target
#   window is strictly forward, so there is no look-ahead of any kind. The rule
#   survives on redundancy, double counting and version dependency instead —
#   forcing recovery_rate_90 in beside the champion's four features moved
#   out-of-time Gini by -0.0001. See repayment_service._FORBIDDEN_FEATURE_KEYS
#   for the full arm-by-arm figures.
#
#   MONOTONICITY IS STRUCTURAL, not tested-for-and-hoped. The 30/60/90 estimates
#   are not three independent scores — that formulation lets a fast-maturing
#   positive factor and a slow-maturing negative one cross, and rate_30 > rate_90
#   is nonsense that no weight tuning can rule out. Instead: score the EVENTUAL
#   (90-day) rate once, then multiply by a speed fraction bounded so that
#   f30 < f60 < 1 for every possible input. rate_30 <= rate_60 <= rate_90 then
#   holds by construction, for any weights anyone chooses later.
#
#   Helpers (_clamp, _linear, _rupees, _f) are imported from
#   ml/repayment_scorecard.py rather than copied. They belong in a shared
#   ml/scorecard_util.py, but extracting them would mean editing that file, and
#   leaving the shipped repayment scorecard byte-identical was a condition of
#   this work. The extraction is a follow-up; copying them was not an option —
#   duplicated definitions are how this codebase's worst bugs happened.
# ───────────────────────────────────────────────────────────────────────────
"""How much of this loan comes back, how fast — and the reason for every point."""
from __future__ import annotations

from app.ml.eligibility import _SECURED, _UNSECURED
from app.ml.repayment_scorecard import _clamp, _f, _linear, _rupees

# Bumped whenever a weight, a band edge, a maturity share or a factor definition
# changes. Stamped onto every snapshot row, so a label can always be traced to
# the rules that produced it. A weight change is a model change; it must not be
# silent. Versioned separately from SCORECARD_VERSION because the two scorecards
# answer different questions and will move at different times.
#
# 1.1.0 (2026-08-24): PAYMENT_MOMENTUM now abstains whenever nothing was received
# in the behaviour window, instead of falling back to last_payment_amount. A
# FACTOR DEFINITION CHANGED, so the bump is not optional — rows written under
# 1.0.0 answer a different question from rows written under 1.1.0, and a model
# trained across both without filtering on recovery_model_version would be
# learning two scorecards at once. No weight, band edge, maturity share or
# BASE_RATE moved. See _payment_momentum for the evidence behind the change.
RECOVERY_SCORECARD_VERSION = "recovery-scorecard-1.1.0"

# ── Direction ────────────────────────────────────────────────────────────────
# The product concept is a RECOVERY RATE: the share of total_outstanding we get
# back. Higher is better, and it is a fraction in [0, 1] rather than a 0-100
# score — because it gets multiplied by a rupee balance downstream, and a number
# that looks like a percentage but is scaled to 100 is exactly the kind of unit
# confusion that produces an expected-recovery figure a hundred times too large.
#
# 0.35 is the base a loan sits at before any factor speaks: a little over a third
# of the balance. It is a starting point for a scorecard, NOT a measured
# portfolio recovery rate — nothing here has been calibrated against realised
# recoveries, because there are none yet. That is what the snapshot labels are
# accumulating for.
BASE_RATE = 0.35
MIN_RATE = 0.02
MAX_RATE = 0.95

# ── Factor codes ─────────────────────────────────────────────────────────────
FACTOR_AGEING = "AGEING"
FACTOR_ARREARS_SHARE = "ARREARS_SHARE"
FACTOR_PAYMENT_RECENCY = "PAYMENT_RECENCY"
FACTOR_PAYMENT_MOMENTUM = "PAYMENT_MOMENTUM"
FACTOR_PENAL_DRAG = "PENAL_DRAG"
FACTOR_SECURITY = "SECURITY"
FACTOR_LEGAL_POSTURE = "LEGAL_POSTURE"
FACTOR_NPA_STATUS = "NPA_STATUS"

# ── Abstention reasons ───────────────────────────────────────────────────────
NO_BALANCE_ON_RECORD = "NO_OUTSTANDING_BALANCE"
NO_PAYMENT_ON_RECORD = "NO_PAYMENT_ON_RECORD"
NO_RECENT_PAYMENT_ACTIVITY = "NO_RECENT_PAYMENT_ACTIVITY"
NO_PENAL_CHARGES = "NO_PENAL_CHARGES"
UNKNOWN_SECURITY = "SECURITY_UNDETERMINED"
NO_LEGAL_ACTION = "NO_LEGAL_OR_SETTLEMENT_ACTION"
NOT_NPA = "NOT_CLASSIFIED_NPA"

# ── Weights ──────────────────────────────────────────────────────────────────
# Each is the maximum magnitude that factor can move the EVENTUAL recovery rate,
# expressed as a share of the balance. Module constants, deliberately NOT
# settings: an env var could change what the label means without changing
# RECOVERY_SCORECARD_VERSION, and the rows already written would then be stamped
# with rules that no longer exist.

# Collateral. The largest weight, and the reason this scorecard exists as
# something separate from the repayment likelihood: a secured loan recovers
# whether or not the borrower cooperates. Reuses the sets from ml/eligibility.py
# rather than re-deriving them.
_W_SECURITY = 0.16

# How late. Ageing depresses recovery, but note it is worth LESS here than
# delinquency is to the repayment score (30 of 124 there). Willingness collapses
# with DPD faster than recoverable value does — a 200-day secured account is
# still worth pursuing, which is precisely the misallocation this label exists
# to correct.
_W_AGEING = 0.18

# When they last paid anything. A borrower who paid six weeks ago is a different
# proposition from one who has paid nothing in six months.
_W_PAYMENT_RECENCY = 0.14

# Money actually moving inside the behaviour window. Upside-only: money arriving
# is evidence, money not arriving is already counted by recency and ageing, and
# charging it twice would double-penalise the same silence.
_W_PAYMENT_MOMENTUM = 0.12

# Arrears as a share of the balance. Ability, not willingness: a small overdue
# slice against a large balance is a stumble, a large one is structural.
_W_ARREARS_SHARE = 0.10

# Penal charges as a share of the balance. Penalty-only. Charges inflate the ask
# without adding recoverable principal, and a borrower who disputes the charges
# disputes the whole demand.
_W_PENAL_DRAG = 0.06

# Legal and settlement posture. Signed BOTH ways, unlike the repayment
# scorecard's penalty-only version, and that difference is the point: filed
# enforcement RAISES eventual recovery (it is leverage, and secured enforcement
# realises an asset) while an accepted settlement LOWERS the recovered share,
# because agreeing a haircut is what a settlement is. Both also change the
# SPEED, which is handled separately below.
_W_LEGAL_POSTURE = 0.08

# The bank's own NPA classification. Low weight because dpd already carries most
# of it; kept because the bank's own call is information dpd alone loses.
_W_NPA_STATUS = 0.06

TOTAL_WEIGHT = (
    _W_SECURITY + _W_AGEING + _W_PAYMENT_RECENCY + _W_PAYMENT_MOMENTUM
    + _W_ARREARS_SHARE + _W_PENAL_DRAG + _W_LEGAL_POSTURE + _W_NPA_STATUS
)  # 0.90 — the coverage denominator

# ── Reference points, each with a reason rather than a round number ──────────
# Shared with the repayment scorecard's reading of the same column, so the two
# never disagree about what "deep" means.
_DPD_ENTRY = 30.0       # the shallowest account that reaches field collections
_DPD_FLOOR = 180.0      # beyond this, further ageing tells us little new

# Payment recency. 15 days is inside one billing cycle — effectively current.
# 180 days matches REPAYMENT_BEHAVIOUR_WINDOW_DAYS: a borrower with no payment
# anywhere in the behaviour window is at the floor, not merely below average.
_RECENCY_BEST = 15.0
_RECENCY_WORST = 180.0

# Arrears share. Under 5% of the balance overdue is a stumble; at 60% the
# borrower is not behind on a loan, they have stopped servicing it.
_ARREARS_BEST = 0.05
_ARREARS_WORST = 0.60

# Paying 10% of the outstanding balance inside the behaviour window is strong
# momentum for a delinquent book — the point at which this factor is fully
# earned rather than partially.
_MOMENTUM_FULL_SHARE = 0.10

# Penal charges at 10% of the balance is the point of full drag. Beyond it the
# ask is dominated by charges the borrower did not borrow.
_PENAL_HEAVY = 0.10

# Settlement states that mean a haircut is on the table or agreed.
_SETTLEMENT_IN_PLAY = frozenset({"OFFERED", "NEGOTIATING", "ACCEPTED"})

# ── Speed ────────────────────────────────────────────────────────────────────
# How much of the eventual recovery lands by 30 and by 60 days, as a fraction of
# the 90-day figure.
#
# The bounds are what makes monotonicity structural. f30 and f60 share the same
# slope, so f60 - f30 is a positive constant for EVERY speed index, and both are
# at most 1. rate_30 <= rate_60 <= rate_90 therefore holds for any input and any
# future re-weighting — it is not a property that has to be re-verified each time
# someone touches a number.
_SHARE_30_SLOW, _SHARE_30_FAST = 0.10, 0.55
_SHARE_60_SLOW, _SHARE_60_FAST = 0.35, 0.80

# The neutral collection pace: half of what is coming lands inside 60 days.
_SPEED_BASE = 0.50
_SPEED_MIN, _SPEED_MAX = 0.0, 1.0

# Speed adjustments. Signed, and they compose. Each is a documented judgement
# about pace only — none of them changes how much comes back, only when.
_SPEED_RECENT_PAYMENT = 0.20     # paying inside the last 30 days: it is already moving
_SPEED_MOMENTUM = 0.10           # money received in the window
_SPEED_DEEP_DPD = -0.20          # a 180+ day account does not resolve this month
_SPEED_SECURED = -0.15           # asset realisation is measured in quarters
_SPEED_LEGAL_FILED = -0.20       # SARFAESI, suit, DRT and arbitration are slow
_SPEED_SETTLEMENT_AGREED = 0.15  # once a haircut is agreed, the money moves fast

# ── Bands ────────────────────────────────────────────────────────────────────
# Edges on the 90-day rate. In the module rather than settings, for the same
# reason as the weights: a band change is a model change and must move the
# version. The label is banded on the EVENTUAL rate, not on 30 days — banding on
# 30 would mark a slow-but-secured loan LOW and steer agents away from money that
# is genuinely recoverable by quarter-end, which is the failure this replaces.
RECOVERY_HIGH = "HIGH"
RECOVERY_MEDIUM = "MEDIUM"
RECOVERY_LOW = "LOW"

RECOVERY_BAND_EDGES = ((0.50, RECOVERY_HIGH), (0.25, RECOVERY_MEDIUM))
RECOVERY_BAND_FLOOR = RECOVERY_LOW

# The horizons this scorecard speaks to. Order matters: the last is the one the
# label is banded on, and expected_recoverable_amount() reads it.
HORIZONS = (30, 60, 90)
LABEL_HORIZON = 90


def band_for(rate_90: float) -> str:
    """HIGH / MEDIUM / LOW from the eventual recovery rate."""
    for edge, name in RECOVERY_BAND_EDGES:
        if rate_90 >= edge:
            return name
    return RECOVERY_BAND_FLOOR


def expected_recoverable_amount(rate: float, total_outstanding: float) -> float:
    """The rupee figure a manager prioritises on.

    Deliberately NOT stored on the snapshot and not a scoring input — it is
    recomputed at read time from the live balance, per this codebase's rule that
    computed things are recomputed. For "where is the recoverable money now",
    the current balance is the right multiplicand anyway; the point-in-time value
    stays frozen in the snapshot's features blob for training pulls.

    Kept here, beside the rate, so the multiplication has exactly one definition.
    """
    return round(max(0.0, float(rate or 0.0)) * max(0.0, float(total_outstanding or 0.0)), 2)


# ── Factors ──────────────────────────────────────────────────────────────────
# Each returns (points, summary, evidence) or None to abstain. A factor with no
# evidence NEVER contributes zero silently — it says so, and its weight is
# withheld from the coverage denominator. Same contract as the repayment
# scorecard and fraud_service.py: absence of evidence is never a finding.

def _outstanding(f) -> float:
    return float(_f(f, "total_outstanding", 0.0) or 0.0)


def _ageing(f):
    """Penalty-only. Arriving in field collections at all is not a credit."""
    dpd = float(_f(f, "dpd", 0) or 0)
    depth = _clamp((dpd - _DPD_ENTRY) / (_DPD_FLOOR - _DPD_ENTRY), 0.0, 1.0)
    points = -round(_W_AGEING * depth, 4)
    return (
        points,
        f"{int(dpd)} days past due",
        {"dpd": int(dpd), "depth_of_range": round(depth, 3)},
    )


def _arrears_share(f):
    """Overdue as a share of the balance. Ability, not willingness."""
    outstanding = _outstanding(f)
    if outstanding <= 0:
        return None
    overdue = float(_f(f, "overdue_amount", 0.0) or 0.0)
    share = _clamp(overdue / outstanding, 0.0, 1.0)
    points = _linear(share, _ARREARS_BEST, _ARREARS_WORST, _W_ARREARS_SHARE)
    return (
        points,
        f"{share * 100:.0f}% of {_rupees(outstanding)} is overdue",
        {"overdue_amount": round(overdue, 2), "total_outstanding": round(outstanding, 2),
         "arrears_share": round(share, 3)},
    )


def _payment_recency(f):
    """How long since any money arrived. Abstains when nothing ever has."""
    days = _f(f, "days_since_last_payment", None)
    if days is None:
        return None
    days = float(days)
    points = _linear(days, _RECENCY_BEST, _RECENCY_WORST, _W_PAYMENT_RECENCY)
    return (
        points,
        f"Last payment {int(days)} days ago",
        {"days_since_last_payment": int(days)},
    )


def _payment_momentum(f):
    """Money actually received in the window. Upside-only — see _W_PAYMENT_MOMENTUM."""
    outstanding = _outstanding(f)
    if outstanding <= 0:
        return None
    paid = float(_f(f, "amount_paid_in_window", 0.0) or 0.0)
    last = float(_f(f, "last_payment_amount", 0.0) or 0.0)
    emi = float(_f(f, "emi_amount", 0.0) or 0.0)
    if paid <= 0:
        # NOTHING ARRIVED IN THE WINDOW — abstain, whatever the loan column says.
        #
        # 2026-08-24. This guard used to read `paid <= 0 and last <= 0`, so a loan
        # with no receipts at all still scored up to 0.4 x weight on the strength
        # of last_payment_amount alone. The 2026-08-24 dry run found 227 loans
        # (43% of the book) taking a positive momentum contribution having
        # received nothing; 77 were 90+ DPD, with a median 129 days since their
        # last payment and 21 of them last paying beyond the 180-day behaviour
        # window entirely.
        #
        # Four things said that was wrong, and none of them was a judgement call:
        # this factor's own docstring says "money actually received IN THE
        # WINDOW"; its abstention constant is named NO_RECENT_PAYMENT_ACTIVITY,
        # which is exactly the state of those loans; the summary it emitted read
        # "Rs 0 received recently; last payment covered 98% of an EMI" attached to
        # a POSITIVE score; and last_payment_amount is overwritten in place by
        # ingest with no history, so reading it unbounded here is the same leak
        # that build_features already refuses for last_payment_date.
        #
        # Nothing is lost by abstaining: when the borrower last paid is carried by
        # PAYMENT_RECENCY at a larger weight (0.14). This factor is about money
        # moving NOW, and silence is already priced by recency and ageing —
        # scoring it here as well would penalise one silence twice.
        return None

    window_share = _clamp(paid / outstanding, 0.0, 1.0)
    window_strength = _clamp(window_share / _MOMENTUM_FULL_SHARE, 0.0, 1.0)
    emi_cover = _clamp(last / emi, 0.0, 1.0) if emi > 0 else 0.0
    strength = 0.6 * window_strength + 0.4 * emi_cover
    points = round(_W_PAYMENT_MOMENTUM * strength, 4)
    return (
        points,
        f"{_rupees(paid)} received recently"
        + (f"; last payment covered {emi_cover * 100:.0f}% of an EMI" if emi > 0 else ""),
        {"amount_paid_in_window": round(paid, 2), "window_share": round(window_share, 4),
         "last_payment_amount": round(last, 2), "emi_cover": round(emi_cover, 3)},
    )


def _penal_drag(f):
    """Penalty-only. Charges inflate the ask without adding recoverable principal."""
    outstanding = _outstanding(f)
    if outstanding <= 0:
        return None
    penal = float(_f(f, "penal_charges", 0.0) or 0.0)
    if penal <= 0:
        return None
    share = _clamp(penal / outstanding, 0.0, 1.0)
    drag = _clamp(share / _PENAL_HEAVY, 0.0, 1.0)
    points = -round(_W_PENAL_DRAG * drag, 4)
    return (
        points,
        f"{_rupees(penal)} of penal charges — {share * 100:.1f}% of the balance",
        {"penal_charges": round(penal, 2), "penal_share": round(share, 4)},
    )


def _security(f):
    """Collateral. BUSINESS abstains — secured or unsecured depending on the
    facility, so treating it as either would be a guess. Matches
    repayment_scorecard._security exactly, by reusing the same sets."""
    loan_type = _f(f, "loan_type")
    if loan_type is None:
        return None
    if loan_type in _SECURED:
        return _W_SECURITY, "Secured against collateral", {"loan_type": str(loan_type)}
    if loan_type in _UNSECURED:
        return -_W_SECURITY, "Unsecured debt", {"loan_type": str(loan_type)}
    return None


def _legal_posture(f):
    """Signed both ways — enforcement is leverage, a settlement is a haircut."""
    legal = (_f(f, "legal_status", "NONE") or "NONE").upper()
    settlement = (_f(f, "settlement_status", "NONE") or "NONE").upper()
    if legal == "NONE" and settlement == "NONE":
        return None

    points = 0.0
    parts = []
    if legal != "NONE":
        points += _W_LEGAL_POSTURE * 0.5
        parts.append(f"enforcement under way ({legal})")
    if settlement in _SETTLEMENT_IN_PLAY:
        points -= _W_LEGAL_POSTURE * 0.8
        parts.append(f"settlement {settlement.lower()} — a haircut is the point")
    elif settlement != "NONE":
        parts.append(f"settlement {settlement.lower()}")

    points = round(_clamp(points, -_W_LEGAL_POSTURE, _W_LEGAL_POSTURE), 4)
    return (
        points,
        "Posture: " + ", ".join(parts),
        {"legal_status": legal, "settlement_status": settlement},
    )


def _npa_status(f):
    """Penalty-only, and abstains when not NPA — a performing classification is
    not evidence of recoverability, it is the absence of one adverse fact."""
    if not bool(_f(f, "npa_flag", False)):
        return None
    return (
        -_W_NPA_STATUS,
        "Classified NPA by the bank",
        {"npa_flag": True},
    )


_FACTORS = (
    (FACTOR_SECURITY, _W_SECURITY, _security, UNKNOWN_SECURITY),
    (FACTOR_AGEING, _W_AGEING, _ageing, None),
    (FACTOR_PAYMENT_RECENCY, _W_PAYMENT_RECENCY, _payment_recency, NO_PAYMENT_ON_RECORD),
    (FACTOR_PAYMENT_MOMENTUM, _W_PAYMENT_MOMENTUM, _payment_momentum, NO_RECENT_PAYMENT_ACTIVITY),
    (FACTOR_ARREARS_SHARE, _W_ARREARS_SHARE, _arrears_share, NO_BALANCE_ON_RECORD),
    (FACTOR_PENAL_DRAG, _W_PENAL_DRAG, _penal_drag, NO_PENAL_CHARGES),
    (FACTOR_LEGAL_POSTURE, _W_LEGAL_POSTURE, _legal_posture, NO_LEGAL_ACTION),
    (FACTOR_NPA_STATUS, _W_NPA_STATUS, _npa_status, NOT_NPA),
)


# ── Speed ────────────────────────────────────────────────────────────────────
def _speed_index(f) -> tuple[float, list[str]]:
    """How fast the recoverable money arrives, in [0, 1]. Never how much.

    Separated from the rate because the two genuinely differ in direction: a
    filed SARFAESI case RAISES eventual recovery and SLOWS it, and collapsing
    both into one number would net them out to roughly nothing — losing exactly
    the distinction the 30/60/90 split was asked for.
    """
    index = _SPEED_BASE
    reasons: list[str] = []

    days = _f(f, "days_since_last_payment", None)
    if days is not None and float(days) <= 30.0:
        index += _SPEED_RECENT_PAYMENT
        reasons.append("paid within the last 30 days")

    if float(_f(f, "amount_paid_in_window", 0.0) or 0.0) > 0:
        index += _SPEED_MOMENTUM
        reasons.append("money received in the behaviour window")

    if float(_f(f, "dpd", 0) or 0) >= _DPD_FLOOR:
        index += _SPEED_DEEP_DPD
        reasons.append(f"{int(float(_f(f, 'dpd', 0) or 0))} days past due")

    loan_type = _f(f, "loan_type")
    if loan_type is not None and loan_type in _SECURED:
        index += _SPEED_SECURED
        reasons.append("secured — asset realisation takes quarters")

    if (_f(f, "legal_status", "NONE") or "NONE").upper() != "NONE":
        index += _SPEED_LEGAL_FILED
        reasons.append("legal enforcement under way")

    if (_f(f, "settlement_status", "NONE") or "NONE").upper() == "ACCEPTED":
        index += _SPEED_SETTLEMENT_AGREED
        reasons.append("settlement agreed — money moves once terms are set")

    return _clamp(index, _SPEED_MIN, _SPEED_MAX), reasons


def _maturity_shares(speed: float) -> tuple[float, float]:
    """(f30, f60): the share of the eventual recovery landing by each horizon.

    f60 - f30 is a positive constant by construction — both lines share a slope —
    so rate_30 < rate_60 < rate_90 holds for every input and survives any future
    re-weighting. See the note on _SHARE_30_SLOW.
    """
    f30 = _SHARE_30_SLOW + (_SHARE_30_FAST - _SHARE_30_SLOW) * speed
    f60 = _SHARE_60_SLOW + (_SHARE_60_FAST - _SHARE_60_SLOW) * speed
    return f30, f60


def score(features) -> dict:
    """Score one LOAN's recovery. Deterministic: no RNG, no clock, no I/O.

    The grain is the loan, not the customer — total_outstanding, dpd, penal
    charges and collateral are all loan-level. Two loans of one borrower can and
    should carry different recovery labels; a customer-level rollup would erase
    exactly the difference this label exists to expose.
    """
    factors: list[dict] = []
    total = 0.0
    weight_that_spoke = 0.0

    for code, weight, fn, abstain_reason in _FACTORS:
        result = fn(features)
        if result is None:
            factors.append({"code": code, "abstained": True, "reason": abstain_reason})
            continue
        points, summary, evidence = result
        total += points
        weight_that_spoke += weight
        factors.append({
            "code": code,
            "direction": "UP" if points >= 0 else "DOWN",
            "points": points,
            "summary": summary,
            "evidence": evidence,
        })

    rate_90 = round(_clamp(BASE_RATE + total, MIN_RATE, MAX_RATE), 4)
    speed, speed_reasons = _speed_index(features)
    f30, f60 = _maturity_shares(speed)

    # Deliberately NOT renormalised over the weights that spoke, matching the
    # repayment scorecard: renormalising would make a loan with one factor
    # indistinguishable from one with eight. The caller is told what the
    # coverage was and decides whether to show a number at all.
    coverage = round(weight_that_spoke / TOTAL_WEIGHT, 3)

    return {
        "recovery_rate_30": round(rate_90 * f30, 4),
        "recovery_rate_60": round(rate_90 * f60, 4),
        "recovery_rate_90": rate_90,
        "recovery_potential": band_for(rate_90),
        "label_horizon_days": LABEL_HORIZON,
        "speed_index": round(speed, 3),
        "speed_reasons": speed_reasons,
        "evidence_coverage": coverage,
        "model_version": RECOVERY_SCORECARD_VERSION,
        "factors": factors,
    }
