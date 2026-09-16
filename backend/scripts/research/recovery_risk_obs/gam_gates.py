import sys, json, warnings; sys.path.insert(0, "."); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.selection import compute_vif
meta = json.load(open("C:/Users/TransOrg/AppData/Local/Temp/claude/c--Users-TransOrg-OneDrive-Desktop-TIQCollect-product/138b2f9f-f1d6-4fff-9684-2176c1d47e9f/scratchpad/obs/ladder/artifacts/recovery_risk/f1/metadata.json"))
SIGNS = meta["spec"]["expected_sign"]
panel = pd.read_parquet("data/ledger/obs_wd10/panel.parquet")
CATS = {c for c in panel.columns if panel[c].dtype == object and c not in ("loan_id", "borrower_id")}
months = np.sort(panel.month_index.unique()); n = len(months)
TR = panel[panel.month_index.isin(months[:int(n*.6)])]; VA = panel[panel.month_index.isin(months[int(n*.6):int(n*.75)])]; OOT = panel[panel.month_index.isin(months[int(n*.75):])]
cols = ['arrears_ratio', 'latest_disposition', 'cibil_score', 'overdue_amount', 'intent_calls_3m', 'last_commit_status', 'recent_ptp_status', 'calls_3m', 'paid_ratio_3m', 'ptp_amount_to_emi', 'employment_type', 'days_since_last_contact', 'disposition_recency_class', 'payment_count_30d', 'mean_call_duration_6m']
pairs = [('latest_disposition', 'arrears_ratio'), ('latest_disposition', 'cibil_score'), ('paid_ratio_3m', 'arrears_ratio')]
num = [c for c in cols if c not in CATS]
vif = compute_vif(TR[num].fillna(TR[num].median()))
print("VIF:", {c: round(float(v), 2) for c, v in vif.items()}, "max", round(float(vif.max()), 2))
csi = {c: round(ev.psi(TR[c], OOT[c]), 4) for c in cols}
print("CSI train->OOT:", csi, "max", max(csi.values()))
ivt = pd.read_csv("C:/Users/TransOrg/AppData/Local/Temp/claude/c--Users-TransOrg-OneDrive-Desktop-TIQCollect-product/138b2f9f-f1d6-4fff-9684-2176c1d47e9f/scratchpad/obs/ladder/artifacts/recovery_risk/f4/eda/information_value.csv").set_index("feature").iv
print("IV:", {c: round(float(ivt.get(c, float('nan'))), 3) for c in cols})
# refit frozen spec to verify monotonicity of the shape functions
CAT_LEVELS = {c: sorted(panel[c].astype(str).unique()) for c in cols if c in CATS}
def frame(d):
    X = d[cols].copy()
    for c in cols:
        if c in CATS: X[c] = pd.Categorical(X[c].astype(str), categories=CAT_LEVELS[c]).codes
    return X
idx = {c: i for i, c in enumerate(cols)}
cst = [{i} for i in range(len(cols))] + [{idx[a], idx[b]} for a, b in pairs]
p = dict(learning_rate=0.05, max_leaf_nodes=4, max_depth=2, l2_regularization=1.0, random_state=0, early_stopping=False,
         categorical_features=[c in CATS for c in cols], monotonic_cst=[0 if c in CATS else int(SIGNS.get(c, 0)) for c in cols],
         interaction_cst=cst, max_bins=32, min_samples_leaf=300, max_iter=577)
m = HistGradientBoostingClassifier(**p).fit(frame(TR), TR.y.to_numpy(int))
po = m.predict_proba(frame(OOT))[:, 1]; y = OOT.y.to_numpy(int)
ks, _ = ev.ks_statistic(y, po); print("refit OOT KS", round(ks, 2), "Gini", round(ev.gini(y, po), 4))
Xtr = frame(TR); base = m.decision_function(Xtr).mean()
for c in num:
    X = Xtr.copy(); grid = np.quantile(TR[c].dropna(), np.linspace(0.02, 0.98, 11)); vals = []
    for v in grid:
        X[c] = v; vals.append(m.decision_function(X).mean() - base)
    d = np.diff(vals); sign = SIGNS.get(c, 0)
    mono = "asc" if np.all(d >= -1e-9) else "desc" if np.all(d <= 1e-9) else "non-mono"
    ok = sign == 0 or (sign > 0 and mono == "asc") or (sign < 0 and mono == "desc")
    print(f"  {c:<26} sign {sign:+d} {mono:<9} range {max(vals)-min(vals):.3f} {'OK' if ok else 'VIOLATION'}")
