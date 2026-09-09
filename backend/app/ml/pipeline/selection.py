# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Feature selection as an ordered, logged pipeline rather
#   than a judgement call: IV filter -> correlation prune -> VIF -> forward
#   stepwise -> sign check.
#
#   EVERY STEP RECORDS WHAT IT REMOVED AND WHY. A selection that cannot be read
#   back is indistinguishable from one that picked at random, which is the same
#   argument ml/allocator.py makes for its own `explanations` payload. The log
#   goes into the model document verbatim.
# ───────────────────────────────────────────────────────────────────────────
"""
Feature selection for a WOE scorecard.

THE ORDER MATTERS AND IS NOT ARBITRARY
--------------------------------------
1. IV        drop what carries no information, flag what carries implausibly much
2. CORRELATION  of two features saying the same thing, keep the more informative
3. VIF       drop what is a linear combination of the others
4. SFS       forward stepwise; stop when the next feature buys nothing
5. SIGN      drop anything whose fitted direction contradicts the business

Correlation before VIF because pairwise redundancy is cheaper to resolve and
leaves VIF a smaller, better-conditioned problem. Sign check LAST because a
coefficient's sign is only meaningful in the presence of the other features that
survived — a feature can look correctly signed alone and flip under control,
and that flip is exactly what the check is for.

THE SIGN CHECK, UNDER THE WOE CONVENTION
----------------------------------------
WOE here is ln(good/bad): HIGH WOE = LOW RISK (verified on this book — the
lowest dpd bin has event rate 0.459 and WOE +0.981; the highest has 0.925 and
-1.702). Modelling y = 1 = bad, every WOE coefficient must therefore be
NEGATIVE. A positive one says "more evidence of good behaviour raises risk",
which is either a collinearity artefact or a sign the binning is wrong. It is
dropped however significant it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score


@dataclass
class SelectionResult:
    selected: list[str] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)
    iv_table: pd.DataFrame | None = None
    vif_table: pd.DataFrame | None = None
    corr_dropped: list[dict] = field(default_factory=list)
    sfs_path: list[dict] = field(default_factory=list)
    sign_dropped: list[dict] = field(default_factory=list)
    reviewed_high_iv: list[str] = field(default_factory=list)

    def record(self, step: str, kept: int, dropped: list[str], reason: str) -> None:
        self.log.append({"step": step, "kept": kept, "dropped": len(dropped),
                         "dropped_features": dropped, "reason": reason})


# ---------------------------------------------------------------------------
# Variance inflation factor
# ---------------------------------------------------------------------------

def compute_vif(X: pd.DataFrame) -> pd.Series:
    """VIF per column, computed by regressing each column on the others.

    statsmodels' variance_inflation_factor is O(k) separate OLS fits and is
    happy to return inf on a perfectly collinear pair. This uses the inverse
    correlation matrix instead — VIF_i is the i-th diagonal of R^-1 — which is
    one decomposition for the whole set and degrades to a large finite number
    rather than inf when the matrix is near-singular.
    """
    cols = list(X.columns)
    if len(cols) < 2:
        return pd.Series(1.0, index=cols)
    R = np.corrcoef(X.to_numpy(dtype=float), rowvar=False)
    R = np.nan_to_num(R, nan=0.0)
    np.fill_diagonal(R, 1.0)
    try:
        Rinv = np.linalg.pinv(R)
    except np.linalg.LinAlgError:               # pragma: no cover
        return pd.Series(np.inf, index=cols)
    return pd.Series(np.clip(np.diag(Rinv), 1.0, 1e6), index=cols)


# ---------------------------------------------------------------------------
# The selector
# ---------------------------------------------------------------------------

def select_features(
    X_woe: pd.DataFrame,
    y: pd.Series,
    iv: dict[str, float],
    gates,
    *,
    max_features: int = 15,
    min_gini_gain: float = 0.0008,
    valid: tuple[pd.DataFrame, pd.Series] | None = None,
    random_state: int = 0,
) -> SelectionResult:
    """Run the five steps. `X_woe` columns are named '<feature>_woe'.

    ON min_gini_gain, AND WHY THE CARD IS SHORT. Measured on the recovery book,
    the stopping threshold barely matters:

        gain >= 0.002    k=3   oot Gini 0.5149
        gain >= 0.0008   k=4   oot Gini 0.5149
        gain >= 0.0003   k=4   oot Gini 0.5149
        gain >= 0.0001   k=5   oot Gini 0.5160

    Two extra features buy 0.001. That is not the stopping rule being harsh, it
    is the correlation and VIF steps having already collapsed an entire block of
    delinquency measures — arrears_ratio, overdue_amount, penal_ratio,
    total_outstanding — into `dpd`, which carries the same information with less
    noise. A short scorecard here is the honest result, not a truncated one; the
    selection log records every feature that went and which survivor displaced
    it.
    """
    res = SelectionResult()
    y = np.asarray(y).astype(int)

    def raw(c: str) -> str:
        return c[:-4] if c.endswith("_woe") else c

    # ── 1. IV filter ────────────────────────────────────────────────────────
    cols = list(X_woe.columns)
    weak = [c for c in cols if iv.get(raw(c), 0.0) < gates.iv_min]
    absurd = [c for c in cols if iv.get(raw(c), 0.0) > gates.iv_max]
    res.reviewed_high_iv = sorted(
        raw(c) for c in cols
        if gates.iv_review < iv.get(raw(c), 0.0) <= gates.iv_max)
    cols = [c for c in cols if c not in set(weak) | set(absurd)]
    res.record("iv_filter", len(cols), [raw(c) for c in weak + absurd],
               f"IV outside [{gates.iv_min}, {gates.iv_max}]; "
               f"{len(res.reviewed_high_iv)} flagged for review above {gates.iv_review}")

    # ── 2. Correlation prune ────────────────────────────────────────────────
    corr = X_woe[cols].corr().abs()
    dropped_corr: list[str] = []
    ordered = sorted(cols, key=lambda c: -iv.get(raw(c), 0.0))
    for i, a in enumerate(ordered):
        if a in dropped_corr:
            continue
        for b in ordered[i + 1:]:
            if b in dropped_corr:
                continue
            r = corr.loc[a, b]
            if r > gates.corr_max:
                dropped_corr.append(b)
                res.corr_dropped.append({
                    "dropped": raw(b), "kept": raw(a), "abs_corr": round(float(r), 4),
                    "iv_dropped": round(iv.get(raw(b), 0.0), 4),
                    "iv_kept": round(iv.get(raw(a), 0.0), 4),
                })
    cols = [c for c in cols if c not in dropped_corr]
    res.record("correlation", len(cols), [raw(c) for c in dropped_corr],
               f"|r| > {gates.corr_max}; the lower-IV member of each pair goes")

    # ── 3. VIF ──────────────────────────────────────────────────────────────
    vif_rows = []
    while len(cols) > 1:
        v = compute_vif(X_woe[cols])
        worst = v.idxmax()
        if v[worst] <= gates.vif_max:
            break
        vif_rows.append({"feature": raw(worst), "vif": round(float(v[worst]), 3),
                         "action": "dropped"})
        cols.remove(worst)
    final_vif = compute_vif(X_woe[cols])
    for c in cols:
        vif_rows.append({"feature": raw(c), "vif": round(float(final_vif[c]), 3),
                         "action": "kept"})
    res.vif_table = pd.DataFrame(vif_rows)
    res.record("vif", len(cols),
               [r["feature"] for r in vif_rows if r["action"] == "dropped"],
               f"VIF > {gates.vif_max}, dropped one at a time worst-first")

    # ── 4. Forward stepwise ─────────────────────────────────────────────────
    # Scored on a held-out slice when one is given, so the stopping rule is not
    # measuring the model's ability to memorise the set it is being chosen on.
    Xv, yv = valid if valid is not None else (X_woe, pd.Series(y, index=X_woe.index))
    yv = np.asarray(yv).astype(int)

    def gini_with(feats: list[str]) -> float:
        m = LogisticRegression(max_iter=1000, random_state=random_state)
        m.fit(X_woe[feats], y)
        p = m.predict_proba(Xv[feats])[:, 1]
        return 2 * roc_auc_score(yv, p) - 1

    chosen: list[str] = []
    remaining = list(cols)
    best_gini = 0.0
    while remaining and len(chosen) < max_features:
        scored = [(gini_with(chosen + [c]), c) for c in remaining]
        scored.sort(reverse=True)
        gain = scored[0][0] - best_gini
        if gain < min_gini_gain:
            res.sfs_path.append({"step": len(chosen) + 1, "candidate": raw(scored[0][1]),
                                 "gini": round(scored[0][0], 4),
                                 "gain": round(gain, 5), "action": "stopped"})
            break
        best_gini, pick = scored[0]
        chosen.append(pick)
        remaining.remove(pick)
        res.sfs_path.append({"step": len(chosen), "candidate": raw(pick),
                             "gini": round(best_gini, 4),
                             "gain": round(gain, 5), "action": "added"})
    res.record("sfs", len(chosen), [raw(c) for c in cols if c not in chosen],
               f"forward stepwise, stop when validation Gini gain < {min_gini_gain}")

    # ── 5. Sign check ───────────────────────────────────────────────────────
    # Iterative: dropping one wrong-signed feature can correct another.
    while chosen:
        m = LogisticRegression(max_iter=1000, random_state=random_state)
        m.fit(X_woe[chosen], y)
        coefs = dict(zip(chosen, m.coef_[0]))
        wrong = [c for c, b in coefs.items() if b > 0]
        if not wrong:
            break
        worst = max(wrong, key=lambda c: coefs[c])
        res.sign_dropped.append({
            "feature": raw(worst), "coefficient": round(float(coefs[worst]), 5),
            "reason": ("positive coefficient on a WOE feature means more evidence "
                       "of good behaviour raises modelled risk — inadmissible"),
        })
        chosen.remove(worst)
    res.record("sign_check", len(chosen), [d["feature"] for d in res.sign_dropped],
               "every WOE coefficient must be negative (high WOE = low risk)")

    res.selected = chosen
    res.iv_table = pd.DataFrame(
        [{"feature": raw(c), "iv": round(iv.get(raw(c), 0.0), 4),
          "selected": c in chosen} for c in X_woe.columns]
    ).sort_values("iv", ascending=False)
    return res
