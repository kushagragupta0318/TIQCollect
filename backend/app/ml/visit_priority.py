# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. "Which cases should agents visit first?"
#
#   WHAT THIS REPLACES. Case.priority, computed once at case creation as
#   min(100, dpd/90*40 + outstanding_principal/500000*30) and never recomputed
#   (seed_data.py:1577; repayment_service.py:732-745 explicitly declines to
#   reprice it). Because it is frozen at creation it cannot reflect effort
#   already spent, nor a recovery estimate that did not exist yet.
#
#   WHAT IT IS NOT. An earlier attempt ordered cases by DPD band alone. It is
#   archived on branch archive/dpd-allocation and was removed on request. Its
#   failure defines this design: it ignored how much money was at stake, and
#   under a tight visit budget it allocated 0% of visits to NPA.
#
#   THE COLLAPSE RISK, MEASURED BEFORE BUILDING. rate_90's largest factor is a
#   DPD ramp (_W_AGEING = 0.18), so the value component could re-import DPD and
#   collapse this back into a DPD sort. Measured read-only over 523 scored
#   loans on the live book:
#
#       corr(DPD, expected_recoverable)          -0.165
#       rank corr(value order, DPD order)        -0.163
#       top-20 overlap of the two orderings      0 of 20
#       corr(DPD, total_outstanding)             +0.094
#       within-band spread of expected_recoverable  317x - 2,703x
#
#   Ranking by recoverable value produces an almost entirely different order
#   from ranking by DPD, because it is driven by loan size and loan size is
#   independent of lateness. The collapse mode is structurally absent, not
#   merely hoped against.
#
#   THE UNCOMFORTABLE COROLLARY. Because value and urgency are near-orthogonal,
#   THE WEIGHTS DECIDE THE OUTCOME. There is no "the components agree anyway"
#   safety net. And since corr(DPD, rate_90) = -0.546, a very old loan draws low
#   urgency AND a low rate — only sheer size rescues it. Large, very-late loans
#   are where the three components disagree most, and they are the first thing a
#   manager will ask about.
#
#   A SCORECARD, NOT A MODEL. Hand-chosen weights over facts the schema already
#   holds. Nothing here was learned from data, so nothing here may be described
#   as ML. It consumes both existing scorecards' outputs, which is exactly why
#   it cannot live inside either: repayment_service.py:102 _FORBIDDEN_FEATURE_KEYS
#   bars recovery_rate_*, priority and expected_recoverable_amount from being fed
#   back in as features, enforced by _raise_if_leaked. This is a consumer, one
#   layer up.
# ─────────────────────────────────────────────────────────────────────────────
"""Rank one case for the field: recoverable value, urgency, effort spent.

Pure. No database, no clock, no RNG — the caller supplies every fact, including
how many days remain on the soonest live promise, so the same inputs always
produce the same score. `_score_case` in case_service.py reads
`datetime.now().hour` and is therefore not reproducible; this deliberately is.
"""
from __future__ import annotations

# Reused rather than redefined. _DPD_NEUTRAL is THE RBI NPA line and already
# exists as a named constant; hardcoding 90 again would let the two drift.
from app.ml.recovery_scorecard import expected_recoverable_amount
from app.ml.repayment_scorecard import _DPD_NEUTRAL, _clamp, _f, _rupees

SCORE_VERSION = "visit-priority-1.1.0"

# ── Component codes ─────────────────────────────────────────────────────────
COMPONENT_VALUE = "RECOVERABLE_VALUE"
COMPONENT_URGENCY = "URGENCY"
COMPONENT_EFFORT = "EFFORT"

# Fixed order, always all three. A manager comparing two cases must see the same
# rows in the same places; a component that vanishes when it has no data turns
# "why is this one higher" into a hunt.
COMPONENT_ORDER = (COMPONENT_VALUE, COMPONENT_URGENCY, COMPONENT_EFFORT)

# ── Weights ─────────────────────────────────────────────────────────────────
# Value leads because it is the thing the current ordering lacks entirely, and
# because the brief leads with it. Urgency is second, and deliberately smaller:
# on its own it is what abandoned NPA last time. Effort normally subtracts;
# a meaningful PTP due today or tomorrow can earn back a bounded amount.
MAX_VALUE_POINTS = 50.0
MAX_URGENCY_POINTS = 35.0
MAX_EFFORT_PENALTY = -25.0

# A live promise can add at most 15 points to effort, making 100 reachable only
# for a large, urgent case with a meaningful near-term promise.
PTP_PROTECTION_BONUS = 15.0
PTP_PROTECTION_DAYS = 1

# Recovery estimates are normally refreshed nightly. Older estimates retain
# some signal, but their value points are discounted instead of being trusted
# as though last week's borrower behaviour were still current.
RECOVERY_FRESH_DAYS = 2
RECOVERY_STALE_DAYS = 7
RECOVERY_STALE_FACTOR = 0.75
RECOVERY_EXPIRED_FACTOR = 0.50

# ── Value ladder ────────────────────────────────────────────────────────────
# Thresholds roughly DOUBLE. The measured spread of expected_recoverable inside
# a single DPD band is 317x-2,703x, so a linear ladder would drop almost every
# case into one bucket and the component would stop ranking anything.
#
# Reads as: "each time the recoverable amount roughly doubles, the case gains
# about 8 points."
_VALUE_LADDER = (
    (1_000_000.0, 50.0),
    (500_000.0, 43.0),
    (250_000.0, 35.0),
    (100_000.0, 26.0),
    (50_000.0, 16.0),
    (25_000.0, 8.0),
)
_VALUE_FLOOR = 0.0

# ── Urgency ladder ──────────────────────────────────────────────────────────
# PEAKS AT 76-90 DPD AND DECLINES SMOOTHLY PAST 90, on purpose.
#
# 90 days is the RBI line at which a loan is classified non-performing. The
# fortnight before it is the last chance to prevent that classification, so it is
# where a collections team pushes hardest. Once the line is crossed the cliff is
# behind us and the classification damage is done, so urgency genuinely falls.
#
# THIS DOES NOT ABANDON NPA. A large NPA case still outranks a small pre-NPA one
# through the value component, which carries more weight than urgency. The
# post-NPA decline is continuous so crossing the line by one day cannot reshuffle
# the day's queue by fifteen points.
_URGENCY_FLOOR = 8.0        # under 30 DPD barely reaches field collections

# ── Effort penalty ──────────────────────────────────────────────────────────
# Diminishing returns. Measured on the benchmark book: a first visit returned
# ~Rs 6,667 against ~Rs 2,872 by the third, so breadth beat depth. 39.5% of open
# cases sit at 3+ visits, so this differentiates rather than decorating.
_EFFORT_PENALTIES = {0: 0.0, 1: -6.0, 2: -14.0}
_EFFORT_EXHAUSTED = -25.0

# An imminent PTP is a follow-up signal only when its amount is meaningful and
# the customer's previous promise behaviour supports it. It adds to the effort
# component; it never waives the penalty for prior visits.


# ── Bands ───────────────────────────────────────────────────────────────────
# A raw "11 / 100" tells a manager nothing on its own, and the panel that shows
# it was headed "Why this case is visited FIRST" — which is false for all but a
# handful of cases and actively misleading on a case ranked near the bottom. The
# band gives the number a meaning without claiming a position the score does not
# support.
BAND_HIGH = "HIGH"
BAND_MEDIUM = "MEDIUM"
BAND_LOW = "LOW"

# Edges chosen off the measured distribution on the live book (545 open cases,
# spread across 0-99, peak in 40-49): roughly the top sixth, the middle, and the
# bottom third.
PRIORITY_BAND_EDGES = ((60.0, BAND_HIGH), (35.0, BAND_MEDIUM))
PRIORITY_BAND_FLOOR = BAND_LOW


def band_for(score_value: float) -> str:
    """HIGH / MEDIUM / LOW for a visit-priority score."""
    for edge, name in PRIORITY_BAND_EDGES:
        if score_value >= edge:
            return name
    return PRIORITY_BAND_FLOOR


def _ladder(value: float, ladder: tuple, floor: float) -> float:
    """First threshold the value clears wins. Ladders are descending."""
    for edge, points in ladder:
        if value > edge:
            return points
    return floor


def _recovery_freshness(age_days) -> tuple[float, str, bool]:
    """(value factor, summary suffix, needs_rescore) for a snapshot's age."""
    if age_days is None:
        return 1.0, "", False
    age = max(0.0, float(age_days))
    if age <= RECOVERY_FRESH_DAYS:
        return 1.0, "", False
    if age <= RECOVERY_STALE_DAYS:
        return (RECOVERY_STALE_FACTOR,
                f"; estimate is {age:.0f} days old and discounted", True)
    return (RECOVERY_EXPIRED_FACTOR,
            f"; estimate is {age:.0f} days old and needs rescore", True)


def _urgency_points(dpd: float) -> float:
    """Urgency with a continuous decline after the 90-day NPA line."""
    if dpd <= 30.0:
        return _URGENCY_FLOOR
    if dpd <= 45.0:
        return 12.0
    if dpd <= 60.0:
        return 22.0
    if dpd <= 75.0:
        return 31.0
    if dpd <= _DPD_NEUTRAL:
        return MAX_URGENCY_POINTS
    if dpd <= 120.0:
        return round(MAX_URGENCY_POINTS - (dpd - _DPD_NEUTRAL) * 0.5, 1)
    if dpd <= 180.0:
        return round(20.0 - (dpd - 120.0) / 6.0, 1)
    return 10.0


def _effort_base(visits: int, allowed: int) -> tuple[float, str]:
    exhausted = allowed > 0 and visits >= allowed
    if visits >= 3 or exhausted:
        summary = (f"{visits} visits already, against {allowed} allowed - "
                   "further visits have returned little"
                   if exhausted else
                   f"{visits} visits already - further visits have returned little")
        return _EFFORT_EXHAUSTED, summary
    points = _EFFORT_PENALTIES.get(visits, _EFFORT_EXHAUSTED)
    return points, ("Not visited yet" if visits == 0
                    else f"{visits} visit{'s' if visits != 1 else ''} so far")


def _ptp_follow_up(features, days: float) -> tuple[float, str, dict]:
    """Bounded follow-up lift for a meaningful, imminent promise to pay."""
    committed = max(0.0, float(_f(features, "ptp_committed_amount", 0.0) or 0.0))
    remaining = max(1.0, float(_f(features, "remaining_target", 0.0) or 0.0))
    resolved = max(0, int(_f(features, "ptp_resolved_count", 0) or 0))
    kept = min(resolved, max(0, int(_f(features, "ptp_kept_count", 0) or 0)))
    coverage = min(1.0, committed / remaining)

    timing_points = 8.0 if days < 1.0 else 5.0
    amount_points = 5.0 if coverage >= 0.75 else 3.0 if coverage >= 0.40 else 1.0
    if not resolved:
        reliability_points, history = 0.0, "no prior resolved promises"
    else:
        reliability = kept / resolved
        if reliability >= 0.75:
            reliability_points, history = 2.0, f"kept {kept}/{resolved} prior promises"
        elif reliability >= 0.50:
            reliability_points, history = 1.0, f"kept {kept}/{resolved} prior promises"
        else:
            reliability_points, history = -5.0, f"kept only {kept}/{resolved} prior promises"

    points = min(PTP_PROTECTION_BONUS, timing_points + amount_points + reliability_points)
    when = "today" if days < 1.0 else "tomorrow"
    summary = f"PTP due {when}, covers {coverage:.0%} of the remaining target; {history}"
    return points, summary, {
        "ptp_due_in_days": days,
        "ptp_committed_amount": round(committed, 2),
        "remaining_target": round(remaining, 2),
        "ptp_coverage": round(coverage, 4),
        "ptp_resolved_count": resolved,
        "ptp_kept_count": kept,
        "ptp_follow_up_points": points,
    }


# ── Components ──────────────────────────────────────────────────────────────
def _value(features) -> dict:
    """How much we expect to recover, as POINTS — never as rupees on the wire.

    The rupee figure is computed here and deliberately not returned.
    tests/test_manager_recovery_surface.py forbids a rupee estimate on a case
    payload, for a reason that still holds: "a rupee figure on a case row is read
    as 'collect this', and rate_90 x total outstanding is not that." The summary
    names the BAND, the evidence carries the inputs, and the amount stays here.
    """
    rate_90 = _f(features, "recovery_rate_90")
    outstanding = float(_f(features, "total_outstanding", 0.0) or 0.0)

    if rate_90 is None or outstanding <= 0:
        # Abstain to the floor rather than guess. A case whose loan has not been
        # scored yet has no claim on the day, but it must not be pushed BELOW a
        # scored case that genuinely recovers nothing either.
        #
        # `abstained` is load-bearing, not decoration: without it _reason() calls
        # an unmeasured case "little or no recoverable balance", which is a
        # statement about the loan when the truth is a statement about our
        # information. Those are not the same claim.
        return {
            "code": COMPONENT_VALUE,
            "points": _VALUE_FLOOR,
            "abstained": True,
            "summary": "No recovery estimate on this loan yet",
            "evidence": {"rate_90": rate_90, "total_outstanding": outstanding},
        }

    # The sanctioned multiplication, from the scorecard that owns it. rate_90 is
    # a rate on TOTAL OUTSTANDING; multiplying it by arrears would be
    # arithmetically wrong and is grepped against in the manager test suite.
    amount = expected_recoverable_amount(rate_90, outstanding)
    rate_age_days = _f(features, "recovery_rate_age_days")
    freshness_factor, freshness_suffix, needs_rescore = _recovery_freshness(rate_age_days)
    points = round(_ladder(amount, _VALUE_LADDER, _VALUE_FLOOR) * freshness_factor, 1)
    return {
        "code": COMPONENT_VALUE,
        "points": points,
        "abstained": False,
        "summary": f"{_rupees(amount)} recoverable over 90 days{freshness_suffix}",
        "evidence": {
            "rate_90": round(float(rate_90), 4),
            "total_outstanding": round(outstanding, 2),
            "rate_age_days": rate_age_days,
            "freshness_factor": freshness_factor,
            "needs_rescore": needs_rescore,
        },
    }


def _urgency(features) -> dict:
    """How close this loan is to the 90-day NPA line, and which side of it."""
    dpd = _f(features, "dpd")
    if dpd is None:
        # `abstained` for the same reason _value carries it: without it the
        # reason line has to guess from the points, and the points cannot tell
        # "no data" apart from "not very late".
        return {
            "code": COMPONENT_URGENCY,
            "points": _URGENCY_FLOOR,
            "abstained": True,
            "summary": "Days-past-due not recorded on this loan",
            "evidence": {"dpd": None},
        }
    dpd = float(dpd)
    points = _urgency_points(dpd)

    if dpd > _DPD_NEUTRAL:
        summary = (f"{dpd:.0f} days late — past the {_DPD_NEUTRAL:.0f}-day NPA "
                   f"line, worked on value rather than urgency")
    elif dpd > 75.0:
        summary = (f"{dpd:.0f} days late — last fortnight before the "
                   f"{_DPD_NEUTRAL:.0f}-day NPA line")
    else:
        summary = f"{dpd:.0f} days late"
    return {
        "code": COMPONENT_URGENCY,
        "points": points,
        "abstained": False,
        "summary": summary,
        "evidence": {"dpd": dpd, "npa_line": _DPD_NEUTRAL},
    }


def _effort(features) -> dict:
    """Effort already spent. Subtracts — unless a promise is about to fall due.

    `ptp_due_in_days` is supplied by the caller rather than derived from a clock,
    so this stays reproducible. None means no active promise.
    """
    visits = int(_f(features, "visit_count", 0) or 0)
    allowed = int(_f(features, "max_visits_allowed", 0) or 0)
    ptp_due_in_days = _f(features, "ptp_due_in_days")
    base_points, base_summary = _effort_base(visits, allowed)

    protected = (ptp_due_in_days is not None
                 and 0 <= float(ptp_due_in_days) <= PTP_PROTECTION_DAYS)
    if protected:
        days = float(ptp_due_in_days)
        ptp_points, ptp_summary, ptp_evidence = _ptp_follow_up(features, days)
        return {
            "code": COMPONENT_EFFORT,
            "points": round(base_points + ptp_points, 1),
            "abstained": False,
            "summary": f"{base_summary}; {ptp_summary}",
            "evidence": {
                "visit_count": visits,
                "max_visits_allowed": allowed,
                "base_effort_points": base_points,
                "penalty_waived": False,
                **ptp_evidence,
            },
        }
    return {
        "code": COMPONENT_EFFORT,
        "points": base_points,
        # Effort never abstains: "not visited yet" is a measurement, not a gap.
        "abstained": False,
        "summary": base_summary,
        "evidence": {"visit_count": visits,
                     "max_visits_allowed": allowed,
                     "base_effort_points": base_points,
                     "ptp_due_in_days": ptp_due_in_days,
                     "penalty_waived": False},
    }


_COMPONENTS = (_value, _urgency, _effort)


def _reason(components: list[dict]) -> str:
    """One line a manager can read out. Names the two terms that moved it most.

    Deliberately built from the components rather than written independently —
    a reason that can disagree with the numbers beside it is worse than none.
    """
    by_code = {c["code"]: c for c in components}
    value, urgency, effort = (by_code[COMPONENT_VALUE], by_code[COMPONENT_URGENCY],
                              by_code[COMPONENT_EFFORT])

    if value.get("abstained"):
        # Says what we do not know, rather than asserting a low balance we never
        # measured.
        lead = "Recoverable balance not yet estimated"
    elif value["points"] >= 43.0:
        lead = "Large recoverable balance"
    elif value["points"] >= 26.0:
        lead = "Worthwhile recoverable balance"
    elif value["points"] > 0.0:
        lead = "Small recoverable balance"
    else:
        lead = "Little or no recoverable balance"

    # FROM THE DPD, NOT FROM THE POINTS. The urgency ladder is deliberately
    # non-monotonic — it peaks at 76-90 and falls after the line — so the same
    # points occur at BOTH ends of the range: 8 points means "under 30 days"
    # and 10 means "over 180". Reading position off the points collapsed those
    # into one branch, and on 2026-08-28 that made 256 of 591 open cases (43%)
    # describe themselves wrongly: a case 12 days late read "long past the NPA
    # line", directly contradicting the "12 days late" printed above it, and a
    # case at 95 days read "mid-delinquency". Thresholds below mirror
    # _urgency's own summary exactly, so the two cannot disagree again.
    dpd_seen = urgency.get("evidence", {}).get("dpd")
    if urgency.get("abstained") or dpd_seen is None:
        mid = "days late not recorded"
    elif dpd_seen > 120.0:
        mid = "long past the NPA line"
    elif dpd_seen > _DPD_NEUTRAL:
        mid = f"past the {_DPD_NEUTRAL:.0f}-day NPA line"
    elif dpd_seen > 75.0:
        mid = f"close to the {_DPD_NEUTRAL:.0f}-day NPA line"
    elif dpd_seen > 45.0:
        mid = "mid-delinquency"
    else:
        mid = "early-stage"

    if effort.get("evidence", {}).get("ptp_follow_up_points") is not None:
        tail = "promise falling due"
    elif effort["points"] <= _EFFORT_EXHAUSTED:
        tail = "already visited repeatedly"
    elif effort["points"] < 0.0:
        tail = "part-worked"
    else:
        tail = "not yet visited"
    return f"{lead}, {mid}, {tail}"


def score(features) -> dict:
    """Rank one CASE. Deterministic given the facts handed in.

    `features` is anything _f can read — a Mapping, a dataclass, or a
    SimpleNamespace in tests. Expected names:

        recovery_rate_90    float | None   latest snapshot rate_90
        total_outstanding   float          live Loan.total_outstanding
        dpd                 int   | None   live Loan.dpd
        visit_count         int            Case.visit_count
        max_visits_allowed  int            Case.max_visits_allowed
        ptp_due_in_days     float | None   days to the soonest ACTIVE PTP

    Every component always appears, in COMPONENT_ORDER, whether or not it had
    data to work with.
    """
    components = [fn(features) for fn in _COMPONENTS]
    # Order is a contract, not an accident of _COMPONENTS.
    components.sort(key=lambda c: COMPONENT_ORDER.index(c["code"]))

    raw = sum(c["points"] for c in components)
    total = round(_clamp(raw, 0.0, 100.0), 1)
    return {
        "score": total,
        "band": band_for(total),
        "components": components,
        "reason": _reason(components),
        # Named for what it is. There is no model here; a sort order that called
        # itself one would be the single most misleading claim this file could
        # make.
        "is_modelled": False,
        "model_version": SCORE_VERSION,
    }
