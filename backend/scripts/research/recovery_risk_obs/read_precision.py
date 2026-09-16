"""What precision of a willingness reading at as_of would close the gap?

On world w1: observables + (willingness + N(0, s)) for several s, GBM
early-stopped on valid, OOT Gini/KS. And: how well do the observables
already recover willingness (residual sd), so the two can be compared.
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score
from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.config import FEED_ONLY_FEATURES, NO_HISTORY_FEATURES

W = sys.argv[1] if len(sys.argv) > 1 else "w1"
D = f"data/ledger/obs_{W}"
panel = pd.read_parquet(f"{D}/panel.parquet")
gt = pd.read_parquet(f"{D}/ground_truth.parquet"); gt["month_index"] = gt.day // 30
df = panel.merge(gt[["loan_id", "month_index", "willingness"]], on=["loan_id", "month_index"], how="left")
NOT = {"loan_id", "borrower_id", "as_of_date", "month_index", "y", "recovered_amount", "outcome_threshold",
       "baseline_overdue_amount", "baseline_emi_amount", "willingness"} | set(FEED_ONLY_FEATURES) | set(NO_HISTORY_FEATURES) | {
    "monthly_income", "dti_ratio", "credit_vintage_months", "num_open_loans", "num_enquiries_6m", "other_lender_delinq",
    "utilization_pct", "mail_returned_count", "address_vintage_months", "phone_verified", "thin_file", "sourcing_channel",
    "residence_type", "is_secured"}
cands = [c for c in panel.columns if c not in NOT]
cats = [c for c in cands if df[c].dtype == object]
months = np.sort(df.month_index.unique()); n = len(months)
TR, VA, OOT = (df[df.month_index.isin(m)] for m in (months[:int(n*.6)], months[int(n*.6):int(n*.75)], months[int(n*.75):]))
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
rng = np.random.default_rng(0)

def frame(d, cols, extra=None):
    X = d[cols].copy()
    for c in cols:
        if c in cats: X[c] = pd.Categorical(X[c].astype(str), categories=sorted(df[c].astype(str).unique())).codes
    if extra is not None: X["w_read"] = extra
    return X

def gbm(extra_tr=None, extra_va=None, extra_oot=None):
    cols = cands
    p = dict(max_iter=600, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0, random_state=0, early_stopping=False)
    cf = [c in cats for c in cols] + ([False] if extra_tr is not None else [])
    m = HistGradientBoostingClassifier(**p, categorical_features=cf).fit(frame(TR, cols, extra_tr), ytr)
    sc = [roc_auc_score(yva, q[:, 1]) for q in m.staged_predict_proba(frame(VA, cols, extra_va))]
    it = int(np.argmax(sc)) + 1
    m = HistGradientBoostingClassifier(**{**p, "max_iter": it}, categorical_features=cf).fit(frame(TR, cols, extra_tr), ytr)
    pr = m.predict_proba(frame(OOT, cols, extra_oot))[:, 1]
    ks, _ = ev.ks_statistic(yoot, pr)
    return round(2 * roc_auc_score(yoot, pr) - 1, 4), round(ks, 2)

print(f"world {W}: sd(willingness at as_of) = {df.willingness.std():.3f}")
print("observables only                     ", gbm())
for s in (0.30, 0.20, 0.15, 0.10, 0.05, 0.0):
    r = gbm(TR.willingness + rng.normal(0, s, len(TR)), VA.willingness + rng.normal(0, s, len(VA)), OOT.willingness + rng.normal(0, s, len(OOT)))
    print(f"observables + willingness read sd={s:<5}", r)
reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, random_state=0).fit(frame(TR, cands), TR.willingness)
res = OOT.willingness - reg.predict(frame(OOT, cands))
print(f"observables already recover willingness with residual sd = {res.std():.3f}  (R^2 {1 - res.var()/OOT.willingness.var():.3f})")
