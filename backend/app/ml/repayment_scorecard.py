# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-21 — New file. Customer.risk_score was not a score of anything. Both
#   writers computed the same thing:
#
#       base = dpd / 90 * 60 + (750 - cibil) / 750 * 40
#       (seed_data.py:458-464, and again in ingest_daily.py:96-107)
#
#   which is a relabelling of two columns we already store. It added no
#   information, and it fed collection_priority_score at 0.3 weight, which sets
#   Case.priority — so a re-badged DPD was quietly ordering field work.
#
#   Three defects came with it:
#     * seed_data.py:1327-1328 scored each customer against a `dpd_hint` that
#       was drawn, used, and thrown away — never the DPD of any of their loans.
#     * The two writers disagreed: the seed floored the score at 30 and could
#       never produce RiskCategory.LOW; ingest floored at 0 and could.
#     * random.uniform(-8, 8) of jitter, so the same borrower scored differently
#       on consecutive runs.
#
#   This module is a SCORECARD, not a model. Hand-chosen weights over
#   arithmetic — no training, no AUC, and it cannot have one until real repayment
#   outcomes have accumulated. It is labelled as such everywhere it surfaces, for
#   the same reason LLMResult.ai_generated exists in core/llm.py: a written-in
#   answer must never be passed off as a modelled one.
#
#   Pure functions in one module, like ml/eligibility.py, because the same
#   scoring belongs in the nightly task, the ingest script and the seed. Three
#   copies of one formula is precisely how the two above came to disagree.
# ───────────────────────────────────────────────────────────────────────────
"""How likely is this borrower to repay — and the reason for every point."""
from __future__ import annotations

from collections.abc import Mapping

from app.ml.eligibility import _SECURED, _UNSECURED

# Bumped whenever a weight, a band edge or a factor definition changes. Stamped
# onto every snapshot row, so a score can always be traced to the rules that
# produced it. A weight change is a model change; it must not be silent.
# 1.1.0 (2026-08-21): delinquency became penalty-only. The 90-DPD neutral point
# was rewarding borrowers for being merely forty days late on a book where every
# account is delinquent by definition. Bumping the version is not optional — it
# is stamped on every snapshot, and rows written under 1.0.0 answer a different
# question from rows written under 1.1.0. A model trained across both without
# filtering on model_version would be learning two scorecards at once.
SCORECARD_VERSION = "scorecard-1.1.0"

# ── Direction ────────────────────────────────────────────────────────────────
# The product concept is a LIKELIHOOD: higher is better. Customer.risk_score is
# the opposite — higher is worse — and both live consumers add it in that
# direction (seed_data.py:1399, ingest_daily.py:429). Writing a likelihood into
# that column would silently invert them: the borrowers most likely to pay would
# be promoted to CRITICAL, and nothing would raise. So the conversion happens
# here, once, and risk_score_from() is the only thing that ever writes it.
BASE_LIKELIHOOD = 50.0
MIN_LIKELIHOOD = 1.0
MAX_LIKELIHOOD = 99.0

# ── Factor codes ─────────────────────────────────────────────────────────────
FACTOR_DELINQUENCY_DEPTH = "DELINQUENCY_DEPTH"
FACTOR_PROMISE_HISTORY = "PROMISE_HISTORY"
FACTOR_PAYMENT_TRACK = "PAYMENT_TRACK"
FACTOR_CONTACTABILITY = "CONTACTABILITY"
FACTOR_ARREARS_BURDEN = "ARREARS_BURDEN"
FACTOR_BUREAU = "BUREAU"
FACTOR_CONDUCT = "CONDUCT"
FACTOR_LEGAL_POSTURE = "LEGAL_POSTURE"
FACTOR_SECURITY = "SECURITY"
FACTOR_LAST_PAYMENT_SIZE = "LAST_PAYMENT_SIZE"
FACTOR_SEGMENT = "SEGMENT"

# ── Abstention reasons ───────────────────────────────────────────────────────
NO_PTP_HISTORY = "NO_RESOLVED_PROMISES"
NO_VISIT_HISTORY = "TOO_FEW_VISITS"
NO_CASE_HISTORY = "NO_CASE_TARGET"
NO_BUREAU_SCORE = "NO_BUREAU_SCORE"
NO_EMI_ON_RECORD = "NO_EMI_ON_RECORD"
NO_ADVERSE_CONDUCT = "NO_ADVERSE_CONDUCT_RECORDED"
NO_LEGAL_ACTION = "NO_LEGAL_ACTION"
NO_PAYMENT_ON_RECORD = "NO_PAYMENT_ON_RECORD"
UNKNOWN_SECURITY = "SECURITY_UNDETERMINED"
UNKNOWN_SEGMENT = "SEGMENT_UNKNOWN"

# ── Weights ──────────────────────────────────────────────────────────────────
# Each is the maximum magnitude that factor can move the likelihood, in points.
# They are module constants and deliberately NOT settings: an env var could
# change what the score means without changing SCORECARD_VERSION, and then the
# rows already written would be stamped with rules that no longer exist.

# Days past due. The largest single weight, and still under a quarter of the
# total — because being the whole score is exactly the failure of the formula
# this replaces.
_W_DELINQUENCY = 30.0

# Kept promises. Revealed behaviour at the doorstep, and the nearest thing in
# the schema to the outcome itself: at identical DPD, a borrower who honoured
# two promises is a different borrower from one who broke two.
#
# COLD START — measured against the live database on 2026-08-21: this factor
# ABSTAINS on 486 of 525 loans (93%). That is the data, not the factor. There
# are 441 PTPs, but 232 are still ACTIVE (133 of those with a committed_date in
# the future), leaving only 198 resolved inside the behaviour window and only 39
# loans carrying the two that REPAYMENT_MIN_PTPS_FOR_HISTORY requires.
#
# The weight stays at 20 deliberately. A high abstention rate today is evidence
# that little promise history has accumulated yet — NOT evidence that promise
# history predicts poorly. Cutting the weight now would bake a property of a
# young demo dataset into the rules, and it would have to be undone precisely
# when the factor started earning its keep. The rate falls on its own as
# promises come due and resolve.
#
# Two consequences follow, and both are intended rather than tolerated:
#   * evidence_coverage is genuinely lower while the book is young. It is NOT
#     renormalised over the factors that spoke — see score() — so a thin
#     borrower reads as thin instead of as confidently average.
#   * in practice today the score is carried by delinquency, contactability,
#     bureau and last-payment size. Anyone describing promise history as a
#     pillar of the current number would be describing the intent, not the
#     behaviour. Re-measure before making that claim.
_W_PROMISE = 20.0

# Money actually received. A fact rather than a claim. Ranked below promises
# only because at 31+ DPD most borrowers have no payment at all, so it abstains
# more often than it speaks.
_W_PAYMENT = 15.0

# Whether anyone can find them. You cannot collect from someone who is never in.
_W_CONTACT = 12.0

# Months of arrears against their own EMI. Ability, not willingness — the two
# are different and the old formula modelled neither.
_W_ARREARS = 10.0

# Bureau score. Deliberately held to 8 rather than the ~20 a production
# scorecard would give it, because in the seeded database cibil_score is
# random.randint(300, 680), independent of everything (seed_data.py:1326).
# RAISE THIS TO ~20 ONCE THE BUREAU FEED IS REAL — and bump SCORECARD_VERSION.
_W_BUREAU = 8.0

# Hostility, fraud flags, refusals. Penalty-only: the absence of hostility is
# not evidence of willingness, so there is no upside branch.
_W_CONDUCT = 8.0

# Legal and settlement posture. Penalty-only, and half-weighted past 90 DPD
# because seed_data.py:1385-1397 only ever populates these for dpd >= 90 —
# full weight there would double-count delinquency depth.
_W_LEGAL = 6.0

# Collateral. Reuses the sets from ml/eligibility.py rather than re-deriving
# them; copy-pasted definitions are how this codebase's worst bugs happened.
_W_SECURITY = 6.0

# Size of the last payment: a full EMI, or a token amount to buy time?
# Low weight because it is a single observation.
_W_LAST_PAYMENT = 5.0

# Income regularity by KYC segment. The lowest weight of any factor that
# speaks, and the spread is deliberately narrow: this is the factor most likely
# to encode something a fair-lending review would object to. It measures how
# REGULAR an income is, never how large. First candidate for removal if the
# score is ever challenged.
_W_SEGMENT = 4.0

TOTAL_WEIGHT = (
    _W_DELINQUENCY + _W_PROMISE + _W_PAYMENT + _W_CONTACT + _W_ARREARS
    + _W_BUREAU + _W_CONDUCT + _W_LEGAL + _W_SECURITY + _W_LAST_PAYMENT
    + _W_SEGMENT
)  # 124.0 — the coverage denominator

# ── Thresholds that are business-tunable live in settings, not here ──────────
# (REPAYMENT_MIN_PTPS_FOR_HISTORY, REPAYMENT_MIN_VISITS_FOR_CONTACT). They are
# passed in so this module stays pure and importable without config.
DEFAULT_MIN_PTPS = 2
DEFAULT_MIN_VISITS = 2

# Reference points, each with a reason rather than a round number.
# Still the RBI NPA line, and still used to half-weight legal posture past it.
# It is NO LONGER the delinquency neutral point — see _delinquency_depth.
_DPD_NEUTRAL = 90.0
# The shallowest account that reaches field collections. Scores 0, not a bonus:
# arriving here at all is not evidence of willingness to pay.
_DPD_ENTRY = 30.0
_DPD_FLOOR = 180.0      # beyond this, further ageing tells us little new
_ARREARS_FLOOR = 6.0    # six months of missed EMIs — a structural position
_CIBIL_MIN, _CIBIL_MAX = 300.0, 900.0   # the CIBIL scale

# Income regularity, not income level. RETIRED scores mildly positive because a
# pension is regular; the self-employed are mildly negative because the income
# is lumpy, not because it is smaller.
_SEGMENT_POINTS = {
    "SALARIED": 1.00,
    "RETIRED": 0.25,
    "SELF_EMPLOYED": -0.25,
    "BUSINESS_OWNER": -0.25,
    "HOMEMAKER": -0.50,
    "STUDENT": -0.50,
}

# ── Bands ────────────────────────────────────────────────────────────────────
# RiskCategory edges. There were two copies of these and they disagreed:
# ingest_daily.py:98-106 had four bands, seed_data.py:460-463 had three and
# floored the score at 30, so RiskCategory.LOW was unreachable on a seeded
# database and RiskBadge's LOW branch (ui/Badge.tsx:70) was dead code.
# Ingest's version wins because it is the complete one. Defined once, here.
RISK_BAND_EDGES = ((80.0, "CRITICAL"), (60.0, "HIGH"), (35.0, "MEDIUM"))
RISK_BAND_FLOOR = "LOW"

# The repayment bands are the exact mirror, so that band and risk_category can
# never drift: 100 - 80 = 20, 100 - 60 = 40, 100 - 35 = 65.
BAND_LIKELY = "LIKELY"
BAND_UNCERTAIN = "UNCERTAIN"
BAND_UNLIKELY = "UNLIKELY"
BAND_VERY_UNLIKELY = "VERY_UNLIKELY"

LIKELIHOOD_BAND_EDGES = (
    (65.0, BAND_LIKELY),
    (40.0, BAND_UNCERTAIN),
    (20.0, BAND_UNLIKELY),
)
LIKELIHOOD_BAND_FLOOR = BAND_VERY_UNLIKELY


def risk_score_from(likelihood: float) -> float:
    """The ONLY conversion to the legacy risk direction. Higher = worse."""
    return round(100.0 - likelihood, 1)


def risk_category_for(risk_score: float) -> str:
    for edge, name in RISK_BAND_EDGES:
        if risk_score >= edge:
            return name
    return RISK_BAND_FLOOR


def band_for(likelihood: float) -> str:
    for edge, name in LIKELIHOOD_BAND_EDGES:
        if likelihood > edge:
            return name
    return LIKELIHOOD_BAND_FLOOR


# ── Helpers ──────────────────────────────────────────────────────────────────
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _linear(value: float, best: float, worst: float, weight: float) -> float:
    """Map value onto [+weight, -weight], linearly, clamped at both ends."""
    if best == worst:
        return 0.0
    frac = _clamp((value - best) / (worst - best), 0.0, 1.0)
    return round(weight * (1.0 - 2.0 * frac), 2)


def _rupees(amount: float) -> str:
    return f"₹{amount:,.0f}"


def _f(features, name, default=None):
    """Read a feature by name.

    Accepts a mapping OR any object with attributes, so tests can pass a
    SimpleNamespace, the service a dataclass, and the analysis script a plain
    dict. Silently returning the default for a dict would make every factor
    abstain at once and produce a flat score of 50 for the whole book — which
    is exactly the failure this scorecard exists to end.
    """
    if isinstance(features, Mapping):
        value = features.get(name, default)
    else:
        value = getattr(features, name, default)
    return default if value is None else value


# ── Factors ──────────────────────────────────────────────────────────────────
# Each returns (points, summary, evidence) or None to abstain. A factor with no
# evidence NEVER contributes zero silently — it says so, and its weight is
# withheld from the coverage denominator. Same contract as fraud_service.py:
# absence of evidence is never a finding.

def _delinquency_depth(f):
    """Penalty only. Delinquency can never earn a borrower points.

    v1.0.0 mapped DPD onto [+30, -30] with a neutral point at 90 (the RBI NPA
    line), which is right for a general lending book and wrong for this one.
    Every account here is already delinquent: the book's median is 72 DPD and
    68% of it sits below 90, so most borrowers were being REWARDED for being
    only forty-odd days late. Measured on the live book, that put 24.8% of
    customers in LOW, and 55% of that band depended on the positive term.

    On a collections book the question is never "is this borrower delinquent"
    — they all are — but "how deep". So the scale runs from 0 at the shallowest
    account that reaches field collections down to the full penalty at
    _DPD_FLOOR. Willingness to pay has to be earned from behaviour instead.
    """
    dpd = _f(f, "dpd")
    if dpd is None:
        return None
    frac = _clamp((float(dpd) - _DPD_ENTRY) / (_DPD_FLOOR - _DPD_ENTRY), 0.0, 1.0)
    points = round(-_W_DELINQUENCY * frac, 2)
    return points, f"{int(dpd)} days past due", {"dpd": int(dpd)}


def _promise_history(f, min_ptps: int = DEFAULT_MIN_PTPS):
    resolved = int(_f(f, "ptps_resolved", 0) or 0)
    honored = int(_f(f, "ptps_honored", 0) or 0)
    if resolved < min_ptps:
        # Zero kept promises out of zero promises is not a bad payer; it is an
        # unknown one. Scoring it as bad makes every new borrower a defaulter.
        return None
    keep_rate = honored / resolved
    points = round(_W_PROMISE * (2.0 * keep_rate - 1.0), 2)
    return (
        points,
        f"Kept {honored} of {resolved} promises to pay",
        {"ptps_honored": honored, "ptps_resolved": resolved,
         "keep_rate": round(keep_rate, 3)},
    )


def _payment_track(f):
    target = _f(f, "case_target_amount")
    if not target or float(target) <= 0:
        return None
    paid = float(_f(f, "amount_paid_in_window", 0.0) or 0.0)
    ratio = _clamp(paid / float(target), 0.0, 1.0)
    points = round(_W_PAYMENT * (2.0 * ratio - 1.0), 2)
    return (
        points,
        f"Paid {_rupees(paid)} of a {_rupees(float(target))} target",
        {"amount_paid": round(paid, 2), "target_amount": round(float(target), 2),
         "ratio": round(ratio, 3)},
    )


def _contactability(f, min_visits: int = DEFAULT_MIN_VISITS):
    visits = int(_f(f, "visits", 0) or 0)
    met = int(_f(f, "visits_met", 0) or 0)
    if visits < min_visits:
        return None
    rate = met / visits
    points = round(_W_CONTACT * (2.0 * rate - 1.0), 2)
    return (
        points,
        f"Found at home on {met} of {visits} visits",
        {"visits": visits, "visits_met": met, "met_rate": round(rate, 3)},
    )


def _arrears_burden(f):
    emi = _f(f, "emi_amount")
    overdue = _f(f, "overdue_amount")
    if not emi or float(emi) <= 0 or overdue is None:
        return None
    months = float(overdue) / float(emi)
    points = _linear(months, 0.0, _ARREARS_FLOOR, _W_ARREARS)
    return (
        points,
        f"{months:.1f} months of arrears against a {_rupees(float(emi))} EMI",
        {"overdue_amount": round(float(overdue), 2),
         "emi_amount": round(float(emi), 2),
         "months_of_arrears": round(months, 2)},
    )


def _bureau(f):
    cibil = _f(f, "cibil_score")
    if cibil is None:
        return None
    points = _linear(float(cibil), _CIBIL_MAX, _CIBIL_MIN, _W_BUREAU)
    return points, f"Bureau score {int(cibil)}", {"cibil_score": int(cibil)}


def _conduct(f):
    """Penalty only. There is no upside branch, by design."""
    hostile = bool(_f(f, "is_hostile", False))
    fraud = bool(_f(f, "fraud_flag", False))
    adverse = int(_f(f, "adverse_visit_outcomes", 0) or 0)
    if not hostile and not fraud and adverse <= 0:
        return None
    penalty = 0.0
    parts = []
    if hostile:
        penalty += 3.0
        parts.append("recorded as hostile")
    if fraud:
        penalty += 3.0
        parts.append("carries a bank fraud flag")
    if adverse > 0:
        penalty += min(2.0, adverse * 1.0)
        parts.append(f"{adverse} refusal or dispute outcome(s)")
    points = -round(min(penalty, _W_CONDUCT), 2)
    return (
        points,
        "Conduct: " + ", ".join(parts),
        {"is_hostile": hostile, "fraud_flag": fraud,
         "adverse_visit_outcomes": adverse},
    )


def _legal_posture(f):
    """Penalty only, and half-weighted past 90 DPD to avoid double-counting."""
    legal = (_f(f, "legal_status", "NONE") or "NONE").upper()
    settlement = (_f(f, "settlement_status", "NONE") or "NONE").upper()
    if legal == "NONE" and settlement == "NONE":
        return None
    dpd = float(_f(f, "dpd", 0) or 0)
    cap = _W_LEGAL / 2.0 if dpd >= _DPD_NEUTRAL else _W_LEGAL
    penalty = 0.0
    parts = []
    if legal != "NONE":
        penalty += cap * 0.7
        parts.append(f"legal status {legal}")
    if settlement != "NONE":
        penalty += cap * 0.3
        parts.append(f"settlement status {settlement}")
    points = -round(min(penalty, cap), 2)
    return (
        points,
        "Posture: " + ", ".join(parts),
        {"legal_status": legal, "settlement_status": settlement,
         "half_weighted_past_npa": dpd >= _DPD_NEUTRAL},
    )


def _security(f):
    loan_type = _f(f, "loan_type")
    if loan_type is None:
        return None
    if loan_type in _SECURED:
        return _W_SECURITY, "Secured against collateral", {"loan_type": str(loan_type)}
    if loan_type in _UNSECURED:
        return -_W_SECURITY, "Unsecured debt", {"loan_type": str(loan_type)}
    # BUSINESS is in neither set in ml/eligibility.py — secured or unsecured
    # depending on the facility, so treating it as either would be a guess.
    return None


def _last_payment_size(f):
    emi = _f(f, "emi_amount")
    last = float(_f(f, "last_payment_amount", 0.0) or 0.0)
    if not emi or float(emi) <= 0 or last <= 0:
        return None
    ratio = _clamp(last / float(emi), 0.0, 1.0)
    points = round(_W_LAST_PAYMENT * (2.0 * ratio - 1.0), 2)
    return (
        points,
        f"Last payment {_rupees(last)} against a {_rupees(float(emi))} EMI",
        {"last_payment_amount": round(last, 2), "emi_ratio": round(ratio, 3)},
    )


def _segment(f):
    segment = (_f(f, "customer_segment") or "").upper()
    if segment not in _SEGMENT_POINTS:
        return None
    points = round(_W_SEGMENT * _SEGMENT_POINTS[segment], 2)
    return points, f"Income segment {segment}", {"customer_segment": segment}


_FACTORS = (
    (FACTOR_DELINQUENCY_DEPTH, _W_DELINQUENCY, _delinquency_depth, None),
    (FACTOR_PROMISE_HISTORY, _W_PROMISE, _promise_history, NO_PTP_HISTORY),
    (FACTOR_PAYMENT_TRACK, _W_PAYMENT, _payment_track, NO_CASE_HISTORY),
    (FACTOR_CONTACTABILITY, _W_CONTACT, _contactability, NO_VISIT_HISTORY),
    (FACTOR_ARREARS_BURDEN, _W_ARREARS, _arrears_burden, NO_EMI_ON_RECORD),
    (FACTOR_BUREAU, _W_BUREAU, _bureau, NO_BUREAU_SCORE),
    (FACTOR_CONDUCT, _W_CONDUCT, _conduct, NO_ADVERSE_CONDUCT),
    (FACTOR_LEGAL_POSTURE, _W_LEGAL, _legal_posture, NO_LEGAL_ACTION),
    (FACTOR_SECURITY, _W_SECURITY, _security, UNKNOWN_SECURITY),
    (FACTOR_LAST_PAYMENT_SIZE, _W_LAST_PAYMENT, _last_payment_size, NO_PAYMENT_ON_RECORD),
    (FACTOR_SEGMENT, _W_SEGMENT, _segment, UNKNOWN_SEGMENT),
)


def score(features, *, min_ptps: int = DEFAULT_MIN_PTPS,
          min_visits: int = DEFAULT_MIN_VISITS) -> dict:
    """Score one LOAN. Deterministic: no RNG, no clock, no I/O.

    The grain is the loan, not the customer — dpd, overdue_amount and loan_type
    are all loan-level, and scoring per-customer is exactly what let
    seed_data.py:1327 score a customer against a DPD belonging to no loan of
    theirs. The customer rollup happens in the service, on the worst loan.
    """
    factors: list[dict] = []
    total = 0.0
    weight_that_spoke = 0.0

    for code, weight, fn, abstain_reason in _FACTORS:
        if code == FACTOR_PROMISE_HISTORY:
            result = fn(features, min_ptps)
        elif code == FACTOR_CONTACTABILITY:
            result = fn(features, min_visits)
        else:
            result = fn(features)

        if result is None:
            factors.append({"code": code, "abstained": True,
                            "reason": abstain_reason})
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

    likelihood = round(
        _clamp(BASE_LIKELIHOOD + total, MIN_LIKELIHOOD, MAX_LIKELIHOOD), 1
    )
    risk = risk_score_from(likelihood)

    # Deliberately NOT renormalised over the weights that spoke. Renormalising
    # would make a borrower with one factor indistinguishable from one with
    # eleven — a confident-looking number with nothing behind it. The caller is
    # told what the coverage was and decides whether to show a number at all.
    coverage = round(weight_that_spoke / TOTAL_WEIGHT, 3)

    return {
        "likelihood": likelihood,
        "risk_score": risk,
        "band": band_for(likelihood),
        "risk_category": risk_category_for(risk),
        "evidence_coverage": coverage,
        "model_version": SCORECARD_VERSION,
        "factors": factors,
    }
