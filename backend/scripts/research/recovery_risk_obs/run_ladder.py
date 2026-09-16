"""The observability experiment ladder (Section 10).

    python scripts/research/recovery_risk_obs/run_ladder.py OUT_DIR [exp1 exp2 exp3 exp4]

Each experiment is the PRODUCTION trainer (`ModelTrainer`: WOE with forced
trends -> IV/corr/VIF/SFS-on-valid/sign -> LR -> segment calibration -> gates
-> GBM challenger) on a scratch spec, artifacts redirected under OUT_DIR so
nothing lands in app/ml/artifacts. Per experiment it also measures the
world's observable ceiling (GBM on every legitimate candidate) and the oracle
(w + cap + reach + observables) on the same OOT rows.

    EXP1  world w1 (channels on)                 candidates = 2.1.0 + channel aggregates
    EXP2  world w2 (channels on + coverage)      same candidates
    EXP3  world w2                               + 30d/90d recency features
    EXP4  world w2                               + payment / capacity features
"""
from __future__ import annotations

import json
import sys
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")

from app.ml.pipeline import evaluate as ev, registry               # noqa: E402
from app.ml.pipeline.config import (                              # noqa: E402
    FEED_ONLY_FEATURES, NO_HISTORY_FEATURES, RECOVERY_RISK_V21,
)
from app.ml.pipeline.train import ModelTrainer                     # noqa: E402

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
registry.ARTIFACT_ROOT = OUT / "artifacts"          # scratch, never the repo
DATA = BACKEND / "data" / "ledger"

# ── candidate sets ─────────────────────────────────────────────────────────
CHANNEL_NUM = ("declined_rate_6m", "mean_call_duration_6m", "last_call_duration",
               "commitments_6m", "commit_kept_ratio_6m", "commit_broken_6m",
               "commit_live_at_asof")
CHANNEL_CAT = ("last_commit_status",)
RECENCY_NUM = ("intent_rate_30d", "intent_rate_90d", "declined_rate_30d",
               "declined_rate_90d", "answered_rate_30d", "calls_30d",
               "answered_calls_30d", "days_since_positive_intent",
               "days_since_negative_intent", "days_since_last_successful_contact",
               "visits_30d", "met_visits_30d", "ptp_conversion_90d",
               "commitments_90d", "commit_kept_ratio_90d", "commit_kept_90d",
               "commit_broken_90d", "days_since_commit_kept",
               "days_since_commit_broken", "mean_call_duration_30d")
RECENCY_CAT = ("recent_visit_outcome_30d", "recent_ptp_status")
SWEEP_NUM = ("calls_3d", "answered_3d", "declined_3d", "intent_3d", "duration_3d", "reached_3d")
DISP_NUM = ("latest_disposition_score", "days_since_disposition",
            "positive_disposition_rate_30d", "negative_disposition_rate_30d",
            "disposition_count_30d", "disposition_trend_90d")
DISP_CAT = ("latest_disposition", "disposition_3d")
DISP2_NUM = ("disposition_score_decayed",)
DISP2_CAT = ("disposition_recency_class",)
PAYCAP_NUM = ("payment_momentum_30_vs_90", "payment_count_30d", "partial_rate_90d",
              "payment_amount_trend", "payment_gap_mean_12m", "hardship_flag_90d",
              "days_since_hardship")
# The freshness ladder (2026-09-15, last brief): the same base universe as
# d10 WITHOUT the reading, then the reading added in four steps.
F_BASE_NUM = CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM
F_BASE_CAT = CHANNEL_CAT + RECENCY_CAT
F1_NUM, F1_CAT = ("days_since_disposition",), ("latest_disposition",)
F2_CAT = ("disposition_3d", "disposition_7d", "disposition_30d")
F3_NUM = ("positive_disposition_rate_30d", "negative_disposition_rate_30d")
F4_NUM = ("fresh_positive_disposition", "fresh_negative_disposition",
          "latest_disposition_score", "disposition_score_decayed", "disposition_trend_90d")
F4_CAT = ("disposition_recency_class",)
SIGNS = {
    "declined_rate_6m": +1, "declined_rate_90d": +1, "declined_rate_30d": +1,
    "mean_call_duration_6m": -1, "mean_call_duration_30d": -1, "last_call_duration": -1,
    "commitments_6m": 0, "commitments_90d": 0,
    "commit_kept_ratio_6m": -1, "commit_kept_ratio_90d": -1, "commit_kept_90d": -1,
    "commit_broken_6m": +1, "commit_broken_90d": +1, "commit_live_at_asof": -1,
    "days_since_commit_kept": +1, "days_since_commit_broken": 0,
    "intent_rate_30d": -1, "intent_rate_90d": -1, "answered_rate_30d": -1,
    "calls_30d": 0, "answered_calls_30d": 0,
    "days_since_positive_intent": +1, "days_since_negative_intent": 0,
    "days_since_last_successful_contact": 0, "visits_30d": 0, "met_visits_30d": 0,
    "ptp_conversion_90d": -1,
    "payment_momentum_30_vs_90": -1, "payment_count_30d": -1, "partial_rate_90d": +1,
    "payment_amount_trend": -1, "payment_gap_mean_12m": +1, "hardship_flag_90d": +1,
    "days_since_hardship": 0,
    "calls_3d": 0, "answered_3d": -1, "declined_3d": +1, "intent_3d": -1,
    "duration_3d": -1, "reached_3d": -1,
    "latest_disposition_score": -1, "days_since_disposition": +1,
    "positive_disposition_rate_30d": -1, "negative_disposition_rate_30d": +1,
    "disposition_count_30d": 0, "disposition_trend_90d": -1,
    "disposition_score_decayed": -1,
    "fresh_positive_disposition": -1, "fresh_negative_disposition": +1,
}
ABSTAIN = ("declined_rate_6m", "declined_rate_90d", "declined_rate_30d",
           "mean_call_duration_6m", "mean_call_duration_30d", "last_call_duration",
           "commit_kept_ratio_6m", "commit_kept_ratio_90d", "days_since_commit_kept",
           "days_since_commit_broken", "intent_rate_30d", "intent_rate_90d",
           "answered_rate_30d", "days_since_positive_intent", "days_since_negative_intent",
           "days_since_last_successful_contact", "ptp_conversion_90d",
           "partial_rate_90d", "payment_amount_trend", "payment_gap_mean_12m",
           "days_since_hardship", "intent_3d", "duration_3d", "reached_3d",
           "latest_disposition_score", "days_since_disposition",
           "positive_disposition_rate_30d", "negative_disposition_rate_30d",
           "disposition_trend_90d", "disposition_score_decayed")

BASE = RECOVERY_RISK_V21
EXPS = {
    "exp1": ("w1", CHANNEL_NUM, CHANNEL_CAT),
    "exp2": ("w2", CHANNEL_NUM, CHANNEL_CAT),
    "exp3": ("w2", CHANNEL_NUM + RECENCY_NUM, CHANNEL_CAT + RECENCY_CAT),
    "exp4": ("w2", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM, CHANNEL_CAT + RECENCY_CAT),
    # Attribution runs on the coverage-unchanged world, because EXP2 showed
    # the extra contact lowers the world's own predictability.
    "exp3b": ("w1", CHANNEL_NUM + RECENCY_NUM, CHANNEL_CAT + RECENCY_CAT),
    "exp4b": ("w1", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM, CHANNEL_CAT + RECENCY_CAT),
    "exp1z": ("w1z", CHANNEL_NUM, CHANNEL_CAT),
    # EXP5 — the pre-scoring sweep world, with the 3-day reading features.
    "exp5": ("w3", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM, CHANNEL_CAT + RECENCY_CAT),
    "exp6": ("w4", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM, CHANNEL_CAT + RECENCY_CAT),
    # The disposition ladder. Same candidate universe as EXP6 plus the reading.
    "d_off": ("wd_off", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM, CHANNEL_CAT + RECENCY_CAT),
    "d30": ("wd30", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM + DISP_NUM, CHANNEL_CAT + RECENCY_CAT + DISP_CAT),
    "d20": ("wd20", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM + DISP_NUM, CHANNEL_CAT + RECENCY_CAT + DISP_CAT),
    "d10": ("wd10", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM + DISP_NUM, CHANNEL_CAT + RECENCY_CAT + DISP_CAT),
    # Freshness-aware forms of the reading (Section 10: ceiling >= 0.50, scorecard below).
    "d30b": ("wd30", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM + DISP_NUM + DISP2_NUM, CHANNEL_CAT + RECENCY_CAT + DISP_CAT + DISP2_CAT),
    "d20b": ("wd20", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM + DISP_NUM + DISP2_NUM, CHANNEL_CAT + RECENCY_CAT + DISP_CAT + DISP2_CAT),
    "d10b": ("wd10", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM + SWEEP_NUM + DISP_NUM + DISP2_NUM, CHANNEL_CAT + RECENCY_CAT + DISP_CAT + DISP2_CAT),
    # Freshness ladder on the frozen wd10 world; f4c = the one coverage experiment.
    "f1": ("wd10", F_BASE_NUM + F1_NUM, F_BASE_CAT + F1_CAT),
    "f2": ("wd10", F_BASE_NUM + F1_NUM, F_BASE_CAT + F1_CAT + F2_CAT),
    "f3": ("wd10", F_BASE_NUM + F1_NUM + F3_NUM, F_BASE_CAT + F1_CAT + F2_CAT),
    "f4": ("wd10", F_BASE_NUM + F1_NUM + F3_NUM + F4_NUM, F_BASE_CAT + F1_CAT + F2_CAT + F4_CAT),
    "f4c": ("wd10c", F_BASE_NUM + F1_NUM + F3_NUM + F4_NUM, F_BASE_CAT + F1_CAT + F2_CAT + F4_CAT),
    # What-ifs (Section 12.7) — measured, not adopted.
    "wi_chan": ("wi_chan", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM, CHANNEL_CAT + RECENCY_CAT),
    "wi_rho": ("wi_rho", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM, CHANNEL_CAT + RECENCY_CAT),
    "wi_obs": ("wi_obs", CHANNEL_NUM + RECENCY_NUM + PAYCAP_NUM, CHANNEL_CAT + RECENCY_CAT),
}
LAT = ["willingness", "capacity", "reachability"]
NOT_FEATURES = {"loan_id", "borrower_id", "as_of_date", "month_index", "y",
                "recovered_amount", "outcome_threshold", "baseline_overdue_amount",
                "baseline_emi_amount"} | set(FEED_ONLY_FEATURES) | set(NO_HISTORY_FEATURES) | {
    "monthly_income", "dti_ratio", "credit_vintage_months", "num_open_loans",
    "num_enquiries_6m", "other_lender_delinq", "utilization_pct", "mail_returned_count",
    "address_vintage_months", "phone_verified", "thin_file", "sourcing_channel",
    "residence_type", "is_secured"}


def _csv_records(path: Path) -> list:
    try:
        return pd.read_csv(path).to_dict(orient="records")
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return []


def gk(y, p):
    ks, _ = ev.ks_statistic(y, p)
    return round(2 * roc_auc_score(y, p) - 1, 4), round(ks, 2)


def _split(panel, spec):
    periods = np.sort(panel[spec.split_col].unique()); n = len(periods)
    i_tr, i_va = int(n * spec.train_frac), int(n * (spec.train_frac + spec.valid_frac))
    col = panel[spec.split_col]
    return (panel[col.isin(periods[:i_tr])], panel[col.isin(periods[i_tr:i_va])],
            panel[col.isin(periods[i_va:])])


def ceilings(panel: pd.DataFrame, world: str, spec) -> dict:
    """GBM on every legitimate candidate in this panel; oracle with latents."""
    gt = pd.read_parquet(DATA / f"obs_{world}" / "ground_truth.parquet")
    gt["month_index"] = gt.day // 30
    df = panel.merge(gt[["loan_id", "month_index"] + LAT], on=["loan_id", "month_index"], how="left")
    cands = [c for c in panel.columns if c not in NOT_FEATURES]
    cats = [c for c in cands if df[c].dtype == object]
    tr, va, oot = _split(df, spec)
    ytr, yva, yoot = (d.y.to_numpy(int) for d in (tr, va, oot))

    def frame(d, cols):
        X = d[cols].copy()
        for c in cols:
            if c in cats:
                X[c] = pd.Categorical(X[c].astype(str), categories=sorted(df[c].astype(str).unique())).codes
        return X

    def gbm(cols):
        p = dict(max_iter=600, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=200,
                 l2_regularization=1.0, random_state=0, early_stopping=False)
        cf = [c in cats for c in cols]
        m = HistGradientBoostingClassifier(**p, categorical_features=cf).fit(frame(tr, cols), ytr)
        sc = [roc_auc_score(yva, q[:, 1]) for q in m.staged_predict_proba(frame(va, cols))]
        it = int(np.argmax(sc)) + 1
        m = HistGradientBoostingClassifier(**{**p, "max_iter": it}, categorical_features=cf).fit(frame(tr, cols), ytr)
        return gk(yoot, m.predict_proba(frame(oot, cols))[:, 1])

    obs = gbm(cands)
    joint = gbm(cands + LAT)
    w_only = gbm(cands + ["willingness"])
    return {"n_candidates": len(cands), "observable_ceiling": obs, "joint_oracle": joint,
            "observables_plus_true_willingness": w_only}


def run(exp: str) -> dict:
    world, num, cat = EXPS[exp]
    panel = pd.read_parquet(DATA / f"obs_{world}" / "panel.parquet")
    meta = json.loads((DATA / f"obs_{world}" / "dataset_metadata.json").read_text())
    if "latest_disposition" in panel.columns and "disposition_recency_class" not in panel.columns:
        from app.ml.simulation.ledger.panel import _recency_class
        panel["disposition_recency_class"] = _recency_class(
            panel.latest_disposition.to_numpy(), panel.days_since_disposition.to_numpy())
        panel["disposition_score_decayed"] = np.round(
            panel.latest_disposition_score.to_numpy() * np.power(0.5, panel.days_since_disposition.to_numpy() / 14.0), 3)
    spec = replace(
        BASE, version=exp,
        numeric_features=BASE.numeric_features + tuple(num),
        categorical_features=BASE.categorical_features + tuple(cat),
        expected_sign={**BASE.expected_sign, **{k: v for k, v in SIGNS.items() if k in num}},
        abstaining_features=BASE.abstaining_features + tuple(a for a in ABSTAIN if a in num),
        description=f"observability ladder {exp} on world {world}")
    missing = [f for f in spec.all_features if f not in panel.columns]
    assert not missing, missing
    print(f"\n{'='*78}\n  {exp.upper()}  world {world}  rows {len(panel):,}  bad {panel.y.mean():.4f}  "
          f"candidates {len(spec.all_features)}\n{'='*78}", flush=True)
    res = ModelTrainer(spec, panel, dataset_meta=meta).run(make_champion=False)
    m = res.metrics
    d = res.artifact_dir
    art = json.loads((d / "metadata.json").read_text())
    iv = pd.read_csv(d / "eda" / "information_value.csv").set_index("feature")
    vif = pd.read_csv(d / "evaluation" / "vif.csv")
    psi = pd.read_csv(d / "evaluation" / "psi.csv").set_index("feature")
    seg = pd.read_csv(d / "evaluation" / "segment_performance.csv")
    sfs = pd.read_csv(d / "evaluation" / "sfs_path.csv")
    sel = res.selected
    out = {
        "exp": exp, "world": world, "n_rows": len(panel), "bad_rate": round(float(panel.y.mean()), 4),
        "n_candidates": len(spec.all_features), "selected": sel, "n_selected": len(sel),
        "train": {k: m["train"][k] for k in ("gini", "auc", "ks")},
        "valid": {k: m["valid"][k] for k in ("gini", "auc", "ks")},
        "oot": {k: m["oot"][k] for k in ("gini", "auc", "ks", "brier", "calibration_gap",
                                          "top_decile_lift", "bad_rate", "n")},
        "oot_rank_order_breaks": m["oot"]["rank_order"]["n_breaks"],
        "oot_challenger_gbm_gini": m["oot_challenger"]["gini"],
        "champion_kind": res.champion_kind,
        "bootstrap_ci": art["bootstrap_gini_oot"],
        "iv_selected": {f: round(float(iv.loc[f, "iv"]), 4) for f in sel},
        "iv_min_selected": round(float(iv.loc[sel, "iv"].min()), 4),
        "iv_max_selected": round(float(iv.loc[sel, "iv"].max()), 4),
        "max_vif": round(float(vif[vif.action == "kept"].vif.max()), 3),
        "score_psi": art["score_psi_train_vs_oot"],
        "max_csi_selected": round(float(psi.loc[sel, "psi"].max()), 4),
        "min_segment_gini": round(float(seg[seg.gated].gini.min()), 4),
        "monotone_selected": bool(iv.loc[sel, "monotonic"].all()),
        "gates_passed": bool(res.passed),
        "sfs_path": sfs.to_dict(orient="records"),
        "sign_dropped": _csv_records(d / "evaluation" / "sign_dropped.csv"),
        "realism_passed": meta["realism"]["passed"],
        "realism": {c["check"]: c["value"] for c in meta["realism"]["checks"]},
    }
    out.update(ceilings(panel, world, spec))
    print(f"  selected {len(sel)}: {', '.join(sel)}")
    print(f"  OOT Gini {out['oot']['gini']}  AUC {out['oot']['auc']}  KS {out['oot']['ks']}  "
          f"Brier {out['oot']['brier']}  gap {out['oot']['calibration_gap']:+}  "
          f"| GBM challenger {out['oot_challenger_gbm_gini']}")
    print(f"  IV {out['iv_min_selected']}-{out['iv_max_selected']}  VIF {out['max_vif']}  "
          f"PSI {out['score_psi']}  CSI {out['max_csi_selected']}  min seg {out['min_segment_gini']}  "
          f"breaks {out['oot_rank_order_breaks']}  gates {'PASS' if res.passed else 'FAIL'}")
    print(f"  ceiling {out['observable_ceiling']}  +willingness {out['observables_plus_true_willingness']}  "
          f"oracle {out['joint_oracle']}  ({out['n_candidates']} candidates)", flush=True)
    (OUT / f"{exp}.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    for e in (sys.argv[2:] or list(EXPS)):
        run(e)
