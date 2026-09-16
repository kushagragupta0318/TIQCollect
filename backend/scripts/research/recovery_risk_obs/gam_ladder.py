"""The GAM ladder (final brief, 2026-09-15): an interpretable additive model
on the FROZEN wd10 world, selection on train + validation only.

    python scripts/research/recovery_risk_obs/gam_ladder.py OUT_DIR

GAM = boosted shape functions: HistGradientBoosting with `interaction_cst`
restricted to singletons (every tree splits on ONE feature, so the model is
exactly additive: logit = b0 + sum_j f_j(x_j)), monotone constraints from the
spec's expected signs, 32 knots per feature (`max_bins`), min 300 rows per
leaf, L2 = 1, learning rate 0.05, iterations chosen on validation. Missing
values are routed by each shape function to a learned side (reported).
Categoricals are native. Interactions are explicit allowed pairs only.

Ladder: EXP0 WOE-LR (the f1 artifact) -> EXP1 GAM on its 10 features ->
EXP2 + best single interaction -> EXP3 + up to 3 -> EXP4 GAM on the VIF pool
-> EXP5 GAM with a validation forward selection over the universe. GBM
benchmark on the same universe. Selection metric: validation KS, then Gini,
then simplicity. OOT is computed for every row of the table but is not what
chooses the specification.
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
from sklearn.inspection import partial_dependence
from sklearn.metrics import brier_score_loss, roc_auc_score

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")
from app.ml.pipeline import evaluate as ev                                 # noqa: E402
from app.ml.pipeline.config import FEED_ONLY_FEATURES, NO_HISTORY_FEATURES  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gam_common import interaction_cst_for                                 # noqa: E402

OUT = Path(sys.argv[1]); (OUT / "gam").mkdir(parents=True, exist_ok=True)
WORLD = "wd10"
ART = OUT / "artifacts" / "recovery_risk" / "f1"          # EXP0: the WOE-LR baseline
ART_POOL = OUT / "artifacts" / "recovery_risk" / "f4"     # the 43-feature VIF pool
meta = json.loads((ART / "metadata.json").read_text())
spec = meta["spec"]
SIGNS = dict(spec["expected_sign"])
panel = pd.read_parquet(BACKEND / "data" / "ledger" / f"obs_{WORLD}" / "panel.parquet")
CATS = set(spec["categorical_features"]) | {c for c in panel.columns if panel[c].dtype == object
                                            and c not in ("loan_id", "borrower_id")}
months = np.sort(panel.month_index.unique()); n = len(months)
TR = panel[panel.month_index.isin(months[:int(n * .6)])]
VA = panel[panel.month_index.isin(months[int(n * .6):int(n * .75)])]
OOT = panel[panel.month_index.isin(months[int(n * .75):])]
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
SEL10 = meta["selected_features"]
POOL = [r["feature"] for r in pd.read_csv(ART_POOL / "evaluation" / "vif.csv").to_dict("records") if r["action"] == "kept"]
NOT = {"loan_id", "borrower_id", "as_of_date", "month_index", "y", "recovered_amount", "outcome_threshold",
       "baseline_overdue_amount", "baseline_emi_amount"} | set(FEED_ONLY_FEATURES) | set(NO_HISTORY_FEATURES) | {
    "monthly_income", "dti_ratio", "credit_vintage_months", "num_open_loans", "num_enquiries_6m", "other_lender_delinq",
    "utilization_pct", "mail_returned_count", "address_vintage_months", "phone_verified", "thin_file", "sourcing_channel",
    "residence_type", "is_secured"}
UNIVERSE = [c for c in panel.columns if c not in NOT]
IV = pd.read_csv(ART_POOL / "eda" / "information_value.csv").set_index("feature").iv.to_dict()
IV.update(pd.read_csv(ART / "eda" / "information_value.csv").set_index("feature").iv.to_dict())
PSI_F = pd.read_csv(ART_POOL / "evaluation" / "psi.csv").set_index("feature").psi.to_dict()
VIF = pd.read_csv(ART_POOL / "evaluation" / "vif.csv").set_index("feature").vif.to_dict()
CAT_LEVELS = {c: sorted(panel[c].astype(str).unique()) for c in UNIVERSE if c in CATS}


def frame(d, cols):
    X = d[cols].copy()
    for c in cols:
        if c in CATS:
            X[c] = pd.Categorical(X[c].astype(str), categories=CAT_LEVELS[c]).codes
    return X


def metrics(y, p, d=None):
    ks, _ = ev.ks_statistic(y, p)
    out = dict(gini=round(2 * roc_auc_score(y, p) - 1, 4), auc=round(roc_auc_score(y, p), 4), ks=round(ks, 2))
    if d is not None:
        dt = ev.decile_table(y, p)
        out.update(brier=round(brier_score_loss(y, p), 5), cal_gap=round(float(p.mean() - y.mean()), 4),
                   breaks=int(ev.rank_order_breaks(dt)["n_breaks"]),
                   lift=round(float(dt.iloc[0].lift), 3))
        seg = ev.segment_performance(y, p, d["dpd_bucket"].reset_index(drop=True))
        out["min_seg_gini"] = round(float(seg.gini.min()), 4)
    return out


def fit_gam(cols, pairs=(), *, gbm=False, max_iter=1500):
    """pairs: list of (a, b) feature-name tuples allowed to interact.

    2026-09-16 — the constraint is built by `gam_common.interaction_cst_for`,
    which expresses it in the REMAPPED index space sklearn actually applies it
    in. Before that fix the declared pair landed on two different features;
    see that module's header for the measurement.
    """
    if gbm:
        cst, leaves, depth = None, 15, None
    else:
        cst = interaction_cst_for(cols, CATS, pairs)
        leaves, depth = (3 if not pairs else 4), (1 if not pairs else 2)
    mono = [0 if c in CATS else int(SIGNS.get(c, 0)) for c in cols]
    p = dict(learning_rate=0.05, max_leaf_nodes=leaves, max_depth=depth, min_samples_leaf=300,
             l2_regularization=1.0, max_bins=32 if not gbm else 255, random_state=0, early_stopping=False,
             categorical_features=[c in CATS for c in cols], monotonic_cst=mono, interaction_cst=cst)
    m = HistGradientBoostingClassifier(**p, max_iter=max_iter).fit(frame(TR, cols), ytr)
    sc = [metrics(yva, q[:, 1])["ks"] for q in m.staged_predict_proba(frame(VA, cols))]
    it = int(np.argmax(sc)) + 1
    m = HistGradientBoostingClassifier(**p, max_iter=it).fit(frame(TR, cols), ytr)
    return m, it


def evaluate(name, cols, pairs, m, it, extra=None):
    ptr, pva, poot = (m.predict_proba(frame(d, cols))[:, 1] for d in (TR, VA, OOT))
    r = dict(model=name, k=len(cols), interactions=[f"{a} x {b}" for a, b in pairs], iterations=it,
             train=metrics(ytr, ptr), valid=metrics(yva, pva), oot=metrics(yoot, poot, OOT),
             score_psi=round(ev.psi(ptr, poot), 4),
             iv_range=f"{min(IV.get(c, np.nan) for c in cols):.3f}-{max(IV.get(c, np.nan) for c in cols):.3f}",
             max_vif=round(max((VIF[c] for c in cols if c in VIF), default=float("nan")), 3),
             max_csi=round(max(PSI_F.get(c, 0.0) for c in cols), 4), features=cols)
    if extra: r.update(extra)
    print(f"{name:<44} k={len(cols):<3} it={it:<4} valid G {r['valid']['gini']:.4f} KS {r['valid']['ks']:5.2f} | "
          f"OOT G {r['oot']['gini']:.4f} KS {r['oot']['ks']:5.2f} AUC {r['oot']['auc']:.4f} Brier {r['oot']['brier']} "
          f"gap {r['oot']['cal_gap']:+} breaks {r['oot']['breaks']} lift {r['oot']['lift']} minseg {r['oot']['min_seg_gini']} "
          f"PSI {r['score_psi']} CSI {r['max_csi']}", flush=True)
    return r


rows = []
# ── EXP0: the WOE-LR baseline, from the artifact ──────────────────────────
pipe = joblib.load(ART / "model.joblib")
ALLF = list(spec["numeric_features"]) + list(spec["categorical_features"])
p0 = {k: pipe.predict_proba(d[ALLF])[:, 1] for k, d in (("tr", TR), ("va", VA), ("oot", OOT))}
r0 = dict(model="EXP0 WOE-LR baseline (f1 artifact)", k=len(SEL10), interactions=[], iterations=None,
          train=metrics(ytr, p0["tr"]), valid=metrics(yva, p0["va"]), oot=metrics(yoot, p0["oot"], OOT),
          score_psi=round(ev.psi(p0["tr"], p0["oot"]), 4),
          iv_range=f"{min(IV[c] for c in SEL10):.3f}-{max(IV[c] for c in SEL10):.3f}",
          max_vif=round(max(VIF[c] for c in SEL10 if c in VIF), 3), max_csi=round(max(PSI_F.get(c, 0) for c in SEL10), 4),
          features=SEL10)
print(f"{r0['model']:<44} k={len(SEL10):<3}         valid G {r0['valid']['gini']:.4f} KS {r0['valid']['ks']:5.2f} | OOT G {r0['oot']['gini']:.4f} KS {r0['oot']['ks']:5.2f}")
rows.append(r0)

# ── EXP1: GAM on the same 10 features ─────────────────────────────────────
m1, it1 = fit_gam(SEL10)
rows.append(evaluate("EXP1 GAM, 10 features, additive", SEL10, [], m1, it1))

# ── EXP2: best single interaction, chosen on VALIDATION ───────────────────
cands = [("latest_disposition", "arrears_ratio"), ("latest_disposition", "dpd"),
         ("latest_disposition", "days_since_disposition")]
trial = {}
for a, b in cands:
    cols = list(dict.fromkeys(SEL10 + [a, b]))          # the pair's members must be inputs
    m, it = fit_gam(cols, [(a, b)])
    pva = m.predict_proba(frame(VA, cols))[:, 1]
    trial[(a, b)] = (metrics(yva, pva), cols, m, it)
    print(f"   interaction trial {a} x {b:<28} valid G {trial[(a, b)][0]['gini']:.4f} KS {trial[(a, b)][0]['ks']:5.2f}  (k={len(cols)})")
base_va = rows[-1]["valid"]
ranked = sorted(trial.items(), key=lambda kv: (kv[1][0]["ks"], kv[1][0]["gini"]), reverse=True)
best_pair, (mv, cols2, m2, it2) = ranked[0]
rows.append(evaluate(f"EXP2 GAM + {best_pair[0]} x {best_pair[1]}", cols2, [best_pair], m2, it2,
                     extra={"interaction_trials": {f"{a} x {b}": v[0] for (a, b), v in trial.items()},
                            "validation_gain_vs_exp1": {"ks": round(mv["ks"] - base_va["ks"], 2), "gini": round(mv["gini"] - base_va["gini"], 4)}}))

# ── EXP3: up to three, added greedily while validation KS improves ───────
chosen = [best_pair]; cur_cols, cur_m, cur_it, cur_va = cols2, m2, it2, mv
for a, b in [k for k, _ in ranked[1:]]:
    cols = list(dict.fromkeys(cur_cols + [a, b]))
    m, it = fit_gam(cols, chosen + [(a, b)])
    v = metrics(yva, m.predict_proba(frame(VA, cols))[:, 1])
    print(f"   add {a} x {b:<28} valid G {v['gini']:.4f} KS {v['ks']:5.2f}  (was KS {cur_va['ks']:5.2f})")
    if v["ks"] > cur_va["ks"] + 0.05:
        chosen.append((a, b)); cur_cols, cur_m, cur_it, cur_va = cols, m, it, v
rows.append(evaluate(f"EXP3 GAM + {len(chosen)} interaction(s)", cur_cols, chosen, cur_m, cur_it))

# ── EXP4: GAM on the VIF pool (additive, then + the chosen interactions) ──
m4, it4 = fit_gam(POOL)
rows.append(evaluate("EXP4 GAM, VIF pool, additive", POOL, [], m4, it4))
pool_i = list(dict.fromkeys(POOL + [x for pr in chosen for x in pr]))
m4b, it4b = fit_gam(pool_i, chosen)
rows.append(evaluate(f"EXP4b GAM, VIF pool + {len(chosen)} interaction(s)", pool_i, chosen, m4b, it4b))

# ── EXP5: forward selection over the universe on VALIDATION KS, from EXP3 ─
# One pass: the validation gain of each remaining candidate added alone to
# the EXP3 model; then greedy adds in that order while KS keeps improving.
remaining = [c for c in UNIVERSE if c not in cur_cols]
gains = []
for c in remaining:
    m, it = fit_gam(cur_cols + [c], chosen, max_iter=400)
    v = metrics(yva, m.predict_proba(frame(VA, cur_cols + [c]))[:, 1])
    gains.append((v["ks"] - cur_va["ks"], v["gini"] - cur_va["gini"], c))
gains.sort(reverse=True)
print("   top single-add validation gains:", [(c, round(g, 2)) for g, _, c in gains[:12]])
sel5, m5, it5, va5 = list(cur_cols), cur_m, cur_it, cur_va
for g, _, c in gains[:12]:
    if g <= 0:
        break
    cols = sel5 + [c]
    m, it = fit_gam(cols, chosen)
    v = metrics(yva, m.predict_proba(frame(VA, cols))[:, 1])
    if v["ks"] > va5["ks"] + 0.05:
        sel5, m5, it5, va5 = cols, m, it, v
        print(f"   EXP5 keeps {c:<32} valid KS {v['ks']:5.2f} G {v['gini']:.4f}")
rows.append(evaluate(f"EXP5 GAM, validation-selected ({len(sel5)} features)", sel5, chosen, m5, it5,
                     extra={"single_add_gains": [(c, round(g, 2)) for g, _, c in gains[:20]]}))

# ── GBM benchmarks ────────────────────────────────────────────────────────
mg, itg = fit_gam(SEL10, gbm=True); rows.append(evaluate("GBM benchmark, 10 features", SEL10, [], mg, itg))
mgp, itgp = fit_gam(POOL, gbm=True); rows.append(evaluate("GBM benchmark, VIF pool", POOL, [], mgp, itgp))
mgu, itgu = fit_gam(UNIVERSE, gbm=True); rows.append(evaluate("GBM benchmark, all candidates", UNIVERSE, [], mgu, itgu))

(OUT / "gam" / "ladder.json").write_text(json.dumps(rows, indent=1, default=str))
joblib.dump({"exp1": (m1, SEL10, []), "exp2": (m2, cols2, [best_pair]), "exp3": (cur_m, cur_cols, chosen),
             "exp4": (m4, POOL, []), "exp4b": (m4b, pool_i, chosen), "exp5": (m5, sel5, chosen)}, OUT / "gam" / "models.joblib")
print("written", OUT / "gam")
