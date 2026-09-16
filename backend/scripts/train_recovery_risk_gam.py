"""Fit, gate and write recovery_risk 2.2.0 — the interpretable GAM — under the
frozen protocol. 2026-09-16.

    python -m scripts.train_recovery_risk_gam [--panel data/ledger/obs_wd10/panel.parquet]

NOT a search. The spec (`config.RECOVERY_RISK_GAM`) names fifteen features,
one declared interaction and the sign of every input; this script fits that
model once on the train months, chooses the number of trees on the validation
months, reads the out-of-time months once, and writes what it found. It
exists because the production-readiness audit found the research fit's one
interaction on the wrong pair (sklearn does not remap `interaction_cst` when
it reorders categoricals first). 2.2.0 is a NEW artifact fitted with the pair
where it was declared; the research artifact is left as it was, corrected in
its own report.

What is written beside the model, each versioned and each hashed:

    model.joblib              GamModel — the estimator, its encoding, the
                              background means the explanation is centred on
    calibrator.joblib         SegmentCalibrator on overdue_amount, fitted on
                              VALIDATION only (the same rule as 1.1.0)
    metadata.json             spec, metrics, gates, calibration, bands,
                              reason-code mapping, the `versions` block
    background.csv            the 2,000 train rows (seed 0) the contributions
                              are centred on
    evaluation/…              decile tables, calibration, PSI/CSI, VIF, IV,
                              shape functions, the interaction table, bands

`make_champion` is False and there is no flag to change that here: promotion
is `registry.promote`, with a person's name on it.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")

from app.ml.pipeline import evaluate as ev                               # noqa: E402
from app.ml.pipeline import registry                                     # noqa: E402
from app.ml.pipeline.binning import WOEBinner                            # noqa: E402
from app.ml.pipeline.calibration import SegmentCalibrator                # noqa: E402
from app.ml.pipeline.config import (COMMITMENT_GRACE_DAYS, COMMITMENT_KEPT_RATIO,  # noqa: E402
                                    DISPOSITION_FRESH_DAYS, RECOVERY_RISK_GAM)
from app.ml.pipeline.gam import (FEATURE_LABELS, REASON_CODE_VERSION, ProbabilityBands,  # noqa: E402
                                 _digest, fit_gam, frame_digest, points_from_logit)
from app.ml.pipeline.selection import compute_vif                        # noqa: E402

BUSINESS_TARGET = {"gini_min": 0.50, "ks_min": 39.0}
BACKGROUND_ROWS, BACKGROUND_SEED = 2000, 0


def _raw(df: pd.DataFrame, cols) -> pd.DataFrame:
    """The frame exactly as the adapter would hand it over: object dtype,
    None where the panel has NaN, strings for the categoricals."""
    X = df[list(cols)].copy().reset_index(drop=True)
    X = X.astype(object).where(X.notna(), None)
    return X


def main(panel_path: Path, out_root: Path | None) -> int:
    s = RECOVERY_RISK_GAM
    cols = s.all_features
    cats = list(s.categorical_features)
    panel = pd.read_parquet(panel_path).reset_index(drop=True)
    ds_meta_path = panel_path.parent / "dataset_metadata.json"
    ds_meta = json.loads(ds_meta_path.read_text()) if ds_meta_path.exists() else {}
    missing_cols = [c for c in cols if c not in panel.columns]
    if missing_cols:
        raise SystemExit(f"panel lacks {missing_cols}")
    for f in s.forbidden:
        assert f not in cols, f"forbidden feature {f} in the spec"

    # ── the frozen split ────────────────────────────────────────────────────
    periods = np.sort(panel[s.split_col].unique()); n = len(periods)
    i_tr, i_va = int(n * s.train_frac), int(n * (s.train_frac + s.valid_frac))
    tr_p, va_p, oot_p = periods[:i_tr], periods[i_tr:i_va], periods[i_va:]
    col = panel[s.split_col]
    TR, VA, OOT = (panel[col.isin(p)].reset_index(drop=True) for p in (tr_p, va_p, oot_p))
    y_tr, y_va, y_oot = (d[s.target].to_numpy(int) for d in (TR, VA, OOT))
    print(f"split: train months {tr_p.min()}-{tr_p.max()} n={len(TR):,} | valid {va_p.min()}-{va_p.max()} "
          f"n={len(VA):,} | OOT {oot_p.min()}-{oot_p.max()} n={len(OOT):,}")

    # Categorical vocabulary from TRAIN only; a level OOT alone produces is
    # unseen at serve time too, and must route to missing, not to a code.
    cat_levels = {c: sorted(TR[c].astype(str).unique()) for c in cats}

    # ── fit ─────────────────────────────────────────────────────────────────
    model, it, curve = fit_gam(TR, y_tr, VA, y_va, features=cols, cat_levels=cat_levels,
                               signs=s.expected_sign, pairs=s.interactions)
    print(f"fitted: {it} iterations chosen on validation KS ({max(curve):.2f}); "
          f"{len(model.estimator._predictors)} trees; groups {sorted(set(model.tree_groups()))}")

    Xtr, Xva, Xoot = (_raw(d, cols) for d in (TR, VA, OOT))
    p_tr, p_va, p_oot = (model.predict_proba(X)[:, 1] for X in (Xtr, Xva, Xoot))
    lg_oot = model.decision_function(Xoot)

    # ── calibration, fitted on VALIDATION only ──────────────────────────────
    seg_col = "overdue_amount"
    cal = SegmentCalibrator(segment_col=seg_col, random_state=0)
    cal.fit(p_va, y_va, VA[seg_col].to_numpy(float))
    assert set(VA[s.split_col].unique()) <= set(va_p) and not (set(VA[s.split_col].unique()) & set(oot_p))
    pc_tr = cal.transform(p_tr, TR[seg_col].to_numpy(float))
    pc_va = cal.transform(p_va, VA[seg_col].to_numpy(float))
    pc_oot = cal.transform(p_oot, OOT[seg_col].to_numpy(float))
    cal_report = cal.segment_report(p_oot, y_oot, OOT[seg_col].to_numpy(float))

    # ── evaluation: raw and calibrated, every split ─────────────────────────
    e = {
        "train": ev.evaluate(y_tr, p_tr, p_tr, label="train"),
        "valid": ev.evaluate(y_va, p_va, p_va, label="valid"),
        "oot_uncalibrated": ev.evaluate(y_oot, p_oot, p_oot, label="oot_uncalibrated"),
        "oot": ev.evaluate(y_oot, pc_oot, pc_oot, label="oot"),
        "train_calibrated": ev.evaluate(y_tr, pc_tr, pc_tr, label="train_calibrated"),
        "valid_calibrated": ev.evaluate(y_va, pc_va, pc_va, label="valid_calibrated"),
    }
    tables: dict[str, pd.DataFrame] = {}
    for k, r in e.items():
        tables[f"evaluation/decile_{k}.csv"] = r["decile_table"]
    tables["evaluation/calibration_oot.csv"] = e["oot"]["calibration_table"]
    tables["evaluation/calibration_oot_uncalibrated.csv"] = e["oot_uncalibrated"]["calibration_table"]
    tables["evaluation/calibration_by_segment.csv"] = pd.DataFrame(cal_report)

    # PSI / CSI on the model's inputs, train -> OOT; score PSI on the served number.
    psi_df = ev.psi_frame(TR, OOT, cols, warn=s.gates.psi_warn, fail=s.gates.psi_fail)
    psi_df["in_model"] = True
    tables["evaluation/psi.csv"] = psi_df
    score_psi = float(ev.psi(pc_tr, pc_oot))
    score_psi_raw = float(ev.psi(p_tr, p_oot))
    max_csi = float(psi_df.psi.max())

    # Segments: every spec dimension the panel has; gated where the dimension
    # is not a discretisation of a model input (dpd_bucket bins `dpd`, and
    # `arrears_ratio` is its near-duplicate — reported, still gated, because
    # the research ladder gated it and the number is the same one).
    seg_rows, min_seg = [], None
    for dim in s.segment_cols:
        if dim in OOT.columns:
            sp = ev.segment_performance(y_oot, pc_oot, OOT[dim].reset_index(drop=True))
            sp.insert(0, "dimension", dim)
            seg_rows.append(sp)
    if seg_rows:
        segs = pd.concat(seg_rows, ignore_index=True)
        tables["evaluation/segments_oot.csv"] = segs
        min_seg = float(segs.gini.min())

    # IV (train, the pipeline's own binner) and VIF (raw numerics).
    binner = WOEBinner(numeric=list(s.numeric_features), categorical=cats,
                       trends=WOEBinner.trends_from_signs(s.expected_sign))
    binner.fit(TR[cols], y_tr)
    iv = binner.iv_frame()
    tables["eda/information_value.csv"] = iv
    num = list(s.numeric_features)
    Xn = TR[num].apply(pd.to_numeric, errors="coerce")
    vif = compute_vif(Xn.fillna(Xn.mean())).rename("vif").reset_index().rename(columns={"index": "feature"})
    tables["evaluation/vif.csv"] = vif

    # Monotonicity, checked rather than trusted: every numeric with a declared
    # sign, the shape function on its fitted grid must not move against it.
    mono_rows = []
    for c in num:
        sign = int(s.expected_sign.get(c, 0))
        sf = model.shape_function(c)
        f = sf[sf.value != "<missing>"].f.to_numpy(float)
        d = np.diff(f)
        ok = True if sign == 0 else bool(np.all(sign * d >= -1e-12))
        mono_rows.append({"feature": c, "declared_sign": sign, "range_logits": round(float(f.max() - f.min()), 4),
                          "monotone_as_declared": ok})
    mono = pd.DataFrame(mono_rows)
    tables["evaluation/monotonicity.csv"] = mono
    assert mono.monotone_as_declared.all(), mono

    # ── gates, the spec's own ───────────────────────────────────────────────
    gates = ev.check_gates(e["oot"], s.gates, train_gini=e["train"]["gini"], max_psi=max_csi,
                           min_segment_gini=min_seg)
    passed = bool((gates.result != "FAIL").all())
    tables["evaluation/gates.csv"] = gates

    # ── background, bands, reason codes, points ─────────────────────────────
    bg_rows = TR.sample(BACKGROUND_ROWS, random_state=BACKGROUND_SEED)
    bg_raw = _raw(bg_rows, cols)
    model.fit_background(bg_raw)
    assert set(bg_rows[s.split_col].unique()) <= set(tr_p)           # train months only
    bg_out = bg_raw.copy()
    for c in (list(s.id_cols) + [s.time_col, s.split_col]):
        bg_out.insert(0, c, bg_rows[c].to_numpy())
    tables["background.csv"] = bg_out
    contrib = model.contributions(Xoot)
    recon = float(np.abs(contrib.drop(columns="logit").sum(axis=1).to_numpy() - lg_oot).max())
    sig = float(np.abs(1 / (1 + np.exp(-lg_oot)) - p_oot).max())
    print(f"explanation: max |intercept + contributions - logit| over OOT = {recon:.2e}; "
          f"max |sigmoid(logit) - p| = {sig:.2e}")
    assert recon < 1e-9 and sig < 1e-12

    bands_version = f"recovery-bands-{s.version}"
    bands = ProbabilityBands.fit(pc_va, y_va, version=bands_version,
                                 fitted_on=f"validation months {va_p.min()}-{va_p.max()}, calibrated probability")
    oot_band = bands.assign(pc_oot)
    band_oot = (pd.DataFrame({"band": oot_band, "y": y_oot, "p": pc_oot})
                .groupby("band").agg(count=("y", "size"), bad_rate=("y", "mean"),
                                     mean_probability=("p", "mean")).reset_index())
    tables["evaluation/bands_oot.csv"] = band_oot
    pts = points_from_logit(lg_oot, pdo=s.pdo, base_score=s.base_score, base_odds=s.base_odds)

    # Reference scores for EVERY OOT row and for the background rows, so the
    # serving path can be held to the artifact exactly — with the frozen
    # panel where it exists, and without it (the background rows travel with
    # the artifact) in any checkout.
    ref = pd.DataFrame({"loan_id": OOT[s.id_cols[0]], "as_of_date": OOT[s.time_col].astype(str),
                        "month_index": OOT[s.split_col], "y": y_oot,
                        "logit": lg_oot, "p_raw": p_oot, "p_calibrated": pc_oot,
                        "band": oot_band, "points": pts})
    tables["evaluation/oot_reference_scores.csv"] = ref
    bg_lg = model.decision_function(bg_raw)
    bg_p = model.predict_proba(bg_raw)[:, 1]
    bg_pc = cal.transform(bg_p, pd.to_numeric(bg_raw[seg_col], errors="coerce").to_numpy(float))
    tables["evaluation/background_reference_scores.csv"] = pd.DataFrame({
        "loan_id": bg_rows[s.id_cols[0]].to_numpy(), "logit": bg_lg, "p_raw": bg_p,
        "p_calibrated": bg_pc, "band": bands.assign(bg_pc),
        "points": points_from_logit(bg_lg, pdo=s.pdo, base_score=s.base_score, base_odds=s.base_odds)})

    for c in cols:
        tables[f"evaluation/shape_functions/{c}.csv"] = model.shape_function(c)
    for a, b in s.interactions:
        grid = pd.DataFrame([(u, v) for u in cat_levels.get(a, []) or [None]
                             for v in (np.quantile(pd.to_numeric(TR[b], errors="coerce").dropna(),
                                                   [.05, .25, .5, .75, .95]) if b not in cat_levels else cat_levels[b])],
                            columns=[a, b])
        X = grid.copy()
        for c in cols:
            if c not in X.columns:
                X[c] = None
        # The pair trees' total, and its split: the two marginals (credited to
        # the features) and the residual interaction (what the pair adds).
        rc = model.raw_contributions(X)
        cc = model.contributions(X)
        grid["f_pair_trees_total"] = (rc[f"{a} x {b}"] - model.background_means_[f"{a} x {b}"]).round(4)
        grid["f_interaction_residual"] = cc[f"{a} x {b}"].round(4)
        tables[f"evaluation/interaction_{a}_x_{b}.csv"] = grid

    # ── metadata ────────────────────────────────────────────────────────────
    metrics = {k: {kk: vv for kk, vv in r.items() if not isinstance(vv, pd.DataFrame)}
               for k, r in e.items()}
    ks_oot, gini_oot = e["oot"]["ks"], e["oot"]["gini"]
    feature_definition = {
        "features": cols, "categorical": cats, "cat_levels": cat_levels,
        "abstaining": list(s.abstaining_features),
        "constants": {"COMMITMENT_KEPT_RATIO": COMMITMENT_KEPT_RATIO,
                      "COMMITMENT_GRACE_DAYS": COMMITMENT_GRACE_DAYS,
                      "DISPOSITION_FRESH_DAYS": DISPOSITION_FRESH_DAYS},
        "point_in_time": "every input from events strictly before midnight of the as_of date; "
                         "statuses as known at as_of",
    }
    versions = {
        "model_artifact": s.version,
        "feature_definition": f"recovery-features-{s.version}+{_digest(feature_definition)}",
        "calibration": f"recovery-calibration-{s.version}+{_digest(cal.to_dict())}",
        "risk_bands": f"{bands_version}+{bands.to_dict()['edges_hash']}",
        "reason_codes": f"{REASON_CODE_VERSION}+{_digest(FEATURE_LABELS)}",
        "background": f"recovery-background-{s.version}+{model.background_hash_}",
        "training_data": registry.frame_hash(TR[cols]),
    }
    metadata = {
        "spec": s.to_dict(),
        "model_type": "gam",
        "champion_kind": "gam",
        "selected_features": cols,
        "n_candidate_features": len(cols),
        "gam": model.to_dict(),
        "iterations": {"chosen": it, "rule": "argmax validation KS", "max_probe": 1500,
                       "validation_ks_curve_head": [round(v, 3) for v in curve[:5]],
                       "validation_ks_at_chosen": round(curve[it - 1], 3)},
        "interaction_constraint": {
            "declared_pairs": [list(p) for p in s.interactions],
            "fitted_groups": sorted(set(model.tree_groups())),
            "expressed_in": "sklearn's remapped column order (categoricals first) via gam.interaction_cst_for",
            "defect_reference": "PRODUCTION_READINESS_AUDIT.md — the research fit of 2026-09-15 had the pair on "
                                "latest_disposition x last_commit_status; this artifact has it where declared",
        },
        "calibration": {**cal.to_dict(), "fitted_on": f"validation months {va_p.min()}-{va_p.max()} "
                                                          f"({len(VA):,} rows); OOT never seen"},
        "calibration_by_segment_oot": cal_report,
        "risk_bands": bands.to_dict(),
        "bands_oot": band_oot.round(4).to_dict(orient="records"),
        "reason_codes": {"version": REASON_CODE_VERSION, "labels": FEATURE_LABELS,
                         "rule": "top-4 |centred contribution| >= 0.02 logits, either direction; "
                                 "intercept + all contributions == logit exactly",
                         "background": {"rows": BACKGROUND_ROWS, "seed": BACKGROUND_SEED,
                                        "hash": model.background_hash_, "base_logit": model.base_logit_}},
        "points": {"rule": "offset - factor * logit, the scorecard scaling; display only",
                   "pdo": s.pdo, "base_score": s.base_score, "base_odds": s.base_odds,
                   "oot_range": [int(pts.min()), int(pts.max())]},
        "explanation_reconciliation": {"max_abs_error_oot": recon, "max_abs_sigmoid_error_oot": sig,
                                       "n_rows": int(len(OOT))},
        "metrics": metrics,
        "gate_summary": "PASS" if passed else "FAIL",
        "gates": gates.to_dict(orient="records"),
        "business_target": {**BUSINESS_TARGET, "oot_gini": gini_oot, "oot_ks": ks_oot,
                            "gini_reached": bool(gini_oot >= BUSINESS_TARGET["gini_min"]),
                            "ks_reached": bool(ks_oot >= BUSINESS_TARGET["ks_min"]),
                            "note": "KS below 39 is a documented model-performance limitation of the frozen "
                                    "world (FINAL_SEARCH_REPORT.md), not an implementation defect"},
        "score_psi_train_vs_oot": round(score_psi, 4),
        "score_psi_train_vs_oot_uncalibrated": round(score_psi_raw, 4),
        "max_csi": round(max_csi, 4),
        "csi": psi_df.to_dict(orient="records"),
        "vif": vif.round(3).to_dict(orient="records"),
        "iv": iv[["feature", "iv"]].round(4).to_dict(orient="records") if "iv" in iv.columns else [],
        "monotonicity": mono.to_dict(orient="records"),
        "psi_gated_on": cols,
        "segment_dimensions_gated": [d for d in s.segment_cols if d in OOT.columns],
        "split": {"train": int(len(TR)), "valid": int(len(VA)), "oot": int(len(OOT)),
                  "train_periods": [int(tr_p.min()), int(tr_p.max())],
                  "valid_periods": [int(va_p.min()), int(va_p.max())],
                  "oot_periods": [int(oot_p.min()), int(oot_p.max())]},
        "dataset": {"panel": str(panel_path), "world": ds_meta.get("world"),
                    "intercept": ds_meta.get("intercept"), "config": ds_meta.get("config"),
                    "panel_hash": frame_digest(panel[cols + [s.target, s.split_col]])},
        "feature_definition": feature_definition,
        "versions": versions,
        "training_data_hash": versions["training_data"],
        "is_modelled": True,
        "NOT_PROMOTED": True,
        "SYNTHETIC_WARNING": "Trained on the event-sourced ledger simulator's wd10 world. Every figure here "
                             "describes synthetic borrowers.",
    }
    if out_root is not None:
        registry.ARTIFACT_ROOT = out_root
    out = registry.save(s.name, s.version, pipeline=model, metadata=metadata, tables=tables,
                        extra={"calibrator": cal}, make_champion=False)

    print(f"\n{s.name} {s.version} -> {out}")
    print(f"  OOT (calibrated)  Gini {gini_oot:.4f}  AUC {e['oot']['auc']:.4f}  KS {ks_oot:.2f}  "
          f"Brier {e['oot']['brier']}  gap {e['oot']['calibration_gap']:+}  breaks {e['oot']['rank_order']['n_breaks']}")
    r = e["oot_uncalibrated"]
    print(f"  OOT (raw)         Gini {r['gini']:.4f}  AUC {r['auc']:.4f}  KS {r['ks']:.2f}  Brier {r['brier']}  "
          f"gap {r['calibration_gap']:+}")
    print(f"  valid Gini {e['valid']['gini']:.4f} KS {e['valid']['ks']:.2f} | train Gini {e['train']['gini']:.4f}")
    print(f"  score PSI {score_psi:.4f}  max CSI {max_csi:.4f}  max VIF {vif.vif.max():.2f}  min seg Gini {min_seg}")
    print(f"  gates: {'PASS' if passed else 'FAIL'}; business target Gini>=0.50 "
          f"{'yes' if gini_oot >= .5 else 'NO'}, KS>=39 {'yes' if ks_oot >= 39 else 'NO'}")
    print(f"  bands: {bands.edges}")
    print(gates.to_string(index=False))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(BACKEND / "data" / "ledger" / "obs_wd10" / "panel.parquet"))
    ap.add_argument("--out-root", default=None, help="write under another artifact root (tests)")
    a = ap.parse_args()
    sys.exit(main(Path(a.panel), Path(a.out_root) if a.out_root else None))
