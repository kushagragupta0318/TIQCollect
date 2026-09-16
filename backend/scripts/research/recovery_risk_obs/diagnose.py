"""Section 2 — information-gap diagnosis on the CURRENT world (read-only).

A. observable ceiling  B. oracle (w + cap + reach)  C. 2.1.0  D. gaps + least observable latent.
"""
import sys, json, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.engine import DecisionEngine
from app.ml.pipeline.config import RECOVERY_RISK_V21

CAND = sys.argv[1]            # candidates.parquet from the discovery pass (current world)
df = pd.read_parquet(CAND)
uni = pd.read_csv(sys.argv[2])
gt = pd.read_parquet("data/ledger/full/ground_truth.parquet"); gt["month_index"] = gt.day // 30
LAT = ["willingness", "capacity", "reachability"]
df = df.merge(gt[["loan_id", "month_index"] + LAT + ["shock_state"]], on=["loan_id", "month_index"], how="left")
months = np.sort(df.month_index.unique()); n = len(months)
tr_m, va_m, oot_m = months[:int(n*.6)], months[int(n*.6):int(n*.75)], months[int(n*.75):]
TR, VA, OOT = (df[df.month_index.isin(m)].copy() for m in (tr_m, va_m, oot_m))
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
CATS = ["loan_type", "dpd_bucket", "city", "employment_type", "branch_code", "last_ptp_status", "last_visit_outcome", "last_met_outcome"]
EXCL = set(uni[uni.iv.isna()].feature) | {"distinct_agents_6m", "fraud_flag", "ptp_rescheduled_6m"}
obs = [c for c in uni.feature if c not in EXCL]

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
def lr(cols):
    med = TR[cols].median()
    m = LogisticRegression(max_iter=3000).fit(TR[cols].fillna(med), ytr)
    return gk(yoot, m.predict_proba(OOT[cols].fillna(med))[:, 1])

print(f"OOT rows {len(OOT)}  bad rate {yoot.mean():.4f}")
A = gbm(obs)
print(f"A. observable ceiling  (GBM, {len(obs)} legitimate observable candidates)   Gini/KS = {A}")
B_lr = lr(LAT); B_gbm = gbm(LAT); B_joint = gbm(obs + LAT)
print(f"B. oracle: w+cap+reach only            LR {B_lr}   GBM {B_gbm}")
print(f"   oracle: w+cap+reach + observables   GBM {B_joint}")
eng = DecisionEngine.get("recovery_risk", "2.1.0")
rows = OOT.reindex(columns=RECOVERY_RISK_V21.all_features).to_dict(orient="records")
p21 = np.array([r.probability for r in eng.score_batch_detailed(rows)], dtype=float)
C = gk(yoot, p21)
print(f"C. 2.1.0 scorecard (production path)   Gini/KS = {C}")
print(f"D. scorecard -> observable ceiling gap   Gini {A[0]-C[0]:+.4f}  KS {A[1]-C[1]:+.2f}")
print(f"   observable -> joint oracle gap        Gini {B_joint[0]-A[0]:+.4f}  KS {B_joint[1]-A[1]:+.2f}")
print("\n   which latent is least observable? (observables + one true latent, GBM)")
for l in LAT + ["shock_state"]:
    r = gbm(obs + [l]); print(f"     + {l:<13} {r}   gain {r[0]-A[0]:+.4f}")
print("\n   how well do the observables recover each latent at as_of? (R^2 of GBM regression, OOT)")
from sklearn.ensemble import HistGradientBoostingRegressor
for l in LAT:
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, random_state=0).fit(frame(TR, obs), TR[l])
    r2 = 1 - ((OOT[l] - m.predict(frame(OOT, obs))) ** 2).sum() / ((OOT[l] - OOT[l].mean()) ** 2).sum()
    print(f"     {l:<13} R^2 = {r2:.3f}")
print("\n   event coverage per delinquent account-month (last 30d before as_of):")
for c in ("calls_3m", "visits_3m", "intent_calls_3m", "ptp_set_3m", "met_visits_3m"):
    print(f"     {c:<18} mean {df[c].mean()/3:.2f} per month")
