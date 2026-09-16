"""Section 7 — is the structured disposition a REALISTIC observation?

    python scripts/research/recovery_risk_obs/disposition_audit.py wd30 wd20 wd10

Per world: distribution, coverage (3d / 30d / ever), missingness, stability
train vs OOT (categorical CSI), relation to the willingness latent (mean w by
class, Spearman of the ordinal score against w at as_of), relation to the
outcome (bad rate by class — must be far from 0/1), confusion against the
noiseless class implied by w at as_of (FP/FN rates), and two leakage checks:
the reading's partial association with y GIVEN true willingness, and a
structural PIT check that no reading on or after as_of enters a feature.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
from app.ml.pipeline import evaluate as ev                       # noqa: E402
from app.ml.simulation.ledger.panel import DISPOSITION_SCORE     # noqa: E402

DATA = BACKEND / "data" / "ledger"
POS = ("WILL_PAY", "MAY_PAY")
NEG = ("REFUSES", "DISPUTE")


def true_class(w, cfg):
    return np.where(w >= cfg["disposition_cut_will"], "WILL_PAY",
           np.where(w >= cfg["disposition_cut_may"], "MAY_PAY",
           np.where(w >= cfg["disposition_cut_nocommit"], "NO_COMMITMENT", "REFUSES")))


def audit(world: str) -> dict:
    d = DATA / f"obs_{world}"
    panel = pd.read_parquet(d / "panel.parquet")
    calls = pd.read_parquet(d / "calls.parquet")
    visits = pd.read_parquet(d / "visits.parquet")
    gt = pd.read_parquet(d / "ground_truth.parquet"); gt["month_index"] = gt.day // 30
    cfg = json.loads((d / "dataset_metadata.json").read_text())["config"]
    months = np.sort(panel.month_index.unique()); n = len(months)
    tr = panel[panel.month_index.isin(months[:int(n * .6)])]
    oot = panel[panel.month_index.isin(months[int(n * .75):])]
    p = panel[panel.month_index > 0]              # month 0 has no history by construction
    m = p.merge(gt[["loan_id", "month_index", "willingness"]], on=["loan_id", "month_index"], how="left")
    out: dict = {"world": world, "read_noise": cfg.get("disposition_read_noise")}

    readings = pd.concat([calls.disposition.dropna(), visits.disposition.dropna()])
    out["n_readings"] = int(len(readings))
    out["readings_per_account_month"] = round(len(readings) / len(p), 2)
    out["distribution"] = readings.value_counts(normalize=True).round(3).to_dict()
    out["coverage_3d"] = round(float((p.disposition_3d != "NONE").mean()), 3)
    out["coverage_30d"] = round(float((p.disposition_count_30d > 0).mean()), 3)
    out["coverage_ever"] = round(float((p.latest_disposition != "NONE").mean()), 3)
    out["median_days_since_reading"] = float(p.days_since_disposition.median())
    out["csi_latest_disposition_train_vs_oot"] = round(ev.psi(tr.latest_disposition, oot.latest_disposition), 4)
    out["csi_disposition_3d_train_vs_oot"] = round(ev.psi(tr.disposition_3d, oot.disposition_3d), 4)

    has = m[m.disposition_3d != "NONE"]
    out["mean_willingness_by_disposition_3d"] = has.groupby("disposition_3d").willingness.mean().round(3).to_dict()
    out["bad_rate_by_disposition_3d"] = has.groupby("disposition_3d").y.mean().round(3).to_dict()
    out["bad_rate_by_latest_disposition"] = m.groupby("latest_disposition").y.mean().round(3).to_dict()
    sc = has.disposition_3d.map(DISPOSITION_SCORE)
    out["spearman_score_vs_willingness_at_asof"] = round(float(spearmanr(sc, has.willingness).correlation), 3)

    # confusion against the noiseless class from w at as_of (<= 3 days later)
    ordinal = has[~has.disposition_3d.isin(("HARDSHIP", "DISPUTE"))]
    tc = true_class(ordinal.willingness.to_numpy(), cfg)
    rec = ordinal.disposition_3d.to_numpy()
    out["exact_class_agreement"] = round(float((tc == rec).mean()), 3)
    pos_true = np.isin(tc, POS); pos_rec = np.isin(rec, POS)
    out["false_positive_rate_positive_stance"] = round(float((pos_rec & ~pos_true).sum() / max((~pos_true).sum(), 1)), 3)
    out["false_negative_rate_positive_stance"] = round(float((~pos_rec & pos_true).sum() / max(pos_true.sum(), 1)), 3)
    out["hardship_or_dispute_override_share"] = round(float(has.disposition_3d.isin(("HARDSHIP", "DISPUTE")).mean()), 3)

    # leakage: association with y GIVEN true willingness at as_of
    from sklearn.linear_model import LogisticRegression
    X1 = has[["willingness"]].to_numpy()
    X2 = np.c_[has.willingness.to_numpy(), sc.to_numpy()]
    y = has.y.to_numpy(int)
    g1 = ev.gini(y, -LogisticRegression().fit(X1, y).predict_proba(X1)[:, 1])
    g2 = ev.gini(y, -LogisticRegression().fit(X2, y).predict_proba(X2)[:, 1])
    out["gini_true_w_only"] = round(abs(g1), 4)
    out["gini_true_w_plus_reading"] = round(abs(g2), 4)
    out["reading_adds_beyond_true_w"] = round(abs(g2) - abs(g1), 4)
    out["uses_only_readings_before_as_of"] = "day < t by construction (panel._before); tests/test_ledger_observability.py"
    return out


if __name__ == "__main__":
    for w in sys.argv[1:]:
        r = audit(w)
        print(json.dumps(r, indent=1))
        (DATA / f"obs_{w}" / "disposition_audit.json").write_text(json.dumps(r, indent=1))
