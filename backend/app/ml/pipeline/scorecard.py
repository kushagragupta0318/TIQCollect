# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. PDO scaling of a fitted WOE logistic into integer points,
#   plus reason codes derived from the points themselves.
#
#   REASON CODES ARE NOT AN EXPLANATION BOLTED ON AFTERWARDS. They are the same
#   arithmetic the score is made of, kept instead of thrown away — the identical
#   argument services/global_allocator.py makes for recording each utility term's
#   contribution rather than recomputing a story later. A reason code that is
#   derived separately from the score can disagree with it; one that IS the score
#   cannot.
# ───────────────────────────────────────────────────────────────────────────
"""
Scorecard scaling and adverse-reason codes.

TWO NUMBERS, TWO DIRECTIONS — READ THIS BEFORE USING EITHER
-----------------------------------------------------------
    probability   P(bad). HIGHER = WORSE. This is what every gate, decile table
                  and evaluation in ml/pipeline consumes.
    points        The scorecard number, scaled by PDO. HIGHER = BETTER, the way
                  a bureau score reads, because that is what a human expects of
                  something called a score.

They are monotonic inverses of each other. Mixing them up produces a model that
is exactly as accurate and completely backwards, so nothing here returns a bare
float — ScoreCardOutput carries both, named.

THE SCALING
-----------
    factor = PDO / ln(2)
    offset = base_score - factor * ln(base_odds)
    points = offset + factor * ln(odds_good)
           = offset - factor * (b0 + sum_i b_i * WOE_i)

The intercept and offset are spread evenly across the contributing features so
that per-feature points sum exactly to the total. That spreading is a
presentation choice and it does not change any ranking.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class ScoreCardOutput:
    probability: float          # P(bad); higher = worse
    points: int                 # scorecard points; higher = better
    band: str
    reason_codes: list[dict] = field(default_factory=list)
    feature_points: dict[str, int] = field(default_factory=dict)


# Bands over POINTS (higher = better), so A is the safest.
#
# THESE ARE ONLY A FALLBACK. Fixed cutoffs are a guess about where a score
# distribution will land, and this one was wrong: with PDO 20 and a base of 600,
# the fitted model put the whole book between 362 and 518 points, so a borrower
# at P(bad) = 0.26 and one at P(bad) = 0.99 both graded "E" and the band carried
# no information at all. Bands are FITTED from the development distribution by
# ScoreCard.fit_bands() and stored in the artifact; these constants exist only
# so a card built without fitting still returns something.
DEFAULT_BANDS = [
    ("A", 640, 10_000), ("B", 610, 640), ("C", 585, 610),
    ("D", 560, 585), ("E", -10_000, 560),
]

# What share of the book falls in each band, safest first. Not equal fifths: a
# collections book wants a small, clearly-identified best grade and a wide tail,
# because the action taken on band A differs from B far more than D differs
# from E.
BAND_SHARES = [("A", 0.15), ("B", 0.20), ("C", 0.25), ("D", 0.25), ("E", 0.15)]


def band_for(points: float, bands=DEFAULT_BANDS) -> str:
    for name, lo, hi in bands:
        if lo <= points < hi:
            return name
    return bands[-1][0]


class ScoreCard:
    """Turns a fitted WOE logistic into points, per-feature points and reasons."""

    def __init__(self, features: list[str], coefficients: np.ndarray,
                 intercept: float, *, pdo: int = 20, base_score: int = 600,
                 base_odds: float = 50.0, bands=DEFAULT_BANDS):
        self.features = list(features)
        self.coefficients = np.asarray(coefficients, dtype=float)
        self.intercept = float(intercept)
        self.pdo = pdo
        self.base_score = base_score
        self.base_odds = base_odds
        self.bands = bands
        self.factor = pdo / math.log(2)
        self.offset = base_score - self.factor * math.log(base_odds)

    # ── points ──────────────────────────────────────────────────────────────
    def points_frame(self, X_woe: pd.DataFrame) -> pd.DataFrame:
        """Per-feature integer points. Rows sum to the total score."""
        k = max(len(self.features), 1)
        share_offset = self.offset / k
        share_intercept = self.factor * self.intercept / k
        out = pd.DataFrame(index=X_woe.index)
        for f, b in zip(self.features, self.coefficients):
            out[f] = np.round(share_offset - share_intercept
                              - self.factor * b * X_woe[f].to_numpy())
        return out.astype(int)

    def score(self, X_woe: pd.DataFrame) -> pd.Series:
        return self.points_frame(X_woe).sum(axis=1)

    # ── bands ───────────────────────────────────────────────────────────────
    def fit_bands(self, X_woe: pd.DataFrame, y=None) -> "ScoreCard":
        """Cut bands at quantiles of the DEVELOPMENT score distribution.

        A band is a decision boundary, so it has to sit where the population
        actually is. Fitting also lets the artifact record the observed bad rate
        per band, which is the number an operator reads — "band D defaults 78%
        of the time" is actionable; "band D means 560-585 points" is not.
        """
        pts = self.score(X_woe).to_numpy()
        cuts, acc = [], 0.0
        for name, share in BAND_SHARES[:-1]:
            acc += share
            cuts.append((name, float(np.quantile(pts, 1.0 - acc))))
        bands, upper = [], 10_000.0
        for name, lo in cuts:
            bands.append((name, lo, upper))
            upper = lo
        bands.append((BAND_SHARES[-1][0], -10_000.0, upper))
        self.bands = bands

        if y is not None:
            yy = np.asarray(y).astype(int)
            self.band_table_ = (
                pd.DataFrame({"band": [band_for(p, bands) for p in pts],
                              "points": pts, "y": yy})
                .groupby("band")
                .agg(count=("y", "size"), bad_rate=("y", "mean"),
                     min_points=("points", "min"), max_points=("points", "max"))
                .reset_index().sort_values("min_points", ascending=False))
        return self

    # ── reason codes ────────────────────────────────────────────────────────
    def fit_reference(self, X_woe: pd.DataFrame) -> "ScoreCard":
        """Record the best attainable points per feature on the development set.

        A reason code answers "why is this score not higher", so it needs a
        reference for what higher would have been. The maximum observed points
        per feature is that reference; using a theoretical maximum instead would
        cite bins that no borrower on the book actually occupies.
        """
        pf = self.points_frame(X_woe)
        self.max_points_ = pf.max().to_dict()
        self.min_points_ = pf.min().to_dict()
        return self

    def reasons(self, feature_points: dict[str, int], top_n: int = 4) -> list[dict]:
        ref = getattr(self, "max_points_", None)
        rows = []
        for f, p in feature_points.items():
            best = ref.get(f, p) if ref else p
            rows.append({"feature": f.removesuffix("_woe"),
                         "points": int(p), "points_lost": int(best - p)})
        rows.sort(key=lambda r: -r["points_lost"])
        return [r for r in rows if r["points_lost"] > 0][:top_n]

    # ── the whole output for one row ────────────────────────────────────────
    def explain(self, X_woe_row: pd.DataFrame, probability: float) -> ScoreCardOutput:
        pf = self.points_frame(X_woe_row).iloc[0].to_dict()
        total = int(sum(pf.values()))
        return ScoreCardOutput(
            probability=float(probability),
            points=total,
            band=band_for(total, self.bands),
            reason_codes=self.reasons(pf),
            feature_points={k: int(v) for k, v in pf.items()},
        )

    def to_dict(self) -> dict:
        return {
            "features": self.features,
            "coefficients": [float(c) for c in self.coefficients],
            "intercept": self.intercept,
            "pdo": self.pdo, "base_score": self.base_score,
            "base_odds": self.base_odds,
            "factor": round(self.factor, 6), "offset": round(self.offset, 6),
            "bands": [list(b) for b in self.bands],
            "band_table": (self.band_table_.to_dict(orient="records")
                           if hasattr(self, "band_table_") else []),
            "max_points": getattr(self, "max_points_", {}),
        }


def scorecard_table(sc: ScoreCard, binner, X_woe: pd.DataFrame) -> pd.DataFrame:
    """The printable scorecard: every feature, every bin, its WOE and its points.

    This is the artifact a credit committee actually reads — not the
    coefficients, and not the AUC. It is generated from the fitted objects, so
    it cannot drift from the model that is deployed.
    """
    k = max(len(sc.features), 1)
    share = sc.offset / k - sc.factor * sc.intercept / k
    rows = []
    for f, b in zip(sc.features, sc.coefficients):
        raw = f.removesuffix("_woe")
        table = binner.tables_.get(raw)
        if table is None:
            continue
        labels = table["Bin"].astype(str)
        body = table[~labels.isin(("",))]
        for _, r in body.iterrows():
            woe = pd.to_numeric(pd.Series([r["WoE"]]), errors="coerce").iloc[0]
            if not np.isfinite(woe):
                continue
            rows.append({
                "feature": raw,
                "bin": str(r["Bin"]),
                "count": int(r["Count"]) if pd.notna(r["Count"]) else 0,
                "event_rate": round(float(r["Event rate"]), 4),
                "woe": round(float(woe), 4),
                "coefficient": round(float(b), 5),
                "points": int(round(share - sc.factor * b * woe)),
            })
    return pd.DataFrame(rows)
