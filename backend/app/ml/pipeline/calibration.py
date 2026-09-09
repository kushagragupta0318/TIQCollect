# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Post-hoc probability calibration, segmented by exposure.
#
#   WHY. The allocator shadow measured `recovery_risk` over-predicting at every
#   balance level, but WORST at the top: on a 1,200-case book the smallest
#   balance quintile was over-predicted by 21% and the largest by 89.6%. Expected
#   value is `balance x probability`, so an error concentrated in the
#   highest-balance segment is exactly where it costs the most money — the plan
#   lost 8-15% of realised rupees across four seeds while its case-level
#   recovery RATE went up. A model can rank well (Gini 0.51 out-of-time, Brier
#   improved 33%) and still be unusable for a rupee-denominated decision, and
#   that is what discrimination metrics alone will never tell you.
#
#   WHAT THIS IS NOT. It is not a re-weighting of the allocator's utility. The
#   0.45 expected_recovery weight is deliberately untouched: it was tuned against
#   a probability whose standard deviation was 0.015 — effectively a constant —
#   so retuning it now would mask the segment error rather than remove it. Fix
#   the probability first; reassess the economics afterwards.
#
#   FITTED ON HELD-OUT DATA, ALWAYS. A calibrator fitted on the same rows the
#   model was fitted on learns the model's training-set optimism and corrects
#   nothing. This fits on the VALIDATION slice — out-of-sample for the model,
#   untouched by the estimator — and is evaluated out-of-time.
# ───────────────────────────────────────────────────────────────────────────
"""
Segment-wise probability calibration.

    cal = SegmentCalibrator(segment_col="overdue_amount", n_segments=5)
    cal.fit(p_valid, y_valid, segment_values_valid)
    p_calibrated = cal.transform(p_oot, segment_values_oot)

TWO METHODS, CHOSEN BY CROSS-VALIDATION RATHER THAN BY PREFERENCE
-----------------------------------------------------------------
    platt      a logistic fitted on the logit of the raw probability, two
               parameters per segment. Smooth, hard to overfit, corrects a
               level-and-slope shift — which is what a segment bias looks like.
    isotonic   non-parametric and monotone. Strictly more flexible, and with a
               few thousand rows per segment it can chase noise into step
               functions that look excellent in-sample.

`fit` runs k-fold inside the calibration sample and picks whichever has the
better held-out Brier, then refits the winner on everything. The choice and both
scores are recorded, so it is visible rather than asserted.

MONOTONICITY IS PRESERVED WITHIN A SEGMENT. Both methods are monotone in the raw
probability, so calibration never reorders two borrowers inside the same segment
— it only moves the level. Ranking within a segment, and therefore the model's
Gini within a segment, is mathematically unchanged.
"""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _brier(y, p) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


class _PlattSegment:
    """Two-parameter logistic on the logit. Monotone by construction."""

    def fit(self, p, y):
        x = _logit(p).reshape(-1, 1)
        y = np.asarray(y).astype(int)
        if len(np.unique(y)) < 2:
            # A segment with one class cannot be calibrated; fall back to the
            # observed base rate rather than inventing a slope.
            self.constant_ = float(y.mean())
            self.model_ = None
            return self
        self.constant_ = None
        self.model_ = LogisticRegression(max_iter=1000)
        self.model_.fit(x, y)
        return self

    def transform(self, p):
        if self.model_ is None:
            return np.full(len(p), self.constant_, dtype=float)
        return self.model_.predict_proba(_logit(p).reshape(-1, 1))[:, 1]


class _IsotonicSegment:
    def fit(self, p, y):
        y = np.asarray(y).astype(int)
        if len(np.unique(y)) < 2:
            self.constant_ = float(y.mean())
            self.model_ = None
            return self
        self.constant_ = None
        self.model_ = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self.model_.fit(np.asarray(p, dtype=float), y)
        return self

    def transform(self, p):
        if self.model_ is None:
            return np.full(len(p), self.constant_, dtype=float)
        return self.model_.predict(np.asarray(p, dtype=float))


_METHODS = {"platt": _PlattSegment, "isotonic": _IsotonicSegment}


class SegmentCalibrator:
    """Calibrate P(event) separately within each band of an exposure variable.

    Parameters
    ----------
    segment_col
        The feature whose bands define the segments. For this model it is
        `overdue_amount` — the quantity the probability gets multiplied BY when
        the allocator turns it into rupees, which is precisely why an error
        correlated with it is so expensive.
    n_segments
        Quantile bands. Cuts are learned on the calibration sample and frozen.
    min_rows_per_segment
        Below this a band is merged into its neighbour rather than fitted on too
        little evidence.
    """

    def __init__(self, segment_col: str = "overdue_amount", n_segments: int = 5,
                 min_rows_per_segment: int = 400, random_state: int = 0):
        self.segment_col = segment_col
        self.n_segments = n_segments
        self.min_rows_per_segment = min_rows_per_segment
        self.random_state = random_state

    # ── fitting ─────────────────────────────────────────────────────────────
    def _bands(self, seg_values: np.ndarray) -> np.ndarray:
        return np.clip(np.searchsorted(self.edges_, seg_values, side="right"),
                       0, len(self.edges_))

    def _fit_method(self, name, p, y, bands):
        fitted = {}
        for b in range(len(self.edges_) + 1):
            m = bands == b
            if m.sum() == 0:
                continue
            fitted[b] = _METHODS[name]().fit(p[m], y[m])
        return fitted

    @staticmethod
    def _apply(fitted, p, bands):
        out = np.array(p, dtype=float, copy=True)
        for b, model in fitted.items():
            m = bands == b
            if m.any():
                out[m] = model.transform(p[m])
        return np.clip(out, 0.0, 1.0)

    def fit(self, p_raw, y, segment_values):
        p_raw = np.asarray(p_raw, dtype=float)
        y = np.asarray(y).astype(int)
        seg = np.asarray(segment_values, dtype=float)

        # Quantile cuts, then merge any band too thin to support a fit.
        qs = np.linspace(0, 100, self.n_segments + 1)[1:-1]
        self.edges_ = np.unique(np.percentile(seg[np.isfinite(seg)], qs))
        bands = self._bands(seg)
        counts = np.bincount(bands, minlength=len(self.edges_) + 1)
        while len(self.edges_) and counts.min() < self.min_rows_per_segment:
            drop = int(np.argmin(counts))
            keep = [i for i in range(len(self.edges_))
                    if i != min(drop, len(self.edges_) - 1)]
            self.edges_ = self.edges_[keep]
            bands = self._bands(seg)
            counts = np.bincount(bands, minlength=len(self.edges_) + 1)

        # Pick the method by held-out Brier inside the calibration sample, so
        # the choice is not made on the data it will be reported against.
        kf = KFold(n_splits=3, shuffle=True, random_state=self.random_state)
        scores: dict[str, float] = {}
        for name in _METHODS:
            preds = np.zeros_like(p_raw)
            for tr, te in kf.split(p_raw):
                fitted = self._fit_method(name, p_raw[tr], y[tr], bands[tr])
                preds[te] = self._apply(fitted, p_raw[te], bands[te])
            scores[name] = _brier(y, preds)
        self.cv_brier_ = {k: round(v, 6) for k, v in scores.items()}
        self.method_ = min(scores, key=scores.get)

        self.models_ = self._fit_method(self.method_, p_raw, y, bands)
        self.n_segments_fitted_ = len(self.models_)
        self.brier_before_ = round(_brier(y, p_raw), 6)
        self.brier_after_ = round(_brier(y, self.transform(p_raw, seg)), 6)
        return self

    # ── applying ────────────────────────────────────────────────────────────
    def transform(self, p_raw, segment_values):
        p_raw = np.asarray(p_raw, dtype=float)
        seg = np.asarray(segment_values, dtype=float)
        # A row with no segment value cannot be placed in a band. It is left
        # UNCALIBRATED rather than assigned to a default band — a silent
        # mis-assignment would apply the wrong correction, which is worse than
        # applying none.
        out = np.array(p_raw, copy=True)
        ok = np.isfinite(seg)
        if ok.any():
            out[ok] = self._apply(self.models_, p_raw[ok], self._bands(seg[ok]))
        return np.clip(out, 0.0, 1.0)

    def transform_one(self, p_raw: float, segment_value) -> float:
        if segment_value is None or not np.isfinite(float(segment_value)):
            return float(p_raw)
        return float(self.transform(np.array([p_raw]), np.array([float(segment_value)]))[0])

    # ── reporting ───────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "segment_col": self.segment_col,
            "method": self.method_,
            "cv_brier_by_method": self.cv_brier_,
            "edges": [float(e) for e in self.edges_],
            "n_segments_fitted": self.n_segments_fitted_,
            "brier_on_calibration_sample": {"before": self.brier_before_,
                                            "after": self.brier_after_},
        }

    def segment_report(self, p_raw, y, segment_values) -> list[dict]:
        """Predicted vs actual per band, before and after. The decisive table."""
        p_raw = np.asarray(p_raw, dtype=float)
        y = np.asarray(y).astype(int)
        seg = np.asarray(segment_values, dtype=float)
        p_cal = self.transform(p_raw, seg)
        bands = self._bands(seg)
        rows = []
        for b in range(len(self.edges_) + 1):
            m = bands == b
            if not m.any():
                continue
            actual = float(y[m].mean())
            rows.append({
                "segment": b + 1,
                "n": int(m.sum()),
                "mean_exposure": round(float(np.nanmean(seg[m])), 2),
                "actual": round(actual, 4),
                "predicted_raw": round(float(p_raw[m].mean()), 4),
                "predicted_calibrated": round(float(p_cal[m].mean()), 4),
                "over_prediction_raw_pct": round(
                    100 * (p_raw[m].mean() - actual) / actual, 1) if actual else None,
                "over_prediction_calibrated_pct": round(
                    100 * (p_cal[m].mean() - actual) / actual, 1) if actual else None,
            })
        return rows
