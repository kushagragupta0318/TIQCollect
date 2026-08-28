# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. "Which cases should agents visit first?"
#
#   WHAT THIS REPLACES. Case.priority, computed once at case creation as
#   min(100, dpd/90*40 + outstanding_principal/500000*30) and never recomputed
#   (seed_data.py:1492; repayment_service.py:732-745 explicitly declines to
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

SCORE_VERSION = "visit-priority-1.0.0"

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
# on its own it is what abandoned NPA last time. Effort only ever subtracts.
MAX_VALUE_POINTS = 50.0
MAX_URGENCY_POINTS = 35.0
MAX_EFFORT_PENALTY = -25.0

# The best achievable total is 90, not 100. Left as 90 rather than rescaled: a
# case that is large, about to tip into NPA and untouched still has no live
# promise to its name, and pretending 100 is reachable would misrepresent the
# ceiling.
PTP_PROTECTION_BONUS = 5.0

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
# PEAKS AT 76-89 DPD AND FALLS AFTER 90, on purpose.
#
# 90 days is the RBI line at which a loan is classified non-performing. The
# fortnight before it is the last chance to prevent that classification, so it is
# where a collections team pushes hardest. Once the line is crossed the cliff is
# behind us and the classification damage is done, so urgency genuinely falls.
#
# THIS DOES NOT ABANDON NPA. A large NPA case still outranks a small pre-NPA one
# through the value component, which carries more weight than urgency. That is
# the whole correction to the removed DPD policy, and
# tests/test_visit_priority.py pins it.
_URGENCY_LADDER = (
    (180.0, 10.0),   # beyond here further ageing tells us little new
    (120.0, 14.0),
    (_DPD_NEUTRAL, 20.0),   # classified; worked on value, not on urgency
    (75.0, 35.0),    # 76-89 — the peak, the last fortnight before the line
    (60.0, 31.0),
    (45.0, 22.0),
    (30.0, 12.0),
)
_URGENCY_FLOOR = 8.0        # under 30 DPD barely reaches field collections

# ── Effort penalty ──────────────────────────────────────────────────────────
# Diminishing returns. Measured on the benchmark book: a first visit returned
# ~Rs 6,667 against ~Rs 2,872 by the third, so breadth beat depth. 39.5% of open
# cases sit at 3+ visits, so this differentiates rather than decorating.
_EFFORT_PENALTIES = {0: 0.0, 1: -6.0, 2: -14.0}
_EFFORT_EXHAUSTED = -25.0

# A promise falling due is the one case where past effort genuinely predicts
# imminent money, so it is exempted from the penalty entirely and given a small
# push. Bounded deliberately: on the live book only 71 cases carry an active PTP
# due inside three days, so the exemption cannot swamp the ranking.
PTP_PROTECTION_DAYS = 3


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
    points = _ladder(amount, _VALUE_LADDER, _VALUE_FLOOR)
    return {
        "code": COMPONENT_VALUE,
        "points": points,
        "summary": f"{_rupees(amount)} recoverable over 90 days",
        "evidence": {
            "rate_90": round(float(rate_90), 4),
            "total_outstanding": round(outstanding, 2),
        },
    }


def _urgency(features) -> dict:
    """How close this loan is to the 90-day NPA line, and which side of it."""
    dpd = _f(features, "dpd")
    if dpd is None:
        return {
            "code": COMPONENT_URGENCY,
            "points": _URGENCY_FLOOR,
            "summary": "Days-past-due not recorded on this loan",
            "evidence": {"dpd": None},
        }
    dpd = float(dpd)
    points = _ladder(dpd, _URGENCY_LADDER, _URGENCY_FLOOR)

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

    protected = (ptp_due_in_days is not None
                 and 0 <= float(ptp_due_in_days) <= PTP_PROTECTION_DAYS)
    if protected:
        days = float(ptp_due_in_days)
        when = "today" if days < 1 else f"in {days:.0f} day{'s' if days >= 2 else ''}"
        return {
            "code": COMPONENT_EFFORT,
            "points": PTP_PROTECTION_BONUS,
            "summary": (f"{visits} visit{'s' if visits != 1 else ''} so far, and a "
                        f"promise falls due {when} — protected"),
            "evidence": {"visit_count": visits,
                         "max_visits_allowed": allowed,
                         "ptp_due_in_days": days,
                         "penalty_waived": True},
        }

    exhausted = allowed > 0 and visits >= allowed
    if visits >= 3 or exhausted:
        points = _EFFORT_EXHAUSTED
        summary = (f"{visits} visits already, against {allowed} allowed — "
                   f"further visits have returned little"
                   if exhausted else
                   f"{visits} visits already — further visits have returned little")
    else:
        points = _EFFORT_PENALTIES.get(visits, _EFFORT_EXHAUSTED)
        summary = ("Not visited yet" if visits == 0
                   else f"{visits} visit{'s' if visits != 1 else ''} so far")
    return {
        "code": COMPONENT_EFFORT,
        "points": points,
        "summary": summary,
        "evidence": {"visit_count": visits,
                     "max_visits_allowed": allowed,
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

    if urgency["points"] >= 31.0:
        mid = f"close to the {_DPD_NEUTRAL:.0f}-day NPA line"
    elif urgency["points"] <= 14.0:
        mid = "long past the NPA line"
    else:
        mid = "mid-delinquency"

    if effort["points"] > 0.0:
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
