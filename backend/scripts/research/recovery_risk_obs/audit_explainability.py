"""Section 9 — explainability of the audited GAM, proved rather than asserted.

    python scripts/research/recovery_risk_obs/audit_explainability.py OUT_DIR

For an additive model plus declared pairs the decomposition is EXACT for any
fixed background distribution B:

    F(x) = E_B[F] + sum_j g_j(x_j) + sum_pairs r_ab(x_a, x_b)
    g_j(v)      = E_B[F | j := v] - E_B[F]
    r_ab(u, v)  = E_B[F | a := u, b := v] - E_B[F] - g_a(u) - g_b(v)

(the pair residual is what the earlier mean-substitution write-up left over as
"any difference is the interaction terms" — it is attributed here, so the parts
sum to the logit to floating-point precision rather than approximately).

Reports: per-borrower contribution tables, the max |sum - logit| over a sample,
the probability transform, and what is MISSING for production explainability.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")

OUT = Path(sys.argv[1])
ART = OUT / "gam" / "audit"
bundle = joblib.load(ART / "gam_exp5.joblib")
meta = json.loads((ART / "gam_exp5_metadata.json").read_text())
m, COLS, PAIRS, LEVELS = bundle["model"], bundle["features"], bundle["pairs"], bundle["cat_levels"]
panel = pd.read_parquet(BACKEND / "data" / "ledger" / "obs_wd10" / "panel.parquet")
months = np.sort(panel.month_index.unique()); n = len(months)
TR = panel[panel.month_index.isin(months[:int(n * .6)])]
OOT = panel[panel.month_index.isin(months[int(n * .75):])]


def frame(d):
    X = d[COLS].copy()
    for c, lv in LEVELS.items():
        X[c] = pd.Categorical(X[c].astype(str), categories=lv).codes
    return X


BG = frame(TR.sample(2000, random_state=0))
BASE = float(m.decision_function(BG).mean())
print(f"background 2,000 train rows (seed 0); E_B[logit] = {BASE:+.6f}")


def contributions(row: pd.Series) -> tuple[dict, float, float]:
    X = frame(row.to_frame().T)
    logit = float(m.decision_function(X)[0])
    g = {}
    for c in COLS:
        B = BG.copy(); B[c] = X[c].iloc[0]
        g[c] = float(m.decision_function(B).mean()) - BASE
    parts = dict(g)
    for a, b in PAIRS:
        B = BG.copy(); B[a] = X[a].iloc[0]; B[b] = X[b].iloc[0]
        gab = float(m.decision_function(B).mean()) - BASE
        parts[f"{a} x {b}"] = gab - g[a] - g[b]
    return parts, logit, BASE + sum(parts.values())


sample = OOT.sample(60, random_state=1)
errs, rows = [], []
for _, r in sample.iterrows():
    parts, logit, total = contributions(r)
    errs.append(abs(total - logit))
    rows.append({"loan_id": r.loan_id, "logit": logit, "sum": total, "err": abs(total - logit)})
errs = np.array(errs)
print(f"\nEXACTNESS over {len(sample)} OOT borrowers: max |intercept + contributions - logit| = {errs.max():.3e}, "
      f"mean {errs.mean():.3e}")
assert errs.max() < 1e-9, "decomposition is not exact"

# probability transform
Xs = frame(sample)
lg = m.decision_function(Xs); pr = m.predict_proba(Xs)[:, 1]
sig = 1.0 / (1.0 + np.exp(-lg))
print(f"probability transform: max |sigmoid(logit) - predict_proba| = {np.max(np.abs(sig - pr)):.3e}")

# two worked examples, high and low risk
print("\n" + "=" * 78 + "\nWORKED EXAMPLES (production explanation shape)\n" + "=" * 78)
examples = {}
order = np.argsort(lg)
for label, i in (("LOWEST-risk of the sample", order[0]), ("HIGHEST-risk of the sample", order[-1])):
    r = sample.iloc[i]
    parts, logit, total = contributions(r)
    p = 1 / (1 + np.exp(-logit))
    print(f"\n{label}: loan {r.loan_id}, as_of {r.as_of_date}")
    print(f"  P(no material payment) = {p:.4f}   logit {logit:+.4f}")
    print(f"  intercept (background mean logit)          {BASE:+.4f}")
    for k, v in sorted(parts.items(), key=lambda kv: -abs(kv[1])):
        val = r[k] if k in r else "(pair)"
        print(f"  {k:<34} = {str(val)[:18]:<18} {v:+.4f}")
    print(f"  {'TOTAL':<34}   {'':<18} {total:+.4f}   (logit {logit:+.4f}, "
          f"error {abs(total - logit):.2e})")
    examples[label] = {"loan_id": r.loan_id, "probability": p, "logit": logit,
                       "intercept": BASE, "contributions": parts}

# determinism
a, _, _ = contributions(sample.iloc[0])
b, _, _ = contributions(sample.iloc[0])
det = max(abs(a[k] - b[k]) for k in a)
print(f"\ndeterminism: same row explained twice, max |diff| = {det:.3e}")

# what production cannot yet do
print("\nMISSING for production explainability:")
gaps = []
if not meta.get("bands"):
    gaps.append("no risk-band table: the WOE pipeline fits bands from the development score "
                "distribution (ScoreCard.bands); this GAM has none, so a served row could carry a "
                "probability and points but no band")
gaps.append("no reason codes: the engine derives them from scorecard points per feature; the "
            "equivalent here is the top-k contributions above, which nothing computes at serve time")
gaps.append("the decomposition needs the frozen background sample shipped with the artifact "
            "(2,000 train rows, seed 0) — it is a property of the explanation, not of the model, "
            "and must be versioned with it or explanations will drift")
for g in gaps:
    print(f"  - {g}")

json.dump({"background_rows": 2000, "background_seed": 0, "base_logit": BASE,
           "max_abs_decomposition_error": float(errs.max()),
           "mean_abs_decomposition_error": float(errs.mean()),
           "max_abs_sigmoid_error": float(np.max(np.abs(sig - pr))),
           "determinism_max_diff": float(det), "examples": examples, "gaps": gaps,
           "per_row": rows}, open(ART / "explainability.json", "w"), indent=1, default=str)
print("\nwritten", ART / "explainability.json")
