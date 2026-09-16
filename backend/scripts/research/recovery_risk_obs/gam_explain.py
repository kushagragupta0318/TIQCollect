"""Interpretability of a fitted GAM from gam_ladder.py (Section 7 of the brief).

    python scripts/research/recovery_risk_obs/gam_explain.py OUT_DIR exp3

For an additive boosted model the partial dependence of each feature IS its
shape function up to a constant, and a borrower's logit decomposes exactly
into intercept + sum of per-feature contributions (+ pair terms). Reports:
shape tables (value grid -> logit contribution), direction, contribution
range, monotonicity against the declared sign, where missing values are
routed, the 2-D table of each allowed interaction, and one borrower's
score decomposition.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

OUT, EXP = Path(sys.argv[1]), sys.argv[2]
models = joblib.load(OUT / "gam" / "models.joblib")
m, cols, pairs = models[EXP]
ladder = {r["model"]: r for r in json.loads((OUT / "gam" / "ladder.json").read_text())}
meta = json.loads((OUT / "artifacts" / "recovery_risk" / "f1" / "metadata.json").read_text())
SIGNS = meta["spec"]["expected_sign"]
CATS = set(meta["spec"]["categorical_features"]) | {"disposition_recency_class", "disposition_7d", "disposition_30d"}
panel = pd.read_parquet(BACKEND / "data" / "ledger" / "obs_wd10" / "panel.parquet")
months = np.sort(panel.month_index.unique()); n = len(months)
TR = panel[panel.month_index.isin(months[:int(n * .6)])]
CAT_LEVELS = {c: sorted(panel[c].astype(str).unique()) for c in cols if c in CATS}


def frame(d):
    X = d[cols].copy()
    for c in cols:
        if c in CATS:
            X[c] = pd.Categorical(X[c].astype(str), categories=CAT_LEVELS[c]).codes
    return X


Xtr = frame(TR)
base = m.predict(Xtr, )  # unused; decision_function is the logit
logit_tr = m.decision_function(Xtr)
intercept = float(logit_tr.mean())


def shape(c, grid=None):
    """Contribution f_c(v) - mean f_c over train, by substituting v for the
    whole train column (exact for an additive model, and for a pair member
    it averages over the partner)."""
    j = cols.index(c)
    X = Xtr.copy()
    if c in CATS:
        values = list(range(len(CAT_LEVELS[c]))); labels = CAT_LEVELS[c]
    else:
        col = TR[c].dropna()
        values = list(np.unique(np.quantile(col, np.linspace(0.02, 0.98, 13)).round(4))) + [np.nan]
        labels = [f"{v:g}" if not (isinstance(v, float) and np.isnan(v)) else "Missing" for v in values]
    out = []
    for v, lab in zip(values, labels):
        X[c] = v
        out.append((lab, float(m.decision_function(X).mean() - intercept)))
    return out


report = {"exp": EXP, "features": cols, "interactions": [f"{a} x {b}" for a, b in pairs],
          "intercept_logit_train_mean": round(intercept, 4), "shapes": {}, "monotone_ok": {}, "missing_routing": {}}
print(f"{EXP}: {len(cols)} features, interactions {report['interactions']}, iterations {m.n_iter_}")
print(f"mean train logit (intercept) {intercept:+.3f}\n")
for c in cols:
    sh = shape(c)
    report["shapes"][c] = sh
    vals = [v for lab, v in sh if lab != "Missing"]
    rng = max(vals) - min(vals)
    sign = SIGNS.get(c, 0)
    if c not in CATS:
        d = np.diff(vals)
        mono = "ascending" if np.all(d >= -1e-9) else "descending" if np.all(d <= 1e-9) else "NON-MONOTONE"
        ok = (sign == 0) or (sign > 0 and mono == "ascending") or (sign < 0 and mono == "descending")
        report["monotone_ok"][c] = bool(ok)
        miss = [v for lab, v in sh if lab == "Missing"]
        report["missing_routing"][c] = round(miss[0], 3) if miss and TR[c].isna().any() else None
        print(f"-- {c:<30} sign {sign:+d}  shape {mono:<12} range {rng:.3f} logits  "
              f"{'OK' if ok else 'VIOLATION'}   missing -> {report['missing_routing'][c]}")
    else:
        print(f"-- {c:<30} categorical            range {rng:.3f} logits")
    for lab, v in sh:
        print(f"      {lab:<24} {v:+.3f}")
print()
for a, b in pairs:
    ia, ib = cols.index(a), cols.index(b)
    X = Xtr.copy()
    la = CAT_LEVELS[a] if a in CATS else [f"{q:g}" for q in np.quantile(TR[a].dropna(), [.1, .3, .5, .7, .9])]
    va = list(range(len(la))) if a in CATS else list(np.quantile(TR[a].dropna(), [.1, .3, .5, .7, .9]))
    lb = CAT_LEVELS[b] if b in CATS else [f"{q:g}" for q in np.quantile(TR[b].dropna(), [.1, .3, .5, .7, .9])]
    vb = list(range(len(lb))) if b in CATS else list(np.quantile(TR[b].dropna(), [.1, .3, .5, .7, .9]))
    tab = pd.DataFrame(index=la, columns=lb, dtype=float)
    for i, x in enumerate(va):
        for j_, y_ in enumerate(vb):
            X[a] = x; X[b] = y_
            tab.iloc[i, j_] = m.decision_function(X).mean() - intercept
    print(f"interaction {a} x {b}: joint contribution (logit), rows={a}, cols={b}")
    print(tab.round(3).to_string()); print()
    report[f"interaction_{a}_x_{b}"] = tab.round(3).to_dict()

# one borrower, decomposed: contributions sum to the logit exactly for the
# additive part; pair terms are attributed to the pair jointly.
i = int(np.argsort(logit_tr)[len(logit_tr) // 2])   # the median-risk borrower in train
row = Xtr.iloc[[i]]
total = float(m.decision_function(row)[0])
parts = {}
for c in cols:
    Xi = Xtr.copy(); Xi[c] = row[c].iloc[0]
    parts[c] = float(m.decision_function(Xi).mean() - intercept)
print(f"one borrower (train row {i}): logit {total:+.3f}  P(no material payment) {1/(1+np.exp(-total)):.3f}")
print(f"  intercept {intercept:+.3f}")
for c, v in sorted(parts.items(), key=lambda kv: -abs(kv[1])):
    val = TR[c].iloc[i]
    print(f"  {c:<30} = {str(val):<14} contributes {v:+.3f}")
print(f"  (additive parts sum to {intercept + sum(parts.values()):+.3f}; any difference is the interaction terms)")
report["example"] = {"row": i, "logit": total, "parts": parts}
(OUT / "gam" / f"{EXP}_explain.json").write_text(json.dumps(report, indent=1, default=str))
