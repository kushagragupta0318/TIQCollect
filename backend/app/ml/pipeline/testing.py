# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. The tests that run AFTER a model looks good, which is
#   exactly when they matter.
#
#   A model that clears its gates has told you it discriminates. It has not told
#   you whether it discriminates because it learned the borrower or because it
#   was handed the answer, whether the holdout is even the same population, or
#   whether the number would survive a different sample. These four checks —
#   leakage sniff, adversarial validation, bootstrap confidence, ablation — are
#   what separate "the metric was good" from "the model is real".
# ───────────────────────────────────────────────────────────────────────────
"""
Adversarial and stability testing.

LEAKAGE SNIFF
    A leaked feature knows the outcome for the specific row. Break the row-level
    link by shuffling the target WITHIN a time period and refitting: a genuine
    predictor keeps most of its power (the population relationship survives), a
    leaked one collapses toward zero because its power was row-specific. Reported
    as a retention ratio, not a verdict — it is evidence for a human.

ADVERSARIAL VALIDATION
    Train a classifier to tell development rows from out-of-time rows. If it can
    (AUC > ~0.70), the two are different populations and the out-of-time Gini is
    measuring transfer as much as skill. That is not necessarily fatal — books
    genuinely drift — but it must be known, and PSI alone can miss a shift that
    lives in the interaction of several features.

BOOTSTRAP
    A Gini is a point estimate on one sample. The confidence interval is what
    says whether 0.53 and 0.49 are different models or the same model twice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import cross_val_score

from app.ml.pipeline.evaluate import gini


def leakage_sniff(X: pd.DataFrame, y: pd.Series, period: pd.Series,
                  features: list[str], *, random_state: int = 0) -> pd.DataFrame:
    """Per-feature AUC before and after shuffling y within each period.

    Retention near 1.0 means the feature's signal is a population-level
    relationship that survives the row-level link being cut — normal. Retention
    near 0 means the feature only worked row-by-row, which is what a leak looks
    like.
    """
    rng = np.random.default_rng(random_state)
    y = pd.Series(np.asarray(y).astype(int), index=X.index)
    y_shuf = y.copy()
    for _, idx in y.groupby(period.to_numpy()).groups.items():
        vals = y.loc[idx].to_numpy().copy()
        rng.shuffle(vals)
        y_shuf.loc[idx] = vals

    # NUMERIC FEATURES ONLY. A rank-based AUC needs an ordering, and a
    # categorical has none that is independent of the target — encoding one by
    # its event rate would put y into the very quantity the shuffle is meant to
    # break, making the test measure itself. Categoricals are covered instead by
    # their binning tables, where a leak shows up as a bin that is 100% one
    # class. Skipped features are returned in the frame so the omission is
    # visible rather than silent.
    rows = []
    skipped = []
    for f in features:
        col = X[f]
        if not pd.api.types.is_numeric_dtype(col):
            skipped.append(f)
            rows.append({"feature": f, "gini_real": np.nan,
                         "gini_time_shuffled": np.nan, "retention": np.nan,
                         "note": "categorical — not rank-testable, see binning table"})
            continue
        med = col.median() if col.notna().any() else 0.0
        v = pd.to_numeric(col, errors="coerce").fillna(med)
        try:
            a_real = abs(2 * roc_auc_score(y, v) - 1)
            a_shuf = abs(2 * roc_auc_score(y_shuf, v) - 1)
        except ValueError:                                 # pragma: no cover
            continue
        rows.append({
            "feature": f,
            "gini_real": round(a_real, 4),
            "gini_time_shuffled": round(a_shuf, 4),
            "retention": round(a_shuf / a_real, 3) if a_real > 1e-9 else np.nan,
            "note": "",
        })
    return pd.DataFrame(rows).sort_values("gini_real", ascending=False,
                                          na_position="last")


def adversarial_validation(dev: pd.DataFrame, oot: pd.DataFrame,
                           features: list[str], *, random_state: int = 0) -> dict:
    """Can a model tell the development sample from the out-of-time sample?"""
    common = [f for f in features if f in dev.columns and f in oot.columns]
    X = pd.concat([dev[common], oot[common]], ignore_index=True)
    X = X.apply(pd.to_numeric, errors="coerce")
    y = np.r_[np.zeros(len(dev)), np.ones(len(oot))]
    m = HistGradientBoostingClassifier(max_iter=120, max_depth=4,
                                       random_state=random_state)
    auc = float(cross_val_score(m, X, y, cv=3, scoring="roc_auc").mean())
    return {
        "auc": round(auc, 4),
        "verdict": ("indistinguishable" if auc < 0.60 else
                    "mild drift" if auc < 0.70 else "different populations"),
        "note": ("AUC near 0.5 means development and out-of-time look like the "
                 "same book. Above 0.70 the out-of-time result is measuring "
                 "transfer across a shift as much as it is measuring skill."),
    }


def bootstrap_gini(y_true, score, *, n_boot: int = 300,
                   random_state: int = 0) -> dict:
    """Percentile confidence interval on Gini, by resampling rows."""
    rng = np.random.default_rng(random_state)
    y = np.asarray(y_true).astype(int)
    s = np.asarray(score, dtype=float)
    n = len(y)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        if len(np.unique(y[idx])) < 2:
            continue
        vals.append(2 * roc_auc_score(y[idx], s[idx]) - 1)
    if not vals:                                            # pragma: no cover
        return {"point": float("nan")}
    v = np.asarray(vals)
    return {
        "point": round(float(gini(y, s)), 4),
        "mean": round(float(v.mean()), 4),
        "std": round(float(v.std()), 4),
        "ci_lower_2.5": round(float(np.percentile(v, 2.5)), 4),
        "ci_upper_97.5": round(float(np.percentile(v, 97.5)), 4),
        "n_bootstrap": len(vals),
    }


def ablation(X_woe: pd.DataFrame, y, features: list[str], X_eval, y_eval,
             *, random_state: int = 0) -> pd.DataFrame:
    """Gini with each feature removed in turn. What is each one actually worth?

    Reported against the full model, so a negative delta means the model is
    BETTER without that feature — which happens, and is worth seeing before a
    committee asks.
    """
    def fit_gini(feats):
        if not feats:
            return 0.0
        m = LogisticRegression(max_iter=1000, random_state=random_state)
        m.fit(X_woe[feats], y)
        return gini(y_eval, m.predict_proba(X_eval[feats])[:, 1])

    full = fit_gini(features)
    rows = [{"removed": "(none — full model)", "gini": round(full, 4), "delta": 0.0}]
    for f in features:
        g = fit_gini([c for c in features if c != f])
        rows.append({"removed": f.removesuffix("_woe"), "gini": round(g, 4),
                     "delta": round(g - full, 4)})
    return pd.DataFrame(rows).sort_values("delta")
