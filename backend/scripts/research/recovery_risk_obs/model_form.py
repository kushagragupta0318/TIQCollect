"""Model-form check on one experiment's artifact: A WOE+LR (as trained), B
regularised LR on the same WOE columns (L1 and L2, C by validation), C GBM on
the selected raw features and on the VIF pool (early-stopped on validation).
KS everywhere, so the functional-form gap is quantified in the metric that
is short.

    python scripts/research/recovery_risk_obs/model_form.py OUT_DIR exp
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")
from app.ml.pipeline import evaluate as ev                     # noqa: E402
from app.ml.pipeline.binning import WOEBinner                  # noqa: E402

OUT, EXP = Path(sys.argv[1]), sys.argv[2]
r = json.loads((OUT / f"{EXP}.json").read_text())
art = OUT / "artifacts" / "recovery_risk" / EXP
meta = json.loads((art / "metadata.json").read_text())
panel = pd.read_parquet(BACKEND / "data" / "ledger" / f"obs_{r['world']}" / "panel.parquet")
months = np.sort(panel.month_index.unique()); n = len(months)
TR, VA, OOT = (panel[panel.month_index.isin(m)] for m in
               (months[:int(n * .6)], months[int(n * .6):int(n * .75)], months[int(n * .75):]))
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
sel = r["selected"]
spec = meta["spec"]
cats_all = set(spec["categorical_features"])
pool = [x["feature"] for x in pd.read_csv(art / "evaluation" / "vif.csv").to_dict("records") if x["action"] == "kept"]


def gk(y, p):
    ks, _ = ev.ks_statistic(y, p)
    return dict(gini=round(2 * roc_auc_score(y, p) - 1, 4), ks=round(ks, 2),
                brier=round(brier_score_loss(y, p), 5))


rows = []
# ── A: the artifact's own pipeline (uncalibrated LR on WOE, the ranking) ──
pipe, _ = joblib.load(art / "model.joblib"), None
pA = pipe.predict_proba(OOT)[:, 1]
rows.append(dict(form="A  WOE + LR (artifact)", k=len(sel), **gk(yoot, pA), valid_gini=r["valid"]["gini"]))

# ── B: regularised LR on the same WOE columns ─────────────────────────────
signs = {k: v for k, v in spec["expected_sign"].items()}
num = [f for f in sel if f not in cats_all]; cat = [f for f in sel if f in cats_all]
b = WOEBinner(num, cat, trends=WOEBinner.trends_from_signs(signs)).fit(TR[sel], ytr)
Wtr, Wva, Woot = b.transform(TR[sel]), b.transform(VA[sel]), b.transform(OOT[sel])
for pen, solver in (("l1", "liblinear"), ("l2", "lbfgs")):
    best = None
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0):
        m = LogisticRegression(penalty=pen, solver=solver, C=C, max_iter=5000).fit(Wtr, ytr)
        g = roc_auc_score(yva, m.predict_proba(Wva)[:, 1])
        if best is None or g > best[0]:
            best = (g, C, m)
    m = best[2]
    nz = int((np.abs(m.coef_[0]) > 1e-6).sum())
    rows.append(dict(form=f"B  {pen.upper()} LR on WOE, C={best[1]}", k=nz, **gk(yoot, m.predict_proba(Woot)[:, 1]),
                     valid_gini=round(2 * best[0] - 1, 4)))

# ── C: GBM, selected raw features and the VIF pool ────────────────────────
def frame(d, cols):
    X = d[cols].copy()
    for c in cols:
        if c in cats_all:
            X[c] = pd.Categorical(X[c].astype(str), categories=sorted(panel[c].astype(str).unique())).codes
    return X


def gbm(cols, label):
    p = dict(max_iter=800, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=200,
             l2_regularization=1.0, random_state=0, early_stopping=False)
    cf = [c in cats_all for c in cols]
    m = HistGradientBoostingClassifier(**p, categorical_features=cf).fit(frame(TR, cols), ytr)
    sc = [roc_auc_score(yva, q[:, 1]) for q in m.staged_predict_proba(frame(VA, cols))]
    it = int(np.argmax(sc)) + 1
    m = HistGradientBoostingClassifier(**{**p, "max_iter": it}, categorical_features=cf).fit(frame(TR, cols), ytr)
    rows.append(dict(form=f"C  GBM {label} (it={it})", k=len(cols), **gk(yoot, m.predict_proba(frame(OOT, cols))[:, 1]),
                     valid_gini=round(2 * max(sc) - 1, 4)))


gbm(sel, "on the selected features")
gbm(pool, "on the VIF pool")
t = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(f"{EXP} on world {r['world']} — OOT n={len(OOT):,}")
print(t.to_string(index=False))
a = t.iloc[0]; c_sel = t[t.form.str.contains("selected")].iloc[0]; c_pool = t[t.form.str.contains("pool")].iloc[0]
print(f"\nfunctional-form gap, same {len(sel)} inputs:  Gini {c_sel.gini - a.gini:+.4f}  KS {c_sel.ks - a.ks:+.2f}")
print(f"information gap, VIF pool vs selected (GBM):  Gini {c_pool.gini - c_sel.gini:+.4f}  KS {c_pool.ks - c_sel.ks:+.2f}")
(OUT / f"{EXP}_model_form.json").write_text(t.to_json(orient="records", indent=1))
