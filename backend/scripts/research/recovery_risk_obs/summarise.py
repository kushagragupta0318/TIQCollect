"""Section 13 comparison table from the ladder outputs + the 2.1.0 artifact."""
import json, sys
from pathlib import Path
import pandas as pd

BACKEND = Path(__file__).resolve().parents[3]
OUT = Path(sys.argv[1])
rows = []
m = json.loads((BACKEND / "app/ml/artifacts/recovery_risk/2.1.0/metadata.json").read_text())
d = BACKEND / "app/ml/artifacts/recovery_risk/2.1.0"
iv = pd.read_csv(d / "eda/information_value.csv").set_index("feature")
vif = pd.read_csv(d / "evaluation/vif.csv")
psi = pd.read_csv(d / "evaluation/psi.csv").set_index("feature")
sel = m["selected_features"]
rows.append(dict(exp="EXP0 2.1.0", bad=0.6934, gini=m["metrics"]["oot"]["gini"], auc=m["metrics"]["oot"]["auc"],
                 ks=m["metrics"]["oot"]["ks"], brier=m["metrics"]["oot"]["brier"],
                 cal_gap=m["metrics"]["oot"]["calibration_gap"],
                 iv=f"{iv.loc[sel,'iv'].min():.3f}-{iv.loc[sel,'iv'].max():.3f}",
                 vif=round(float(vif[vif.action == 'kept'].vif.max()), 2), psi=m["score_psi_train_vs_oot"],
                 csi=round(float(psi.loc[sel, 'psi'].max()), 4), k=len(sel), gbm=m["metrics"]["oot_challenger"]["gini"],
                 ceiling="0.492 / 36.29", plus_w="0.5506", oracle="0.5777", gates="PASS", minseg=0.2596, features=", ".join(sel)))
for e in ("exp1", "exp2", "exp3", "exp4", "exp3b", "exp4b", "exp1z", "exp5", "exp6", "wi_chan", "wi_rho", "wi_obs"):
    f = OUT / f"{e}.json"
    if not f.exists():
        continue
    r = json.loads(f.read_text())
    rows.append(dict(exp=e.upper() + f" ({r['world']})", bad=r["bad_rate"], gini=r["oot"]["gini"], auc=r["oot"]["auc"], ks=r["oot"]["ks"],
                     brier=r["oot"]["brier"], cal_gap=r["oot"]["calibration_gap"],
                     iv=f"{r['iv_min_selected']:.3f}-{r['iv_max_selected']:.3f}", vif=r["max_vif"], psi=r["score_psi"],
                     csi=r["max_csi_selected"], k=r["n_selected"], gbm=r["oot_challenger_gbm_gini"],
                     ceiling=f"{r['observable_ceiling'][0]} / {r['observable_ceiling'][1]}",
                     plus_w=f"{r['observables_plus_true_willingness'][0]}", oracle=f"{r['joint_oracle'][0]}",
                     gates="PASS" if r["gates_passed"] else "FAIL", minseg=r["min_segment_gini"],
                     features=", ".join(r["selected"])))
t = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 200)
print(t.drop(columns=["features"]).to_string(index=False))
for r in rows:
    print(f"\n{r['exp']}: {r['features']}")
