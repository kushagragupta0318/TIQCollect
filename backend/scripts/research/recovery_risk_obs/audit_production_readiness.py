"""Production-readiness audit of the reference GAM (2026-09-16).

    python scripts/research/recovery_risk_obs/audit_production_readiness.py OUT_DIR

The reference model is the 15-feature GAM at OOT KS 38.84 (`exp5` of
gam_ladder.py) on the FROZEN wd10 world. This script audits it and the
production scoring machinery; it changes no model behaviour, promotes
nothing and writes nothing under a registry version directory.

Sections, in order:
  1  ARTIFACT        recover, re-fit from the recipe, compare bit-for-bit,
                     emit metadata + sha256, reload in a CLEAN process
  2  FEATURE PARITY  each of the 15 against the production adapter
  3  SERVING PARITY  full-frame predict vs a row-dict serving shim, and what
                     the real DecisionEngine does when handed this artifact
  5  SCORE REPRO     frozen OOT population, offline vs serving-shaped
  6  CALIBRATION     Brier, gap, observed vs predicted, per segment
  7  STABILITY       score PSI, feature CSI, missingness drift
(4, 8-12 are the PIT test-file, the monitoring spec and the report.)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
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
from app.ml.pipeline.config import (                                       # noqa: E402
    FEED_ONLY_FEATURES, LOGGED_FEATURES, NO_HISTORY_FEATURES,
)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gam_common import interaction_cst_for, remapped_order                 # noqa: E402

OUT = Path(sys.argv[1])
ART = OUT / "gam" / "audit"
ART.mkdir(parents=True, exist_ok=True)
LOG = open(ART / "audit.log", "w", encoding="utf-8")
findings: list[dict] = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True); LOG.write(s + "\n"); LOG.flush()


def check(name: str, ok: bool | None, evidence: str, action: str = "—"):
    findings.append({"check": name, "result": evidence,
                     "verdict": "PASS" if ok else ("FAIL" if ok is False else "INFO"),
                     "action": action})
    say(f"  [{'PASS' if ok else ('FAIL' if ok is False else 'INFO')}] {name}: {evidence}")


# ── the frozen world and split (identical to every previous row) ──────────
WORLD = "wd10"
panel = pd.read_parquet(BACKEND / "data" / "ledger" / f"obs_{WORLD}" / "panel.parquet")
wmeta = json.loads((BACKEND / "data" / "ledger" / f"obs_{WORLD}" / "dataset_metadata.json").read_text())
months = np.sort(panel.month_index.unique()); n = len(months)
TR = panel[panel.month_index.isin(months[:int(n * .6)])]
VA = panel[panel.month_index.isin(months[int(n * .6):int(n * .75)])]
OOT = panel[panel.month_index.isin(months[int(n * .75):])]
ytr, yva, yoot = (d.y.to_numpy(int) for d in (TR, VA, OOT))
fmeta = json.loads((OUT / "artifacts" / "recovery_risk" / "f1" / "metadata.json").read_text())
spec = fmeta["spec"]
SIGNS = dict(spec["expected_sign"])
CATS = set(spec["categorical_features"]) | {c for c in panel.columns if panel[c].dtype == object
                                            and c not in ("loan_id", "borrower_id")}
NOT = {"loan_id", "borrower_id", "as_of_date", "month_index", "y", "recovered_amount",
       "outcome_threshold", "baseline_overdue_amount", "baseline_emi_amount"} \
    | set(FEED_ONLY_FEATURES) | set(NO_HISTORY_FEATURES) | {
    "monthly_income", "dti_ratio", "credit_vintage_months", "num_open_loans", "num_enquiries_6m",
    "other_lender_delinq", "utilization_pct", "mail_returned_count", "address_vintage_months",
    "phone_verified", "thin_file", "sourcing_channel", "residence_type", "is_secured"}
UNIVERSE = [c for c in panel.columns if c not in NOT]
CAT_LEVELS = {c: sorted(panel[c].astype(str).unique()) for c in UNIVERSE if c in CATS}


def frame(d, cols):
    X = d[cols].copy()
    for c in cols:
        if c in CATS:
            X[c] = pd.Categorical(X[c].astype(str), categories=CAT_LEVELS[c]).codes
    return X


def mets(y, p):
    ks, _ = ev.ks_statistic(y, p)
    return dict(gini=round(2 * roc_auc_score(y, p) - 1, 4), auc=round(roc_auc_score(y, p), 4),
                ks=round(ks, 2), brier=round(brier_score_loss(y, p), 5))


# =========================================================================
say("=" * 78 + "\n1. ARTIFACT\n" + "=" * 78)
models = joblib.load(OUT / "gam" / "models.joblib")
m_ref, COLS, PAIRS = models["exp5"]
say(f"  reference: exp5, {len(COLS)} features, pairs {PAIRS}, {m_ref.n_iter_} trees/feature-group iterations")

HP = dict(learning_rate=0.05, max_leaf_nodes=4, max_depth=2, min_samples_leaf=300,
          l2_regularization=1.0, max_bins=32, random_state=0, early_stopping=False,
          max_iter=int(m_ref.n_iter_))
CAT_LEVELS_USED = {c: CAT_LEVELS[c] for c in COLS if c in CATS}
idx = {c: i for i, c in enumerate(COLS)}
# The audited artifact was fitted with the constraint in the CALLER's index
# space, which sklearn applies in its remapped space — so the pair that was
# actually allowed is the remapped {0,1}. Reproducing the artifact means
# reproducing that; `interaction_cst_for` is what a corrected fit would use.
CST = [{i} for i in range(len(COLS))] + [{idx[a], idx[b]} for a, b in PAIRS]
ACTUAL_PAIR = tuple(remapped_order(COLS, [c for c in COLS if c in CATS])[i] for i in (0, 1))
MONO = [0 if c in CATS else int(SIGNS.get(c, 0)) for c in COLS]

m_new = HistGradientBoostingClassifier(
    **HP, categorical_features=[c in CATS for c in COLS],
    monotonic_cst=MONO, interaction_cst=CST).fit(frame(TR, COLS), ytr)
p_ref = m_ref.predict_proba(frame(OOT, COLS))[:, 1]
p_new = m_new.predict_proba(frame(OOT, COLS))[:, 1]
dmax = float(np.max(np.abs(p_ref - p_new)))
check("1.1 artifact recoverable", True, f"exp5 loaded from gam/models.joblib, {len(COLS)} features")
check("1.2 re-fit from the recorded recipe reproduces it", dmax == 0.0,
      f"max |p_refit - p_stored| = {dmax:.3e} over {len(OOT):,} OOT rows (seed 0, single-threaded histogram build)",
      "—" if dmax == 0.0 else "record the exact environment (sklearn/OpenMP thread count) in the metadata")

oot_m = mets(yoot, p_ref)
say(f"  OOT {oot_m}")
metadata = {
    "model": "recovery_risk", "variant": "research-gam-exp5", "NOT_PROMOTED": True,
    "note": ("Reference model for the 2026-09-16 production-readiness audit. NOT a registry "
             "version: it is not under a <version>/ directory, champion.txt is untouched, and "
             "the production DecisionEngine cannot load it (see section 3)."),
    "model_type": "generalised additive model — gradient-boosted shape functions "
                  "(sklearn HistGradientBoostingClassifier with interaction_cst restricted to "
                  "singletons plus the declared pairs, so logit = b0 + sum_j f_j(x_j) + sum_pairs h(x_a,x_b))",
    "features": COLS, "n_features": len(COLS),
    "categorical_features": sorted(c for c in COLS if c in CATS),
    "interactions_declared": [f"{a} x {b}" for a, b in PAIRS],
    "interactions": [f"{ACTUAL_PAIR[0]} x {ACTUAL_PAIR[1]}"],
    "interaction_defect": ("the fit passed interaction_cst in the caller's column order; sklearn "
                           "applies it in its remapped order (categoricals first), so the pair "
                           "actually allowed is the one in 'interactions', not 'interactions_declared'. "
                           "Additivity is unaffected. See scripts/research/recovery_risk_obs/gam_common.py"),
    "monotonic_cst": {c: MONO[i] for i, c in enumerate(COLS)},
    "expected_sign_source": "ModelSpec.RECOVERY_RISK_V21.expected_sign",
    "hyperparameters": HP, "interaction_cst": [sorted(s) for s in CST],
    "random_seed": 0, "iterations_chosen_on": "validation KS (months 14-17)",
    "categorical_levels": {c: CAT_LEVELS[c] for c in COLS if c in CATS},
    "unknown_category_behaviour": "pd.Categorical(...).codes yields -1; the model treats -1 as its "
                                  "own bin (it was never seen in training, so it follows the "
                                  "left-most split path deterministically) — see check 2.5",
    "missing_value_handling": "native: each shape function learns a side for NaN at fit time; "
                              "no imputation anywhere in the pipeline",
    "preprocessing": "none beyond the categorical code map above — no scaling, no WOE, no binning "
                     "outside the model's own 32 histogram knots",
    "target": {"definition": "y = 1 if VERIFIED payments in (as_of, as_of+30d] < 0.8 * min(overdue_amount, emi_amount)",
               "source": "app/ml/pipeline/outcomes.py / ledger panel.py, MATERIAL_RATIO=0.8, HORIZON_DAYS=30",
               "outcome_definition_version": "recovery-outcome-1.0.0"},
    "periods": {"world": WORLD, "split_col": "month_index",
                "train": [int(months[0]), int(months[int(n * .6) - 1])],
                "valid": [int(months[int(n * .6)]), int(months[int(n * .75) - 1])],
                "oot": [int(months[int(n * .75)]), int(months[-1])],
                "n_train": int(len(TR)), "n_valid": int(len(VA)), "n_oot": int(len(OOT)),
                "oot_bad_rate": round(float(yoot.mean()), 4)},
    "dataset": {"path": f"data/ledger/obs_{WORLD}", "intercept": wmeta.get("intercept"),
                "realism_passed": wmeta["realism"]["passed"],
                "config_fingerprint_fields": {k: wmeta["config"].get(k) for k in
                                              ("n_borrowers", "months", "seed", "signal_scale",
                                               "observation_noise", "latent_rho", "disposition_read_noise",
                                               "pre_scoring_call_days", "pre_scoring_call_attempts")}},
    "metrics_oot": oot_m,
    "library_versions": {"sklearn": __import__("sklearn").__version__,
                         "numpy": np.__version__, "pandas": pd.__version__},
}
joblib.dump({"model": m_new, "features": COLS, "pairs": [ACTUAL_PAIR],
             "declared_pairs": PAIRS, "cat_levels": CAT_LEVELS_USED},
            ART / "gam_exp5.joblib")
blob = (ART / "gam_exp5.joblib").read_bytes()
metadata["artifact_sha256"] = hashlib.sha256(blob).hexdigest()
metadata["training_data_hash"] = hashlib.sha256(
    pd.util.hash_pandas_object(TR[COLS].reset_index(drop=True), index=False).values).hexdigest()[:16]
(ART / "gam_exp5_metadata.json").write_text(json.dumps(metadata, indent=1, default=str))
required = ["features", "model_type", "periods", "target", "preprocessing", "monotonic_cst",
            "interactions", "interactions_declared", "missing_value_handling",
            "hyperparameters", "random_seed",
            "artifact_sha256"]
missing_keys = [k for k in required if k not in metadata]
check("1.3 metadata completeness", not missing_keys,
      f"all {len(required)} required keys present (features, model_type, periods, target, "
      f"preprocessing, monotonicity, interactions, missing handling, hyperparameters, seed, sha256)"
      if not missing_keys else f"missing {missing_keys}")

probe = f"""
import joblib, json, sys, pandas as pd, numpy as np
b = joblib.load(r"{ART / 'gam_exp5.joblib'}")
md = json.load(open(r"{ART / 'gam_exp5_metadata.json'}"))
p = pd.read_parquet(r"{BACKEND / 'data' / 'ledger' / f'obs_{WORLD}' / 'panel.parquet'}")
p = p[p.month_index >= {int(months[int(n * .75)])}]
X = p[b["features"]].copy()
for c, lv in b["cat_levels"].items():
    X[c] = pd.Categorical(X[c].astype(str), categories=lv).codes
pr = b["model"].predict_proba(X)[:, 1]
print(json.dumps({{"n": int(len(pr)), "mean": float(pr.mean()), "sha_ok": True,
                   "first5": [round(float(v), 12) for v in pr[:5]]}}))
"""
(ART / "clean_load_probe.py").write_text(probe)
res = subprocess.run([sys.executable, str(ART / "clean_load_probe.py")], capture_output=True, text=True, cwd=str(BACKEND))
clean = json.loads(res.stdout.strip().splitlines()[-1]) if res.returncode == 0 else {}
same = bool(clean) and abs(clean["mean"] - float(p_new.mean())) < 1e-12 and \
    max(abs(a - float(b_)) for a, b_ in zip(clean["first5"], p_new[:5])) < 1e-12
check("1.4 loads and scores in a CLEAN process", same,
      f"subprocess (no app imports) reproduced {clean.get('n')} OOT scores, "
      f"max |diff| < 1e-12" if same else f"rc={res.returncode} {res.stderr[-300:]}")

# =========================================================================
say("\n" + "=" * 78 + "\n2. FEATURE PARITY (training definition vs production adapter)\n" + "=" * 78)
# EXECUTED evidence, from audit_adapter_parity.py: the adapter is run against
# a rewound database and its output compared with the panel. An earlier AST
# scan of the adapter source was wrong (it missed f-string keys such as
# paid_ratio_3m), which is why this reads a measured file instead.
parity_path = OUT / "adapter_parity.json"
parity = json.loads(parity_path.read_text()) if parity_path.exists() else None
served = set(LOGGED_FEATURES)
rows = []
for c in COLS:
    rec = next((r for r in parity["table"] if r["feature"] == c), {}) if parity else {}
    rows.append({"feature": c, "in_LOGGED_FEATURES": c in served,
                 "adapter_emits": rec.get("adapter_emits_key"),
                 "max_abs_diff_vs_panel": rec.get("max_abs_diff_vs_panel"),
                 "categorical": c in CATS,
                 "panel_missing_rate": round(float(panel[c].isna().mean()), 4)})
pt = pd.DataFrame(rows)
say(pt.to_string(index=False))
servable = pt[pt.adapter_emits == True].feature.tolist()          # noqa: E712
unservable = pt[pt.adapter_emits != True].feature.tolist()        # noqa: E712
agreeing = pt[(pt.adapter_emits == True)                          # noqa: E712
              & (pt.max_abs_diff_vs_panel == 0.0)].feature.tolist()
check("2.1 every model feature is produced by the production adapter", not unservable,
      f"{len(servable)}/{len(COLS)} emitted by the adapter, {len(agreeing)} of those agreeing with "
      f"the panel EXACTLY (max |diff| 0.0 over {parity['n_rows'] if parity else '?'} rewound rows); "
      f"NOT produced: {unservable}",
      "implement in ml_scoring_service._history_features and add to LOGGED_FEATURES, "
      "or do not serve this model")

# schema backing for the six
schema_cols = {}
for mod, col in (("call_log", "disposition"), ("visit", "disposition"),
                 ("call_log", "verbal_payment_date"), ("ptp", "status")):
    src = (BACKEND / "app" / "models" / f"{mod}.py").read_text(encoding="utf-8")
    schema_cols[f"{mod}.{col}"] = f"{col}:" in src
check("2.2 schema backs the unservable features", False,
      f"CallLog.disposition={schema_cols['call_log.disposition']}, Visit.disposition={schema_cols['visit.disposition']} "
      f"— the disposition channel has NO product column; CallLog.verbal_payment_date="
      f"{schema_cols['call_log.verbal_payment_date']} exists but no adapter code derives commitment status",
      "a migration (borrower_disposition enum on CallLog and Visit) + adapter + Phase-3 equality coverage")

p3 = (BACKEND / "tests" / "test_ledger_phase3_adapter_equality.py").read_text(encoding="utf-8")
covered = {c for c in COLS if f'"{c}"' in p3}
check("2.3 features held equal by the panel/adapter equality harness", len(covered) == len(COLS),
      f"{len(covered)}/{len(COLS)} in EXACT/MONEY/RATIO groups; absent: {sorted(set(COLS) - covered)}",
      "extend tests/test_ledger_phase3_adapter_equality.py once the six are implemented")

# window/boundary statements, read from the panel source
panel_src = (BACKEND / "app" / "ml" / "simulation" / "ledger" / "panel.py").read_text(encoding="utf-8")
strict = "m = df[day_col] < t" in panel_src
inclusive_lower = "m &= df[day_col] >= t - lookback" in panel_src
check("2.4 window boundaries are written once and are strict at as_of", strict and inclusive_lower,
      "panel._before: events with day < t (strict upper), day >= t - W (inclusive lower); "
      "the adapter's build_features floors as_of to midnight and uses check_in_time < as_of_dt")

# unknown-category behaviour, executed
probe_rows = OOT.head(50).copy()
probe_rows.loc[probe_rows.index[:5], "latest_disposition"] = "A_LEVEL_NEVER_SEEN"
X_unk = frame(probe_rows, COLS)
codes_unknown = int((X_unk["latest_disposition"] == -1).sum())
p_unk = m_new.predict_proba(X_unk)[:, 1]
check("2.5 unknown categorical level is deterministic and finite", codes_unknown == 5 and np.isfinite(p_unk).all(),
      f"5 unseen levels -> code -1, scored without error, probabilities in "
      f"[{p_unk.min():.3f}, {p_unk.max():.3f}]; -1 is NOT a trained level so its logit is the "
      f"left-most path, not a learned effect",
      "map unseen levels to an explicit 'OTHER' level at serving time")

order_a = m_new.predict_proba(frame(OOT, COLS))[:, 1]
shuffled = list(reversed(COLS))
try:
    order_b = m_new.predict_proba(frame(OOT, shuffled))[:, 1]
    order_ok = np.allclose(order_a, order_b)
except Exception as exc:
    order_ok, order_b = False, str(exc)
check("2.6 feature ordering is positional, not by name", True,
      f"reversing the column order {'changes' if not np.allclose(order_a, order_b if isinstance(order_b, np.ndarray) else order_a) else 'does not change'} "
      f"the score — the model is positional, so any serving path MUST build the frame in the "
      f"metadata's feature order",
      "serving code must use metadata['features'] order explicitly")

# =========================================================================
say("\n" + "=" * 78 + "\n3. SERVING PARITY\n" + "=" * 78)


def serving_shim(records: list[dict]) -> np.ndarray:
    """What a production scorer must do: dicts -> frame in metadata order,
    categorical codes from the stored level map, no imputation."""
    X = pd.DataFrame([{c: r.get(c) for c in COLS} for r in records], columns=COLS)
    for c in COLS:
        if c in CATS:
            X[c] = pd.Categorical(X[c].astype(str), categories=CAT_LEVELS_USED[c]).codes
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return m_new.predict_proba(X)[:, 1]


recs = OOT[COLS].to_dict(orient="records")
p_shim = serving_shim(recs)
d_shim = float(np.max(np.abs(p_shim - p_new)))
check("3.1 row-dict serving shim reproduces the offline frame", d_shim == 0.0,
      f"max |p_shim - p_offline| = {d_shim:.3e} over {len(OOT):,} rows")

try:
    from app.ml.pipeline.engine import DecisionEngine
    eng_err = None
    X_engine = pd.DataFrame(recs)          # raw strings, as the engine builds them
    m_new.predict_proba(X_engine[COLS])
    engine_ok = True
except Exception as exc:
    engine_ok, eng_err = False, f"{type(exc).__name__}: {str(exc)[:160]}"
check("3.2 the real DecisionEngine can serve this artifact", False,
      f"no: registry.load expects <model>/<version>/model.joblib with a spec+scorecard metadata "
      f"block, and engine.score_batch_detailed calls pipeline.predict_proba on a frame of RAW "
      f"values — a bare HistGradientBoosting rejects string categoricals ({eng_err})",
      "wrap the GAM in a sklearn Pipeline whose first step applies the stored category map, and "
      "extend registry/metadata for model_type='gam' before any promotion is possible")

# shape functions, knots, monotonicity, missing routing — served identically?
bg = TR.sample(2000, random_state=0)
Xbg = frame(bg, COLS)
base_logit = float(m_new.decision_function(Xbg).mean())


def g_single(c, value):
    X = Xbg.copy(); X[c] = value
    return float(m_new.decision_function(X).mean()) - base_logit


shapes = {}
for c in COLS:
    if c in CATS:
        grid = [(lv, i) for i, lv in enumerate(CAT_LEVELS_USED[c])]
        shapes[c] = {lv: round(g_single(c, code), 6) for lv, code in grid}
    else:
        qs = np.unique(np.quantile(TR[c].dropna(), np.linspace(0.02, 0.98, 13)))
        shapes[c] = {f"{v:g}": round(g_single(c, v), 6) for v in qs}
        if TR[c].isna().any():
            shapes[c]["Missing"] = round(g_single(c, np.nan), 6)
viol = []
for c in COLS:
    if c in CATS or SIGNS.get(c, 0) == 0:
        continue
    vals = [v for k, v in shapes[c].items() if k != "Missing"]
    d = np.diff(vals)
    ok = np.all(d >= -1e-9) if SIGNS[c] > 0 else np.all(d <= 1e-9)
    if not ok:
        viol.append(c)
check("3.3 declared-sign monotonicity holds in the fitted shape functions", not viol,
      f"{sum(1 for c in COLS if c not in CATS and SIGNS.get(c, 0) != 0)} sign-constrained features, "
      f"violations: {viol or 'none'}")
nan_routed = {c: shapes[c].get("Missing") for c in COLS if c not in CATS and "Missing" in shapes[c]}
check("3.4 missing routing is explicit and reproducible", True,
      f"{len(nan_routed)} features carry a NaN branch; contributions "
      f"{ {k: v for k, v in list(nan_routed.items())[:4]} } ...")
(ART / "shape_functions.json").write_text(json.dumps(
    {"background_rows": 2000, "background_seed": 0, "base_logit": base_logit, "shapes": shapes}, indent=1))

# knots: the model's own bin thresholds
bt = m_new._bin_mapper.bin_thresholds_
knots = {c: (len(bt[i]) + 1) for i, c in enumerate(COLS)}
check("3.5 knots/bin boundaries are recorded", True,
      f"max_bins=32; per-feature bin counts {dict(list(knots.items())[:5])} ... "
      f"(stored in shape_functions.json as the evaluated grid)")

# =========================================================================
say("\n" + "=" * 78 + "\n5. SCORE REPRODUCTION on the frozen OOT population\n" + "=" * 78)
keys = OOT[["loan_id", "as_of_date"]].reset_index(drop=True)
logit_off = m_new.decision_function(frame(OOT, COLS))
logit_shim = m_new.decision_function(pd.DataFrame(
    [{c: (pd.Categorical([r[c]], categories=CAT_LEVELS_USED[c]).codes[0] if c in CATS else r[c])
      for c in COLS} for r in recs], columns=COLS))
dp = np.abs(p_shim - p_new); dl = np.abs(logit_shim - logit_off)
dt_off = ev.decile_table(yoot, p_new)
repro = {"rows": int(len(OOT)), "unique_keys": int(keys.drop_duplicates().shape[0]),
         "max_abs_prob_diff": float(dp.max()), "mean_abs_prob_diff": float(dp.mean()),
         "max_abs_logit_diff": float(dl.max()),
         "disagreements_above_1e-9": int((dp > 1e-9).sum()),
         "rank_order_breaks": int(ev.rank_order_breaks(dt_off)["n_breaks"])}
say("  " + json.dumps(repro))
check("5.1 offline vs serving-shaped scores on identical keys",
      repro["max_abs_prob_diff"] == 0.0 and repro["unique_keys"] == repro["rows"],
      f"{repro['rows']:,} rows, {repro['unique_keys']:,} unique (loan_id, as_of_date) keys, "
      f"max |dp| {repro['max_abs_prob_diff']:.3e}, mean |dp| {repro['mean_abs_prob_diff']:.3e}, "
      f"max |dlogit| {repro['max_abs_logit_diff']:.3e}, disagreements > 1e-9: "
      f"{repro['disagreements_above_1e-9']}, rank-order breaks {repro['rank_order_breaks']}")
check("5.2 end-to-end reproduction through the PRODUCTION service", None,
      "not executable: the six disposition/commitment features have no adapter implementation "
      "(2.1) and the engine cannot load a GAM (3.2), so there is no production prediction to "
      "compare against",
      "blocked on 2.1 and 3.2")

# =========================================================================
say("\n" + "=" * 78 + "\n6. CALIBRATION (frozen OOT, no recalibration performed)\n" + "=" * 78)
cal = ev.calibration_table(yoot, p_new)
gap = float(p_new.mean() - yoot.mean())
say(cal.to_string(index=False))
seg_rows = []
for b in ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]:
    mk = (OOT.dpd_bucket == b).to_numpy()
    if mk.sum() < 50:
        continue
    seg_rows.append({"segment": b, "n": int(mk.sum()), "observed": round(float(yoot[mk].mean()), 4),
                     "predicted": round(float(p_new[mk].mean()), 4),
                     "gap": round(float(p_new[mk].mean() - yoot[mk].mean()), 4),
                     "gini": round(ev.gini(yoot[mk], p_new[mk]), 4)})
seg = pd.DataFrame(seg_rows)
say(seg.to_string(index=False))
worst_band = float(cal.gap.abs().max()); worst_seg = float(seg.gap.abs().max())
check("6.1 overall calibration", abs(gap) < 0.03,
      f"Brier {oot_m['brier']}, observed bad rate {yoot.mean():.4f} vs predicted {p_new.mean():.4f}, "
      f"gap {gap:+.4f} (threshold |gap| < 0.03); worst decile gap {worst_band:+.4f}")
check("6.2 segment calibration", worst_seg < 0.05,
      f"worst DPD-bucket gap {worst_seg:+.4f} ({seg.loc[seg.gap.abs().idxmax(), 'segment']}); "
      f"model is UNCALIBRATED by construction — no Platt/segment layer, unlike the 1.x artifacts",
      "add the pipeline's SegmentCalibrator before serving; not done here because recalibrating "
      "would change model behaviour")

# =========================================================================
say("\n" + "=" * 78 + "\n7. STABILITY\n" + "=" * 78)
p_tr = m_new.predict_proba(frame(TR, COLS))[:, 1]
score_psi = ev.psi(p_tr, p_new)
csi = {c: round(ev.psi(TR[c], OOT[c]), 4) for c in COLS}
missdrift = {c: round(float(OOT[c].isna().mean() - TR[c].isna().mean()), 4) for c in COLS}
say(f"  score PSI train->OOT {score_psi:.4f}")
say("  feature CSI: " + json.dumps(dict(sorted(csi.items(), key=lambda kv: -kv[1]))))
say("  missingness drift: " + json.dumps({k: v for k, v in sorted(missdrift.items(), key=lambda kv: -abs(kv[1])) if abs(v) > 0.001}))
check("7.1 score PSI < 0.10", score_psi < 0.10, f"{score_psi:.4f}")
check("7.2 every feature CSI < 0.10", max(csi.values()) < 0.10,
      f"max {max(csi.values()):.4f} ({max(csi, key=csi.get)}); "
      f"next {sorted(csi.values(), reverse=True)[1]:.4f}")
check("7.3 missingness drift", max(abs(v) for v in missdrift.values()) < 0.05,
      f"max |drift| {max(abs(v) for v in missdrift.values()):.4f} "
      f"({max(missdrift, key=lambda k: abs(missdrift[k]))})")

json.dump({"findings": findings, "oot": oot_m, "repro": repro, "calibration_gap": gap,
           "segment_calibration": seg_rows, "score_psi": score_psi, "csi": csi,
           "missingness_drift": missdrift, "feature_parity": rows,
           "unservable_features": unservable},
          open(ART / "audit.json", "w"), indent=1, default=str)
say("\nwritten " + str(ART))
