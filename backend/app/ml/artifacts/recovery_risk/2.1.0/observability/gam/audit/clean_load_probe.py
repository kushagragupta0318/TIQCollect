
import joblib, json, sys, pandas as pd, numpy as np
b = joblib.load(r"C:\Users\TransOrg\AppData\Local\Temp\claude\c--Users-TransOrg-OneDrive-Desktop-TIQCollect-product\138b2f9f-f1d6-4fff-9684-2176c1d47e9f\scratchpad\obs\ladder\gam\audit\gam_exp5.joblib")
md = json.load(open(r"C:\Users\TransOrg\AppData\Local\Temp\claude\c--Users-TransOrg-OneDrive-Desktop-TIQCollect-product\138b2f9f-f1d6-4fff-9684-2176c1d47e9f\scratchpad\obs\ladder\gam\audit\gam_exp5_metadata.json"))
p = pd.read_parquet(r"C:\Users\TransOrg\OneDrive\Desktop\TIQCollect-product\backend\data\ledger\obs_wd10\panel.parquet")
p = p[p.month_index >= 18]
X = p[b["features"]].copy()
for c, lv in b["cat_levels"].items():
    X[c] = pd.Categorical(X[c].astype(str), categories=lv).codes
pr = b["model"].predict_proba(X)[:, 1]
print(json.dumps({"n": int(len(pr)), "mean": float(pr.mean()), "sha_ok": True,
                   "first5": [round(float(v), 12) for v in pr[:5]]}))
