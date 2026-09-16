"""Stage 4 — information-gap decomposition + remaining legitimate probes."""
import sys, json, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.binning import WOEBinner

S = sys.argv[1]
pd.set_option("display.width", 250)
df = pd.read_parquet(f"{S}/candidates.parquet")
fam = json.load(open(f"{S}/families.json"))
uni = pd.read_csv(f"{S}/univariate_dev.csv")
months = np.sort(df.month_index.unique()); n = len(months)
tr_m, va_m, oot_m = months[:int(n*.6)], months[int(n*.6):int(n*.75)], months[int(n*.75):]
gt = pd.read_parquet("data/ledger/full/ground_truth.parquet"); gt["month_index"] = gt.day // 30
lat = ["willingness", "capacity", "reachability", "shock_state", "true_pay_logit"]
df = df.merge(gt[["loan_id", "month_index"] + lat], on=["loan_id", "month_index"], how="left")
# calendar (observable): month of year at as_of
df["cal_sin"] = np.sin(2 * np.pi * (df.month_index % 12) / 12); df["cal_cos"] = np.cos(2 * np.pi * (df.month_index % 12) / 12)
TR, VA, OOT = (df[df.month_index.isin(m)].copy() for m in (tr_m, va_m, oot_m))
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
CATS = ["loan_type", "dpd_bucket", "city", "employment_type", "branch_code", "last_ptp_status", "last_visit_outcome", "last_met_outcome"]
def gk(y, p):
    ks, _ = ev.ks_statistic(y, p); return round(2 * roc_auc_score(y, p) - 1, 4), round(ks, 2)
def frame(d, cols):
    X = d[cols].copy()
    for c in cols:
        if c in CATS: X[c] = pd.Categorical(X[c].astype(str), categories=sorted(df[c].astype(str).unique())).codes
    return X
def gbm(cols):
    p = dict(max_iter=600, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0, random_state=0, early_stopping=False)
    m = HistGradientBoostingClassifier(**p, categorical_features=[c in CATS for c in cols]).fit(frame(TR, cols), ytr)
    sc = [roc_auc_score(yva, q[:, 1]) for q in m.staged_predict_proba(frame(VA, cols))]
    it = int(np.argmax(sc)) + 1
    m = HistGradientBoostingClassifier(**{**p, "max_iter": it}, categorical_features=[c in CATS for c in cols]).fit(frame(TR, cols), ytr)
    return gk(yoot, m.predict_proba(frame(OOT, cols))[:, 1])

pool = [r["feature"] for r in fam["vif"] if r["action"] == "kept"]
allc = [c for c in uni.feature if c in df.columns and c not in ("distinct_agents_6m", "fraud_flag", "ptp_rescheduled_6m") and not uni.set_index("feature").iv.isna().get(c, True)]
print("pool", len(pool), "all", len(allc))
base_pool = gbm(pool); base_all = gbm(allc)
print(f"\nGBM VIF-pool OOT {base_pool}   GBM all-candidates OOT {base_all}")

print("\n=== INFORMATION GAP: what each HIDDEN quantity would add to the observables (GBM, OOT) ===")
for extra in (["willingness"], ["capacity"], ["reachability"], ["shock_state"], ["willingness", "capacity"], ["willingness", "capacity", "reachability", "shock_state"], ["true_pay_logit"], lat):
    print(f"  all-candidates + {extra!s:<60} {gbm(allc + extra)}")

print("\n=== calendar month (observable) ===")
print("  cal alone LR OOT", gk(yoot, LogisticRegression().fit(TR[["cal_sin", "cal_cos"]], ytr).predict_proba(OOT[["cal_sin", "cal_cos"]])[:, 1]))
print("  GBM all + calendar", gbm(allc + ["cal_sin", "cal_cos"]))
print("  bad rate by calendar month (dev):", TR.assign(cm=TR.month_index % 12).groupby("cm").y.mean().round(3).to_dict())

print("\n=== live-PTP flag: prevalence + bad rate in OOT ===")
print(OOT.groupby("ptp_live_at_asof").y.agg(["mean", "size"]))

print("\n=== WOE bin tables of the selected 10 (fitted on train) ===")
sel = fam["selected"]
NUM = [c for c in sel if c not in CATS]; CAT = [c for c in sel if c in CATS]
b = WOEBinner(NUM, CAT).fit(TR[sel], ytr)
for c in sel:
    t = b.tables_[c]
    print(f"\n-- {c}  IV={b.iv_[c]:.4f} monotonic={b.monotonic_ok_[c]}")
    print(t[["Bin", "Count", "Count (%)", "Event rate", "WoE"]].to_string(index=False))
