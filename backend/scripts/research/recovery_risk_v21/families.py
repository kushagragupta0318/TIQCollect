"""Stage 3 — ceilings, selection, model families.

Selection uses train (fit) + valid (score) ONLY. OOT is scored once per
final candidate at the end, on identical rows.
"""
import sys, json, warnings, itertools
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, brier_score_loss
from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.binning import WOEBinner
from app.ml.pipeline.selection import select_features, compute_vif
from app.ml.pipeline.config import RECOVERY_RISK_V2, FEED_ONLY_FEATURES, NO_HISTORY_FEATURES

S = sys.argv[1]
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
df = pd.read_parquet(f"{S}/candidates.parquet")
uni = pd.read_csv(f"{S}/univariate_dev.csv")
months = np.sort(df.month_index.unique()); n = len(months)
tr_m, va_m, oot_m = months[:int(n*.6)], months[int(n*.6):int(n*.75)], months[int(n*.75):]
TR, VA, OOT = (df[df.month_index.isin(m)].copy() for m in (tr_m, va_m, oot_m))
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
print(f"train {len(TR)} valid {len(VA)} oot {len(OOT)}  bad rate tr {ytr.mean():.3f} oot {yoot.mean():.3f}")

def gk(y, p):
    ks, _ = ev.ks_statistic(y, p)
    return round(2 * roc_auc_score(y, p) - 1, 4), round(ks, 2)

CATS = ["loan_type", "dpd_bucket", "city", "employment_type", "branch_code",
        "last_ptp_status", "last_visit_outcome", "last_met_outcome"]
EXCL = set(uni[uni.iv.isna()].feature) | {"distinct_agents_6m", "fraud_flag", "ptp_rescheduled_6m"}
cands = [c for c in uni.feature if c not in EXCL]
NUM = [c for c in cands if c not in CATS]
CAT = [c for c in cands if c in CATS]
print("candidates", len(cands), "numeric", len(NUM), "categorical", len(CAT))

# ── A. observable ceilings (all candidates) ────────────────────────────────
def hgb_frame(d, cols):
    X = d[cols].copy()
    for c in cols:
        if c in CATS:
            X[c] = pd.Categorical(X[c].astype(str), categories=sorted(df[c].astype(str).unique())).codes
    return X

def fit_hgb(cols, monotone=None, **kw):
    params = dict(max_iter=600, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=200,
                  l2_regularization=1.0, early_stopping=True, validation_fraction=None,
                  random_state=0)
    params.update(kw)
    # manual early stopping on the chronological valid split
    Xtr, Xva = hgb_frame(TR, cols), hgb_frame(VA, cols)
    best, best_it = -1, 0
    m = HistGradientBoostingClassifier(**{**params, "early_stopping": False}, categorical_features=[c in CATS for c in cols],
                                       monotonic_cst=monotone)
    m.fit(Xtr, ytr)
    # staged: pick the iteration count with best valid AUC
    scores = [roc_auc_score(yva, p[:, 1]) for p in m.staged_predict_proba(Xva)]
    best_it = int(np.argmax(scores)) + 1
    m2 = HistGradientBoostingClassifier(**{**params, "early_stopping": False, "max_iter": best_it},
                                        categorical_features=[c in CATS for c in cols], monotonic_cst=monotone)
    m2.fit(Xtr, ytr)
    return m2, best_it, round(2 * max(scores) - 1, 4)

print("\n=== OBSERVABLE CEILING (all candidates, GBM, early-stopped on valid) ===")
m_all, it_all, gva = fit_hgb(cands)
p = m_all.predict_proba(hgb_frame(OOT, cands))[:, 1]
print("GBM all candidates: valid Gini", gva, "iters", it_all, "OOT (Gini, KS)", gk(yoot, p))
ceiling_obs = gk(yoot, p)

# ── B. oracle (latents + observables) ──────────────────────────────────────
gt = pd.read_parquet("data/ledger/full/ground_truth.parquet")
gt["month_index"] = gt.day // 30
lat = ["willingness", "capacity", "reachability", "shock_state", "true_pay_logit"]
def with_lat(d):
    return d.merge(gt[["loan_id", "month_index"] + lat], on=["loan_id", "month_index"], how="left")
TRl, VAl, OOTl = with_lat(TR), with_lat(VA), with_lat(OOT)
print("latent coverage", TRl.willingness.notna().mean().round(3))
Xo = lambda d, cols: d[cols].fillna(d[cols].median())
lr = LogisticRegression(max_iter=2000).fit(Xo(TRl, lat), ytr)
print("ORACLE latents only (LR)     OOT", gk(yoot, lr.predict_proba(Xo(OOTl, lat))[:, 1]))
TRl2, VAl2, OOTl2 = TRl.copy(), VAl.copy(), OOTl.copy()
TR_bak, VA_bak, OOT_bak = TR, VA, OOT
TR, VA, OOT = TRl2, VAl2, OOTl2
m_or, it_or, _ = fit_hgb(cands + lat)
p_or = m_or.predict_proba(hgb_frame(OOT, cands + lat))[:, 1]
print("ORACLE joint (GBM)           OOT", gk(yoot, p_or))
TR, VA, OOT = TR_bak, VA_bak, OOT_bak

# ── C. selection protocol (WOE on train, SFS scored on valid) ──────────────
class G: pass
gates = RECOVERY_RISK_V2.gates
signs = dict(RECOVERY_RISK_V2.expected_sign)
binner = WOEBinner(NUM, CAT, trends=WOEBinner.trends_from_signs(signs))
binner.fit(TR[NUM + CAT], ytr)
Wtr, Wva, Woot = binner.transform(TR[NUM + CAT]), binner.transform(VA[NUM + CAT]), binner.transform(OOT[NUM + CAT])
iv = dict(binner.iv_)
print("\nbinner failed:", binner.failed_)
sel = select_features(Wtr, pd.Series(ytr), iv, gates, max_features=14, min_gini_gain=0.0008,
                      valid=(Wva, pd.Series(yva)))
print("\nSELECTION LOG")
for s in sel.log: print("  ", {k: v for k, v in s.items() if k != "dropped_features"})
print("corr dropped:")
for r in sel.corr_dropped: print("   ", r)
print("VIF:"); print(sel.vif_table.to_string(index=False))
print("SFS path:")
for r in sel.sfs_path: print("   ", r)
print("sign dropped:", sel.sign_dropped)
selected = [c[:-4] for c in sel.selected]
print("\nSELECTED", len(selected), selected)

# Also: an extended SFS with a looser stop to see the marginal curve
sel2 = select_features(Wtr, pd.Series(ytr), iv, gates, max_features=16, min_gini_gain=0.0002,
                       valid=(Wva, pd.Series(yva)))
print("\nLOOSE SFS path (gain>=0.0002):")
for r in sel2.sfs_path: print("   ", r)
selected_loose = [c[:-4] for c in sel2.selected]

# ── D. model families on the selected set ─────────────────────────────────
def report(name, p_va, p_oot, feats):
    gv, kv = gk(yva, p_va); go, ko = gk(yoot, p_oot)
    br = round(brier_score_loss(yoot, p_oot), 5); gap = round(float(p_oot.mean() - yoot.mean()), 4)
    print(f"{name:<44} k={len(feats):<2} valid G={gv:.4f} KS={kv:5.2f} | OOT G={go:.4f} KS={ko:5.2f} Brier={br} gap={gap:+.4f}")
    return dict(name=name, k=len(feats), valid_gini=gv, valid_ks=kv, oot_gini=go, oot_ks=ko, brier=br, cal_gap=gap, features=feats)

results = []
print("\n=== MODEL FAMILIES ===")
for label, feats in (("selected", selected), ("selected_loose", selected_loose)):
    W = [f + "_woe" for f in feats]
    # A. WOE + LR (pipeline form)
    lrA = LogisticRegression(max_iter=2000).fit(Wtr[W], ytr)
    results.append(report(f"A WOE+LR [{label}]", lrA.predict_proba(Wva[W])[:, 1], lrA.predict_proba(Woot[W])[:, 1], feats))
    # B. L1-regularised LR on WOE, C by valid
    best = None
    for C in (0.01, 0.03, 0.1, 0.3, 1.0):
        m = LogisticRegression(penalty="l1", solver="liblinear", C=C, max_iter=3000).fit(Wtr[W], ytr)
        g = roc_auc_score(yva, m.predict_proba(Wva[W])[:, 1])
        if best is None or g > best[0]: best = (g, C, m)
    m = best[2]; nz = [f for f, b in zip(feats, m.coef_[0]) if abs(b) > 1e-6]
    results.append(report(f"B L1-LR WOE C={best[1]} [{label}]", m.predict_proba(Wva[W])[:, 1], m.predict_proba(Woot[W])[:, 1], nz))
    # C. GBM on raw selected
    mC, itC, _ = fit_hgb(feats)
    results.append(report(f"C GBM raw it={itC} [{label}]", mC.predict_proba(hgb_frame(VA, feats))[:, 1], mC.predict_proba(hgb_frame(OOT, feats))[:, 1], feats))
    # D. monotone GBM (sign-constrained), interpretable
    mono = [int(signs.get(f, 0)) if f not in CATS else 0 for f in feats]
    mD, itD, _ = fit_hgb(feats, monotone=mono)
    results.append(report(f"D monotone-GBM it={itD} [{label}]", mD.predict_proba(hgb_frame(VA, feats))[:, 1], mD.predict_proba(hgb_frame(OOT, feats))[:, 1], feats))

# B' L1 over the whole VIF-filtered pool (regularisation as selection)
pool = [c for c in Wtr.columns if c[:-4] in [r["feature"] for r in sel.vif_table.to_dict("records") if r["action"] == "kept"]]
best = None
for C in (0.003, 0.01, 0.03, 0.1):
    m = LogisticRegression(penalty="l1", solver="liblinear", C=C, max_iter=3000).fit(Wtr[pool], ytr)
    g = roc_auc_score(yva, m.predict_proba(Wva[pool])[:, 1])
    nz = [f[:-4] for f, b in zip(pool, m.coef_[0]) if abs(b) > 1e-6]
    print(f"  L1 pool C={C}: valid AUC {g:.4f} nonzero {len(nz)}")
    if best is None or g > best[0]: best = (g, C, m, nz)
m = best[2]
results.append(report(f"B' L1-LR on VIF pool C={best[1]}", m.predict_proba(Wva[pool])[:, 1], m.predict_proba(Woot[pool])[:, 1], best[3]))
# C' GBM on the VIF pool
poolraw = [c[:-4] for c in pool]
mC2, itC2, _ = fit_hgb(poolraw)
results.append(report(f"C' GBM on VIF pool it={itC2}", mC2.predict_proba(hgb_frame(VA, poolraw))[:, 1], mC2.predict_proba(hgb_frame(OOT, poolraw))[:, 1], poolraw))

# ── E. family ablation: SFS run with each family removed / alone ───────────
print("\n=== FAMILY CONTRIBUTION (WOE+LR, SFS on valid, OOT reported) ===")
fam = dict(zip(uni.feature, uni.family))
def run_sfs(pool_feats):
    cols = [f + "_woe" for f in pool_feats if f + "_woe" in Wtr.columns]
    s = select_features(Wtr[cols], pd.Series(ytr), iv, gates, max_features=14, min_gini_gain=0.0008, valid=(Wva[cols], pd.Series(yva)))
    W = s.selected
    if not W: return (0, 0), [], (0, 0)
    m = LogisticRegression(max_iter=2000).fit(Wtr[W], ytr)
    return gk(yva, m.predict_proba(Wva[W])[:, 1]), [c[:-4] for c in W], gk(yoot, m.predict_proba(Woot[W])[:, 1])
base_all = run_sfs(cands)
print(f"ALL families            valid {base_all[0]} OOT {base_all[2]} k={len(base_all[1])}")
fam_rows = []
for fname in sorted(set(fam.values())):
    without = [c for c in cands if fam.get(c) != fname]
    alone = [c for c in cands if fam.get(c) == fname or fam.get(c) == "delinquency"]
    r_wo = run_sfs(without); r_al = run_sfs(alone)
    print(f"without {fname:<12} valid {r_wo[0]} OOT {r_wo[2]} k={len(r_wo[1])} | {fname}+delinquency alone valid {r_al[0]} OOT {r_al[2]} k={len(r_al[1])}")
    fam_rows.append(dict(family=fname, without_valid=r_wo[0][0], without_oot=r_wo[2][0], alone_valid=r_al[0][0], alone_oot=r_al[2][0]))

json.dump(dict(ceiling_obs=ceiling_obs, results=results, selected=selected, selected_loose=selected_loose,
               family=fam_rows, vif=sel.vif_table.to_dict("records"), corr=sel.corr_dropped, sfs=sel.sfs_path,
               sfs_loose=sel2.sfs_path, iv={k: round(v, 4) for k, v in iv.items()}),
          open(f"{S}/families.json", "w"), indent=1, default=str)
print("written families.json")
