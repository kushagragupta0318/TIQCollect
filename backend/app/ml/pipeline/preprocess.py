# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Preprocessing as a fitted, picklable transformer rather
#   than a notebook cell.
#
#   MISSING VALUES ARE NOT IMPUTED. That is a deliberate departure from the
#   sklearn default reflex. In a WOE scorecard, "we have no bureau record for
#   this borrower" is information — on this book the missingness is partly MNAR,
#   concentrated on thin files — and imputing it to a median destroys the
#   signal AND tells the model something false. Missing survives to the binner,
#   which gives it its own bin and its own WOE. Every downstream step is built
#   to carry NaN, so nothing here fills one in.
# ───────────────────────────────────────────────────────────────────────────
"""
Preprocessing: outlier capping, rare-category grouping, type coercion.

Everything is FITTED ON TRAIN ONLY and applied unchanged to validation and
out-of-time. Percentile caps learned on the full frame would leak the holdout's
distribution into the training transformation, which is the quiet kind of leak
that never shows up as an error and inflates the holdout result.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class Preprocessor(BaseEstimator, TransformerMixin):
    """Cap numeric outliers, group rare categories, coerce types.

    Parameters
    ----------
    numeric, categorical
        Column names. Anything not named is dropped, so a frame that gains a
        column later cannot silently change the model's inputs.
    cap_lower, cap_upper
        Winsorising percentiles, learned on train.
    rare_threshold
        Category levels below this share of the training rows collapse to
        "__RARE__". A level seen 4 times cannot support a stable WOE, and an
        unseen level at scoring time has to land somewhere defined.
    """

    def __init__(self, numeric: list[str], categorical: list[str],
                 cap_lower: float = 0.01, cap_upper: float = 0.99,
                 rare_threshold: float = 0.05):
        self.numeric = list(numeric)
        self.categorical = list(categorical)
        self.cap_lower = cap_lower
        self.cap_upper = cap_upper
        self.rare_threshold = rare_threshold

    def fit(self, X: pd.DataFrame, y=None):
        self.caps_: dict[str, tuple[float, float]] = {}
        for c in self.numeric:
            if c not in X.columns:
                continue
            col = pd.to_numeric(X[c], errors="coerce")
            lo, hi = col.quantile([self.cap_lower, self.cap_upper])
            if not np.isfinite(lo):
                lo = col.min()
            if not np.isfinite(hi):
                hi = col.max()
            self.caps_[c] = (float(lo), float(hi))

        self.levels_: dict[str, list[str]] = {}
        n = max(len(X), 1)
        for c in self.categorical:
            if c not in X.columns:
                continue
            share = X[c].astype("string").fillna("__MISSING__").value_counts() / n
            self.levels_[c] = sorted(share[share >= self.rare_threshold].index.tolist())

        self.feature_names_ = [c for c in self.numeric + self.categorical
                               if c in X.columns]
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=X.index)
        for c in self.numeric:
            if c not in X.columns:
                continue
            col = pd.to_numeric(X[c], errors="coerce")
            lo, hi = self.caps_.get(c, (None, None))
            # NaN is preserved by clip; that is the intent — see the header.
            out[c] = col.clip(lo, hi) if lo is not None else col
        for c in self.categorical:
            if c not in X.columns:
                continue
            col = X[c].astype("string").fillna("__MISSING__")
            keep = set(self.levels_.get(c, []))
            out[c] = col.where(col.isin(keep), "__RARE__").astype("object")
        return out[self.feature_names_]

    def get_feature_names_out(self, input_features=None):
        return np.asarray(self.feature_names_, dtype=object)


class ColumnSubset(BaseEstimator, TransformerMixin):
    """Keep exactly these columns, in this order.

    Sits between the binner and the estimator so the fitted Pipeline carries the
    SELECTED feature set inside it. Without this the artifact would depend on a
    caller passing the right columns in the right order — which is the same
    class of failure as saving an estimator without its preprocessing, just one
    step later.
    """

    def __init__(self, columns: list[str]):
        self.columns = list(columns)

    def fit(self, X, y=None):
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.columns if c not in X.columns]
        if missing:
            raise KeyError(f"scoring frame is missing fitted columns: {missing}")
        return X[self.columns]

    def get_feature_names_out(self, input_features=None):
        return np.asarray(self.columns, dtype=object)
