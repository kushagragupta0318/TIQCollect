"""The final controlled search (2026-09-15): can an admissible, stable,
interpretable model on the FROZEN wd10 world reach OOT KS >= 39?

    python scripts/research/recovery_risk_obs/gam_search.py OUT_DIR

Rules encoded here, not in prose:
  * the world, split and OOT rows are the ones every previous row used;
  * admissible candidates = the 115 observable candidates whose
    train -> VALIDATION PSI is < 0.10 (OOT distributions are never read
    during the search; the train -> OOT CSI gate is checked once, at the end,
    on the frozen model);
  * every accept / reject decision is validation KS; an addition must buy
    >= 0.05 KS, a removal must cost < 0.05 (compactness);
  * the model is the GAM of gam_ladder.py (exactly additive shape functions,
    monotone where the sign is declared, explicit pairs only);
  * interactions come from a fixed list of interpretable pairs, accepted only
    on validation KS;
  * a 3-point smoothing grid (knots / min-leaf) is chosen on validation;
  * OOT is scored ONCE per frozen stage at the end, never consulted before.
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
from sklearn.metrics import brier_score_loss, roc_auc_score

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")
from app.ml.pipeline import evaluate as ev                                 # noqa: E402
from app.ml.pipeline.config import FEED_ONLY_FEATURES, NO_HISTORY_FEATURES  # noqa: E402
from app.ml.pipeline.selection import compute_vif                          # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gam_common import interaction_cst_for                                 # noqa: E402

OUT = Path(sys.argv[1]); (OUT / "search").mkdir(parents=True, exist_ok=True)
LOG = open(OUT / "search" / "search.log", "w", encoding="utf-8")


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True); LOG.write(s + "\n"); LOG.flush()


meta = json.loads((OUT / "artifacts" / "recovery_risk" / "f1" / "metadata.json").read_text())
SIGNS = dict(meta["spec"]["expected_sign"])
panel = pd.read_parquet(BACKEND / "data" / "ledger" / "obs_wd10" / "panel.parquet")
CATS = {c for c in panel.columns if panel[c].dtype == object and c not in ("loan_id", "borrower_id")}
months = np.sort(panel.month_index.unique()); n = len(months)
TR = panel[panel.month_index.isin(months[:int(n * .6)])]
VA = panel[panel.month_index.isin(months[int(n * .6):int(n * .75)])]
OOT = panel[panel.month_index.isin(months[int(n * .75):])]
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
NOT = {"loan_id", "borrower_id", "as_of_date", "month_index", "y", "recovered_amount", "outcome_threshold",
       "baseline_overdue_amount", "baseline_emi_amount"} | set(FEED_ONLY_FEATURES) | set(NO_HISTORY_FEATURES) | {
    "monthly_income", "dti_ratio", "credit_vintage_months", "num_open_loans", "num_enquiries_6m", "other_lender_delinq",
    "utilization_pct", "mail_returned_count", "address_vintage_months", "phone_verified", "thin_file", "sourcing_channel",
    "residence_type", "is_secured"}
UNIVERSE = [c for c in panel.columns if c not in NOT]
CAT_LEVELS = {c: sorted(panel[c].astype(str).unique()) for c in UNIVERSE if c in CATS}
IVT = pd.read_csv(OUT / "artifacts" / "recovery_risk" / "f4" / "eda" / "information_value.csv").set_index("feature").iv.to_dict()

# ── admissibility: train -> VALIDATION stability only ─────────────────────
psi_va = {c: ev.psi(TR[c], VA[c]) for c in UNIVERSE}
ADMISSIBLE = [c for c in UNIVERSE if psi_va[c] < 0.10]
say(f"universe {len(UNIVERSE)}, admissible on train->valid PSI < 0.10: {len(ADMISSIBLE)}; excluded:",
    {c: round(v, 3) for c, v in psi_va.items() if v >= 0.10})

START = ["arrears_ratio", "latest_disposition", "cibil_score", "no_answer_streak", "overdue_amount",
         "intent_calls_3m", "last_commit_status", "recent_ptp_status", "calls_3m", "paid_ratio_3m",
         "ptp_amount_to_emi", "employment_type", "days_since_last_contact", "disposition_recency_class",
         "interest_rate"]
START_PAIRS = [("latest_disposition", "arrears_ratio")]
GRID = {"knots32_leaf300": dict(max_bins=32, min_samples_leaf=300),
        "knots64_leaf300": dict(max_bins=64, min_samples_leaf=300),
        "knots32_leaf150": dict(max_bins=32, min_samples_leaf=150)}
CFG = dict(GRID["knots32_leaf300"])


def frame(d, cols):
    X = d[cols].copy()
    for c in cols:
        if c in CATS:
            X[c] = pd.Categorical(X[c].astype(str), categories=CAT_LEVELS[c]).codes
    return X


def mets(y, p, d=None):
    ks, _ = ev.ks_statistic(y, p)
    out = dict(gini=round(2 * roc_auc_score(y, p) - 1, 4), auc=round(roc_auc_score(y, p), 4), ks=round(ks, 2),
               brier=round(brier_score_loss(y, p), 5))
    if d is not None:
        dt = ev.decile_table(y, p)
        out.update(cal_gap=round(float(p.mean() - y.mean()), 4), breaks=int(ev.rank_order_breaks(dt)["n_breaks"]),
                   lift=round(float(dt.iloc[0].lift), 3),
                   min_seg_gini=round(float(ev.segment_performance(y, p, d["dpd_bucket"].reset_index(drop=True)).gini.min()), 4))
    return out


def fit(cols, pairs=(), cfg=None, max_iter=1200):
    cfg = cfg or CFG
    # 2026-09-16: remapped index space — see gam_common.
    cst = interaction_cst_for(cols, CATS, pairs)
    p = dict(learning_rate=0.05, max_leaf_nodes=(3 if not pairs else 4), max_depth=(1 if not pairs else 2),
             l2_regularization=1.0, random_state=0, early_stopping=False,
             categorical_features=[c in CATS for c in cols],
             monotonic_cst=[0 if c in CATS else int(SIGNS.get(c, 0)) for c in cols], interaction_cst=cst, **cfg)
    m = HistGradientBoostingClassifier(**p, max_iter=max_iter).fit(frame(TR, cols), ytr)
    sc = [ev.ks_statistic(yva, q[:, 1])[0] for q in m.staged_predict_proba(frame(VA, cols))]
    it = int(np.argmax(sc)) + 1
    m = HistGradientBoostingClassifier(**p, max_iter=it).fit(frame(TR, cols), ytr)
    return m, it, mets(yva, m.predict_proba(frame(VA, cols))[:, 1])


stages = []      # frozen sub-models for the incremental table (scored on OOT at the end)
cols, pairs = list(START), list(START_PAIRS)
m, it, va = fit(cols, pairs)
say(f"\nSTART (current best GAM) k={len(cols)} pairs={pairs}: valid KS {va['ks']} G {va['gini']} it={it}")
stages.append(("start: current best GAM", list(cols), list(pairs), CFG, m, it, va))

# ── 1. single-add pass over every unused admissible candidate ────────────
say("\n1. single-add validation gains conditioned on the current best GAM")
remaining = [c for c in ADMISSIBLE if c not in cols]
gains = []
for c in remaining:
    _, _, v = fit(cols + [c], pairs, max_iter=500)
    gains.append((round(v["ks"] - va["ks"], 2), round(v["gini"] - va["gini"], 4), c))
gains.sort(reverse=True)
for g, gg, c in gains[:20]:
    say(f"   {c:<36} dKS {g:+.2f}  dGini {gg:+.4f}  (train->valid PSI {psi_va[c]:.3f}, IV {IVT.get(c, float('nan')):.3f})")
say("   negative or zero for the rest:", [(c, g) for g, _, c in gains if g <= 0][:8], "...")

# ── 2. greedy forward on validation KS (>= +0.05 to accept) ──────────────
say("\n2. greedy forward (accept >= +0.05 validation KS)")
accepted = []
for g0, _, c in gains:
    if g0 <= 0 or len(cols) >= 22:
        break
    m2, it2, v2 = fit(cols + [c], pairs)
    d = round(v2["ks"] - va["ks"], 2)
    if d >= 0.05:
        cols.append(c); m, it, va = m2, it2, v2; accepted.append((c, d))
        say(f"   + {c:<34} valid KS {va['ks']} (+{d:.2f})  G {va['gini']}")
        stages.append((f"+ {c}", list(cols), list(pairs), CFG, m, it, va))
    else:
        say(f"   x {c:<34} +{d:.2f} < 0.05, not added")

# ── 3. interactions from a fixed interpretable list ──────────────────────
say("\n3. interactions (accept >= +0.05 validation KS)")
PAIR_LIST = [("disposition_recency_class", "arrears_ratio"), ("latest_disposition", "dpd"),
             ("latest_disposition", "calls_3m"), ("latest_disposition", "cibil_score"),
             ("recent_ptp_status", "ptp_amount_to_emi"), ("paid_ratio_3m", "arrears_ratio"),
             ("last_commit_status", "arrears_ratio"), ("no_answer_streak", "latest_disposition")]
for a, b in PAIR_LIST:
    if (a, b) in pairs:
        continue
    cols2 = list(dict.fromkeys(cols + [a, b]))
    if any(psi_va[x] >= 0.10 for x in (a, b)):
        say(f"   x {a} x {b}: member inadmissible"); continue
    m2, it2, v2 = fit(cols2, pairs + [(a, b)])
    d = round(v2["ks"] - va["ks"], 2)
    if d >= 0.05:
        cols, pairs, m, it, va = cols2, pairs + [(a, b)], m2, it2, v2
        say(f"   + {a} x {b:<28} valid KS {va['ks']} (+{d:.2f})")
        stages.append((f"+ {a} x {b}", list(cols), list(pairs), CFG, m, it, va))
    else:
        say(f"   x {a} x {b:<28} +{d:.2f}")

# ── 4. backward pruning for compactness (drop if it costs < 0.05) ────────
say("\n4. backward pruning (drop a feature if validation KS falls < 0.05)")
changed = True
while changed and len(cols) > 7:
    changed = False
    for c in sorted(cols, key=lambda x: IVT.get(x, 0)):
        if any(c in pr for pr in pairs):
            continue
        cols2 = [x for x in cols if x != c]
        m2, it2, v2 = fit(cols2, pairs)
        if va["ks"] - v2["ks"] < 0.05:
            say(f"   - {c:<34} valid KS {v2['ks']} ({v2['ks'] - va['ks']:+.2f}) dropped")
            cols, m, it, va = cols2, m2, it2, v2; changed = True
            break
if stages[-1][1] != cols:
    stages.append(("after pruning", list(cols), list(pairs), CFG, m, it, va))

# ── 5. smoothing grid on validation ───────────────────────────────────────
say("\n5. smoothing grid (validation KS)")
best_cfg, best = CFG, (m, it, va)
for name, cfg in GRID.items():
    m2, it2, v2 = fit(cols, pairs, cfg=cfg)
    say(f"   {name:<18} valid KS {v2['ks']} G {v2['gini']} it={it2}")
    if v2["ks"] > best[2]["ks"] + 0.05:
        best_cfg, best = cfg, (m2, it2, v2)
m, it, va = best
if best_cfg is not CFG:
    stages.append((f"smoothing {best_cfg}", list(cols), list(pairs), best_cfg, m, it, va))

# ── FREEZE, then OOT once per frozen stage ────────────────────────────────
say(f"\nFROZEN: k={len(cols)} features {cols}\n        pairs {pairs}\n        cfg {best_cfg}  valid KS {va['ks']} G {va['gini']} AUC {va['auc']} Brier {va['brier']}")
psi_oot = {c: ev.psi(TR[c], OOT[c]) for c in cols}
num = [c for c in cols if c not in CATS]
vif = compute_vif(TR[num].fillna(TR[num].median())) if len(num) > 1 else pd.Series(dtype=float)
rows = []
for name, cs, prs, cfg, mm, itt, vv in stages:
    po = mm.predict_proba(frame(OOT, cs))[:, 1]
    ptr = mm.predict_proba(frame(TR, cs))[:, 1]
    o = mets(yoot, po, OOT)
    rows.append(dict(stage=name, k=len(cs), pairs=[f"{a} x {b}" for a, b in prs], valid=vv, oot=o,
                     score_psi=round(ev.psi(ptr, po), 4), features=cs))
    say(f"{name:<40} k={len(cs):<3} valid KS {vv['ks']:5.2f} G {vv['gini']:.4f} | OOT KS {o['ks']:5.2f} G {o['gini']:.4f} "
        f"AUC {o['auc']:.4f} Brier {o['brier']} gap {o['cal_gap']:+} breaks {o['breaks']} lift {o['lift']} minseg {o['min_seg_gini']} PSI {rows[-1]['score_psi']}")
final = rows[-1]
gates = {
    "oot_ks_ge_39": final["oot"]["ks"] >= 39.0, "oot_gini_ge_050": final["oot"]["gini"] >= 0.50,
    "auc_in_band": 0.70 <= final["oot"]["auc"] <= 0.85, "score_psi_lt_010": final["score_psi"] < 0.10,
    "max_csi_lt_010": max(psi_oot.values()) < 0.10, "max_vif_lt_5": bool((vif < 5).all()) if len(vif) else True,
    "no_rank_breaks": final["oot"]["breaks"] == 0, "calibration_abs_gap_lt_003": abs(final["oot"]["cal_gap"]) < 0.03,
    "min_features_7": len(cols) >= 7,
}
gates = {k: bool(v) for k, v in gates.items()}
say("\nHARD GATES on the frozen model:", json.dumps(gates))
say("feature CSI train->OOT:", {c: round(v, 3) for c, v in psi_oot.items()})
say("VIF (numeric inputs):", {c: round(float(v), 2) for c, v in vif.items()})
json.dump(dict(admissible=ADMISSIBLE, excluded={c: psi_va[c] for c in UNIVERSE if psi_va[c] >= 0.10},
               single_add_gains=gains, accepted=accepted, final_features=cols, final_pairs=pairs, cfg=best_cfg,
               stages=rows, gates=gates, csi=psi_oot, vif=vif.to_dict() if len(vif) else {}),
          open(OUT / "search" / "search.json", "w"), indent=1, default=str)
joblib.dump((m, cols, pairs, best_cfg), OUT / "search" / "final.joblib")
say("written")
