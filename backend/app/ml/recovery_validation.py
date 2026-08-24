# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-24 — New file. The measuring instrument for the recovery scorecard,
#   built BEFORE the first outcome labels exist so that the questions are fixed
#   in advance and cannot be chosen after seeing the answers.
#
#   READ-ONLY BY CONSTRUCTION. Pure functions over numbers. No database, no
#   session, no writes — the caller (scripts/validate_recovery.py) does the
#   reading. Same split as ml/recovery_scorecard.py and repayment_service.py:
#   the arithmetic is pure and testable, the I/O is somewhere else.
#
#   RANKING AND CALIBRATION ARE SEPARATE, and keeping them separate is the whole
#   point of this module. They answer different questions, fail independently,
#   and are fixed with different levers:
#
#     RANKING     — do loans the scorecard rates higher actually recover more?
#                   Invariant to any monotone transform: multiply every
#                   prediction by 0.4 and the ranking is identical. This is what
#                   the HIGH/MEDIUM/LOW LABEL needs to be right, because the
#                   label orders field work.
#     CALIBRATION — when it says 38.8%, does 38.8% come back? This is what the
#                   RUPEE FIGURE needs to be right, and it is what the
#                   ₹21.06 Cr on the Analytics page rests on.
#
#   A scorecard can rank perfectly and be uniformly miscalibrated (fix: the
#   intercept, BASE_RATE). It can be calibrated on average and rank poorly (fix:
#   the weights). Reporting one number that mixes them would hide both.
#
#   NUMPY ONLY. scipy is not a declared dependency of this backend — it happens
#   to be installed on one developer machine, which is exactly how an import
#   error reaches a container. Spearman and the confidence interval are
#   implemented here rather than imported.
#
#   NOTHING HERE CHANGES THE SCORECARD. It measures; it does not tune. Any
#   recalibration that these numbers eventually justify is a separate, versioned
#   decision.
# ───────────────────────────────────────────────────────────────────────────
"""Did the recovery scorecard work — ranking and calibration, kept apart."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

# ── Admissibility ────────────────────────────────────────────────────────────
# Why a scored row may not enter the analysis. Order matters: the first reason
# that applies wins, so a censored row that is also immature reads as IMMATURE
# (it may yet resolve) rather than as permanently excluded.
IMMATURE = "IMMATURE"                # the horizon has not closed yet
VERSION_MISMATCH = "VERSION_MISMATCH"  # scored by a different scorecard version
UNOBSERVABLE = "UNOBSERVABLE"        # no case, so the ledger cannot see a payment
CENSORED = "CENSORED"                # bank recall / settlement / write-off
NO_DENOMINATOR = "NO_DENOMINATOR"    # total_outstanding <= 0, no rate definable
BACKFILL = "BACKFILL"                # features leak the future; never train on these
ADMISSIBLE = "ADMISSIBLE"

# Outcomes that mean money did not arrive for a reason that is not the
# borrower's conduct. Mirrors CENSORED_OUTCOMES in models/repayment_snapshot.py;
# duplicated as plain strings so this module stays importable without the ORM.
CENSORING_OUTCOMES = frozenset({
    "SETTLED", "WRITTEN_OFF", "RECALLED", "DECEASED", "CENSORED",
})

# The version this validation run is for. Pooling across versions would average
# two different scorecards and call the result one measurement.
EXPECTED_VERSION = "recovery-scorecard-1.1.0"

# Below this an band's mean is too noisy to calibrate a level against. Chosen
# before the data arrived: at a realised-rate SD near 0.25, n=100 gives roughly
# ±5pp at 95%, which can separate bands but only just.
MIN_ADMISSIBLE_PER_BAND = 100

BANDS = ("HIGH", "MEDIUM", "LOW")


def classify(row: Any, *, horizon: int, expected_version: str = EXPECTED_VERSION) -> str:
    """Why this row is or is not usable at this horizon.

    `row` is anything with the snapshot's attributes — an ORM row, a stub, a
    mapping wrapped in an object. Deliberately duck-typed so the caller decides
    where the data comes from.
    """
    if getattr(row, "recovery_model_version", None) != expected_version:
        return VERSION_MISMATCH
    if getattr(row, "is_backfill", False):
        return BACKFILL

    through = getattr(row, "recovery_labelled_through_days", 0) or 0
    amount = getattr(row, f"recovered_amount_{horizon}", None)

    # The three-state rule the snapshot model documents: NULL with the marker
    # behind the horizon means "not yet"; NULL with the marker at or past it
    # means the ledger could never see this loan; 0.0 means observed and empty.
    if amount is None:
        return UNOBSERVABLE if through >= horizon else IMMATURE

    if (getattr(row, "outcome", None) or "") in CENSORING_OUTCOMES:
        return CENSORED
    if not _outstanding(row):
        return NO_DENOMINATOR
    return ADMISSIBLE


def _outstanding(row: Any) -> float:
    features = getattr(row, "features", None) or {}
    try:
        return float(features.get("total_outstanding") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Observation:
    """One admissible loan at one horizon: what was predicted, what came back."""
    loan_id: str
    band: str
    predicted_rate: float
    realised_rate: float
    outstanding: float
    secured: str                      # SECURED / UNSECURED / UNDETERMINED
    factors: dict[str, float] = field(default_factory=dict)

    @property
    def predicted_amount(self) -> float:
        return self.predicted_rate * self.outstanding

    @property
    def realised_amount(self) -> float:
        return self.realised_rate * self.outstanding

    @property
    def residual(self) -> float:
        """Realised minus predicted. Positive means the scorecard undercalled."""
        return self.realised_rate - self.predicted_rate


def observation_from(row: Any, *, horizon: int) -> Observation:
    """Build an Observation from an admissible snapshot row."""
    outstanding = _outstanding(row)
    realised = float(getattr(row, f"recovered_amount_{horizon}") or 0.0)
    contributions = (getattr(row, "recovery_contributions", None) or {})
    factors: dict[str, float] = {}
    security = "UNDETERMINED"
    for f in contributions.get("factors", []) or []:
        if f.get("abstained"):
            continue
        code = f.get("code")
        points = float(f.get("points") or 0.0)
        factors[code] = points
        if code == "SECURITY":
            security = "SECURED" if points > 0 else "UNSECURED"
    return Observation(
        loan_id=getattr(row, "loan_id", ""),
        band=getattr(row, "recovery_potential", "") or "",
        predicted_rate=float(getattr(row, f"recovery_rate_{horizon}") or 0.0),
        realised_rate=realised / outstanding if outstanding else 0.0,
        outstanding=outstanding,
        secured=security,
        factors=factors,
    )


# ── Statistics ───────────────────────────────────────────────────────────────
def mean_with_ci(values: Sequence[float], z: float = 1.96) -> dict[str, Any]:
    """Mean and a 95% interval.

    NORMAL APPROXIMATION, not Student's t. At n >= 30 the difference is under
    ~4% of the interval width and does not change any decision here; below that
    the interval is understated, which is why `small_sample` is returned rather
    than left for the reader to work out. It is the caller's job to refuse to
    conclude from a flagged band, and validate_recovery.py does.
    """
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "lo": None, "hi": None,
                "sd": None, "small_sample": True}
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    half = z * sd / np.sqrt(n) if n > 1 else 0.0
    return {"n": n, "mean": mean, "lo": mean - half, "hi": mean + half,
            "sd": sd, "small_sample": n < 30}


def _rank(values: Sequence[float]) -> np.ndarray:
    """Average ranks, so ties do not distort the correlation."""
    arr = np.asarray(values, dtype=float)
    order = arr.argsort()
    ranks = np.empty(len(arr), dtype=float)
    ranks[order] = np.arange(1, len(arr) + 1, dtype=float)
    # Average the ranks within each tied group.
    for value in np.unique(arr):
        mask = arr == value
        if mask.sum() > 1:
            ranks[mask] = ranks[mask].mean()
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Correlation, or None when either series is constant.

    The constant check is on the RANGE, not the standard deviation. Several
    factors are constant by construction — NPA_STATUS is always exactly -0.06
    when it speaks, SECURITY always ±0.16 — and for those np.std() returns
    something like 2e-16 rather than 0. A `std == 0` guard lets that through,
    np.corrcoef divides by it, and the report shows a correlation that is pure
    floating-point noise sitting next to real ones.
    """
    if len(x) < 3 or len(x) != len(y):
        return None
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    # Belt and braces for series that vary only by rounding dust.
    if a.std() < 1e-12 or b.std() < 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Rank correlation — Pearson over average ranks.

    THE PRIMARY RANKING METRIC, and deliberately rank-based: it is unchanged by
    any monotone rescaling of the predictions, so it measures ordering and says
    nothing at all about whether the levels are right. That separation is the
    reason it is here rather than a plain correlation on the rates.
    """
    if len(x) < 3 or len(x) != len(y):
        return None
    return pearson(_rank(x), _rank(y))


# ── Ranking ──────────────────────────────────────────────────────────────────
def ranking_metrics(observations: Sequence[Observation]) -> dict[str, Any]:
    """Does the order hold? Nothing here depends on the predicted LEVEL."""
    by_band = {b: [o.realised_rate for o in observations if o.band == b] for b in BANDS}
    band_stats = {b: mean_with_ci(v) for b, v in by_band.items()}

    means = [band_stats[b]["mean"] for b in BANDS]
    monotonic = all(
        a is not None and b is not None and a >= b
        for a, b in zip(means, means[1:])
    )

    high, low = band_stats["HIGH"]["mean"], band_stats["LOW"]["mean"]
    lift = (high / low) if (high is not None and low not in (None, 0)) else None

    # Do the extreme bands actually separate, or do their intervals overlap?
    separated = None
    if band_stats["HIGH"]["lo"] is not None and band_stats["LOW"]["hi"] is not None:
        separated = band_stats["HIGH"]["lo"] > band_stats["LOW"]["hi"]

    # How often does a LOW loan beat the typical HIGH one? A cheap, readable
    # measure of overlap that does not assume a distribution.
    overlap = None
    if by_band["HIGH"] and by_band["LOW"]:
        high_median = float(np.median(by_band["HIGH"]))
        overlap = sum(1 for v in by_band["LOW"] if v > high_median) / len(by_band["LOW"])

    return {
        "by_band": band_stats,
        "monotonic": monotonic,
        "high_low_lift": lift,
        "bands_separated": separated,
        "low_above_high_median": overlap,
        "spearman": spearman([o.predicted_rate for o in observations],
                             [o.realised_rate for o in observations]),
        "insufficient_bands": [b for b in BANDS
                               if band_stats[b]["n"] < MIN_ADMISSIBLE_PER_BAND],
    }


def top_k_capture(observations: Sequence[Observation], k: float = 0.20) -> dict[str, Any]:
    """Share of realised money sitting in the top k by PREDICTED money.

    THE OPERATIONAL METRIC — the one that maps to reallocating agents. Random
    targeting captures k. Anything near k means the label does not change where
    the team should go, whatever the correlations say.
    """
    if not observations:
        return {"k": k, "captured": None, "baseline": k, "lift": None, "n": 0}
    ranked = sorted(observations, key=lambda o: -o.predicted_amount)
    cutoff = max(1, int(round(len(ranked) * k)))
    total = sum(o.realised_amount for o in ranked)
    if total <= 0:
        return {"k": k, "captured": None, "baseline": k, "lift": None,
                "n": len(ranked), "note": "no money recovered in the cohort"}
    captured = sum(o.realised_amount for o in ranked[:cutoff]) / total
    return {"k": k, "captured": captured, "baseline": k, "lift": captured / k,
            "n": len(ranked), "loans_in_top_k": cutoff}


def marginal_lift_by_security(observations: Sequence[Observation]) -> dict[str, Any]:
    """Does the label still order loans WITHIN a collateral class?

    The question SECURITY's dominance forces. On the 2026-08-24 cohort it was the
    largest single contributor on 424 of 525 loans and 139 of 165 HIGH loans were
    secured. If the ordering vanishes inside each group, the label is an
    expensive proxy for loan_type and the honest thing is to say so.
    """
    out: dict[str, Any] = {}
    for group in ("SECURED", "UNSECURED", "UNDETERMINED"):
        subset = [o for o in observations if o.secured == group]
        stats = {b: mean_with_ci([o.realised_rate for o in subset if o.band == b])
                 for b in BANDS}
        high, low = stats["HIGH"]["mean"], stats["LOW"]["mean"]
        out[group] = {
            "n": len(subset),
            "by_band": stats,
            "high_low_lift": (high / low) if (high is not None and low not in (None, 0)) else None,
            "spearman": spearman([o.predicted_rate for o in subset],
                                 [o.realised_rate for o in subset]),
        }
    return out


# ── Calibration ──────────────────────────────────────────────────────────────
def calibration_metrics(observations: Sequence[Observation]) -> dict[str, Any]:
    """Are the LEVELS right? Nothing here says anything about the ordering."""
    if len(observations) < 3:
        return {"n": len(observations), "bias": None, "slope": None,
                "intercept": None, "by_band": {}}

    predicted = np.array([o.predicted_rate for o in observations], dtype=float)
    realised = np.array([o.realised_rate for o in observations], dtype=float)

    # Calibration-in-the-large: the average miss. A large, roughly CONSTANT bias
    # across bands is an intercept error, and BASE_RATE is the intercept.
    bias = float(realised.mean() - predicted.mean())

    # Calibration slope: regress realised on predicted. 1.0 is perfect. Below 1
    # means the predictions are spread too widely — the scorecard is more
    # confident about differences than the outcomes justify.
    slope = intercept = None
    if predicted.std() > 0:
        slope, intercept = (float(v) for v in np.polyfit(predicted, realised, 1))

    by_band = {}
    for b in BANDS:
        subset = [o for o in observations if o.band == b]
        if not subset:
            by_band[b] = {"n": 0}
            continue
        p = mean_with_ci([o.predicted_rate for o in subset])
        r = mean_with_ci([o.realised_rate for o in subset])
        by_band[b] = {"n": len(subset), "predicted": p["mean"], "realised": r["mean"],
                      "realised_lo": r["lo"], "realised_hi": r["hi"],
                      "bias": r["mean"] - p["mean"], "small_sample": r["small_sample"]}

    # Is the bias the same everywhere (an intercept problem) or band-dependent
    # (a spread problem)? Reported as a spread, not diagnosed here — the caller
    # states the diagnosis so the rule stays visible in the report.
    biases = [v["bias"] for v in by_band.values() if v.get("bias") is not None]
    return {
        "n": len(observations),
        "mean_predicted": float(predicted.mean()),
        "mean_realised": float(realised.mean()),
        "bias": bias,
        "slope": slope,
        "intercept": intercept,
        "by_band": by_band,
        "bias_spread": (max(biases) - min(biases)) if len(biases) > 1 else None,
    }


def factor_residual_correlation(observations: Sequence[Observation]) -> dict[str, Any]:
    """Which factors correlate with the scorecard's own error?

    A factor whose contribution tracks the residual is mis-weighted: when it
    speaks, the prediction is wrong in a consistent direction. Correlation, not
    causation, and with this many factors some correlation is expected by chance
    — this ranks candidates for investigation, it does not license a re-weighting.
    """
    codes = sorted({c for o in observations for c in o.factors})
    residuals = [o.residual for o in observations]
    out = {}
    for code in codes:
        pairs = [(o.factors[code], o.residual) for o in observations if code in o.factors]
        if len(pairs) < 10:
            out[code] = {"n": len(pairs), "correlation": None,
                         "note": "too few observations"}
            continue
        xs, ys = zip(*pairs)
        out[code] = {"n": len(pairs), "correlation": pearson(xs, ys),
                     "mean_contribution": float(np.mean(xs))}
    return dict(sorted(
        out.items(),
        key=lambda kv: abs(kv[1]["correlation"]) if kv[1]["correlation"] is not None else -1,
        reverse=True))


def diagnose(ranking: dict[str, Any], calibration: dict[str, Any],
             *, bias_tolerance: float = 0.05,
             spearman_floor: float = 0.30) -> dict[str, Any]:
    """Cross ranking against calibration to name the lever — without pulling it.

    The table this implements:
      ranking good + bias uniform   -> BASE_RATE (an intercept shift)
      ranking good + bias by band   -> weight scale or band edges
      ranking poor                  -> factor weights
      both good                     -> leave it alone
    """
    rho = ranking.get("spearman")
    bias = calibration.get("bias")
    spread = calibration.get("bias_spread")

    ranks_well = (ranking.get("monotonic") is True
                  and rho is not None and rho >= spearman_floor)
    calibrated = bias is not None and abs(bias) <= bias_tolerance
    uniform = spread is not None and spread <= bias_tolerance

    if rho is None or bias is None:
        verdict, lever = "INSUFFICIENT_DATA", None
    elif ranks_well and calibrated:
        verdict, lever = "BOTH_HOLD", None
    elif ranks_well and not calibrated and uniform:
        verdict, lever = "LEVEL_WRONG_ORDER_RIGHT", "BASE_RATE"
    elif ranks_well and not calibrated:
        verdict, lever = "SPREAD_WRONG_ORDER_RIGHT", "WEIGHT_SCALE_OR_BAND_EDGES"
    else:
        verdict, lever = "ORDER_WRONG", "FACTOR_WEIGHTS"

    return {
        "verdict": verdict,
        "candidate_lever": lever,
        "ranks_well": ranks_well,
        "calibrated": calibrated,
        "bias_uniform_across_bands": uniform,
        # Stated every time: this module measures and never tunes.
        "action": "REPORT ONLY — no scorecard change is authorised by this run",
    }


def summarise_admissibility(counts: dict[str, int]) -> dict[str, Any]:
    """Exclusions before findings, always."""
    total = sum(counts.values())
    admissible = counts.get(ADMISSIBLE, 0)
    return {
        "total": total,
        "counts": dict(counts),
        "admissible": admissible,
        "admissible_share": (admissible / total) if total else None,
        "excluded": total - admissible,
    }
