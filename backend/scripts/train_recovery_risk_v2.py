"""recovery_risk 2.x candidates, trained on the ledger panel.

    python -m scripts.build_ledger_dataset --tier full      # the world, first
    python -m scripts.train_recovery_risk_v2 --tier full    # 2.0.0
    python -m scripts.train_recovery_risk_v2 --tier full --version 2.1.0 \
        --also-compare 2.0.0                                # the fresh candidate

2026-09-15 (later) — generalised over `config.CANDIDATE_SPECS` for the 2.1.0
fresh development. Same steps, same gates, same comparison module; the only
additions are `--also-compare` (score further versions on the IDENTICAL
out-of-time rows, so 1.1.0, 2.0.0 and the candidate are read side by side)
and a stability block (Gini/KS per out-of-time month, per DPD bucket, and
per score decile) that every version in the comparison gets.

WHAT THIS DOES, IN ORDER, AND WHAT IT REFUSES TO DO.

  1. Trains `ModelSpec.RECOVERY_RISK_V2` (2.0.0) on the ledger panel with the
     SAME target, split, gates, PDO scaling and forbidden list as 1.1.0 — only
     the candidate set is wider and the development panel is the ledger. The
     artifact is written with `make_champion=False`; `champion.txt` is not
     touched by anything in this file.
  2. Trains the 1.x spec on the SAME panel as a reference, so the uplift can be
     attributed to the FEATURES rather than to the world having changed. It is
     written under a scratch version and never promotable.
  3. Runs the Phase 2 model-facing checks 7-10 (imported from
     scripts/phase2_ledger_validation, not restated) against 2.0.0.
  4. Scores the DEPLOYED 1.1.0 and the new 2.0.0 on the IDENTICAL out-of-time
     rows through `DecisionEngine.score_batch_detailed` — the production path,
     calibrator and coverage floor included — and hands both vectors to
     `ml/pipeline/comparison.compare_to_incumbent`, the same gate the automated
     retrain uses.
  5. Prints and records a NO-DEGRADE table: every metric the 1.1.0 artifact
     carries beside the 2.0.0 figure, with the direction that counts as worse.
     The verdict is a FAIL if any of them moved the wrong way beyond its own
     stated tolerance. Nothing in it is a warning.

THE ONE THING IT MUST NOT DO is move a gate. Every threshold here is either the
spec's own (imported) or the comparison module's own (imported). If 2.0.0 does
not clear them, the answer is that it does not clear them.

Output: app/ml/artifacts/recovery_risk/2.0.0/ (committed, like every artifact)
plus V2_REPORT.json inside it, and data/ledger/<tier>/V2_REPORT.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ml.pipeline import registry                                   # noqa: E402
from app.ml.pipeline.comparison import compare_to_incumbent            # noqa: E402
from app.ml.pipeline import evaluate as ev                             # noqa: E402
from app.ml.pipeline.config import (                                   # noqa: E402
    CANDIDATE_SPECS, RECOVERY_RISK, RECOVERY_RISK_V2,
)
from app.ml.pipeline.engine import DecisionEngine                      # noqa: E402
from app.ml.pipeline.report import write_model_document                # noqa: E402
from app.ml.pipeline.train import ModelTrainer                         # noqa: E402
from scripts.phase2_ledger_validation import (                         # noqa: E402
    DPD_DOMINANCE_MAX, ORACLE_CAPTURE_MAX, ORACLE_GAP_MIN_ABS, _gini_from,
    _split, information_ceiling, leakage_probes,
)

DATA_ROOT = BACKEND / "data" / "ledger"
INCUMBENT = "1.1.0"
#: Where each candidate's report goes, beside its artifact and the dataset.
REPORT_NAMES = {"2.0.0": "V2_REPORT.json", "2.1.0": "V21_REPORT.json"}
#: The 1.x spec re-fitted on the v2 world. A scratch version: never promotable
#: (make_champion=False, and its name says what it is), kept on disk so the
#: same-world attribution can be re-read rather than re-run.
REFERENCE_VERSION = "2.0.0-ref-v1spec"


# ---------------------------------------------------------------------------
# The no-degrade table
# ---------------------------------------------------------------------------
# TWO KINDS OF ROW, AND THE DISTINCTION IS THE WHOLE POINT.
#
# The first run of this script compared 2.0.0 against the numbers in the 1.1.0
# ARTIFACT — Gini 0.5136, KS 39.72 — and printed FAIL. Those numbers describe
# 1.1.0 on the book_simulator world; 2.0.0 is developed on the ledger world,
# where 1.1.0 itself scores Gini 0.4046. Comparing a model on one world against
# a model on another is not a comparison, and a table that does it would have
# either blocked a better model or excused a worse one.
#
#   SAME-ROWS rows: both models scored on the IDENTICAL out-of-time frame
#     through the production path. Worse = the comparison module's own gate
#     failed (Gini beyond sampling error, Brier +5%, KS -10%, calibration
#     +0.05, top-5 breaks). This is the only apples-to-apples read there is.
#   MODEL-PROPERTY rows: things measured on each artifact's own development
#     data. The incumbent's value is shown for context; the verdict is the
#     SPEC GATE, because a wider scorecard legitimately carries a weaker
#     ninth feature than a four-feature one carries its fourth.

def no_degrade_table(inc_art: dict, v2_art: dict, cmp, spec) -> list[dict]:
    g = spec.gates
    out = []
    # ── same rows, production path ──────────────────────────────────────────
    same = {
        "gini": ("higher_better", "gini_materially_better"),
        "ks": ("higher_better", "ks_not_collapsed"),
        "brier": ("lower_better", "brier_not_worse"),
        "calibration_gap": ("abs_lower_better", "calibration_not_worse"),
        "rank_order_breaks_top5": ("lower_better", "rank_order_top5_not_worse"),
        "rank_order_breaks": ("lower_better", None),
    }
    gates = {x["gate"]: x["result"] for x in cmp.gates}
    for k, (direction, gate) in same.items():
        a, b = cmp.incumbent[k], cmp.challenger[k]
        if direction == "abs_lower_better":
            a, b = abs(a), abs(b)
        better = (b > a) if direction == "higher_better" else (b < a)
        worse = (b < a) if direction == "higher_better" else (b > a)
        if gate is not None:
            result = ("FAIL" if gates.get(gate) == "FAIL"
                      else ("IMPROVED" if better else "HELD"))
        else:
            result = "FAIL" if worse else ("IMPROVED" if better else "HELD")
        out.append({"scope": "same_rows", "metric": f"oot_{k}", "incumbent": a,
                    "v2": b, "direction": direction, "result": result,
                    "basis": f"comparison gate {gate}" if gate else "strict"})

    # ── model properties, each against the spec gate ────────────────────────
    props = [
        ("max_kept_vif", "lower_better", g.vif_max, "< vif_max"),
        ("max_candidate_iv", "lower_better", g.iv_max, "< iv_max"),
        ("min_selected_iv", "higher_better", g.iv_min, ">= iv_min"),
        ("score_psi_train_vs_oot", "lower_better", g.psi_warn, "< psi_warn"),
        ("max_selected_feature_psi", "lower_better", g.psi_warn, "< psi_warn"),
        ("train_minus_oot_gini_gap", "lower_better", g.max_train_test_gini_gap,
         "< max_train_test_gini_gap"),
        ("n_selected_features", "higher_better", None, "informational"),
    ]
    for key, direction, threshold, basis in props:
        a, b = inc_art.get(key), v2_art.get(key)
        if b is None:
            out.append({"scope": "model_property", "metric": key, "incumbent": a,
                        "v2": b, "direction": direction, "result": "N/A",
                        "basis": basis})
            continue
        if threshold is None:
            ok = True
        elif direction == "higher_better":
            ok = b >= threshold
        else:
            ok = b < threshold
        better = a is not None and ((b > a) if direction == "higher_better" else (b < a))
        out.append({"scope": "model_property", "metric": key, "incumbent": a, "v2": b,
                    "direction": direction, "threshold": threshold,
                    "result": "FAIL" if not ok else ("IMPROVED" if better else "HELD"),
                    "basis": basis})
    return out


def _summary(meta: dict, artifact_dir: Path) -> dict:
    """The comparable numbers out of one artifact's metadata and tables."""
    m = meta["metrics"]
    oot, tr = m["oot"], m["train"]
    vif = pd.read_csv(artifact_dir / "evaluation" / "vif.csv")
    iv = pd.read_csv(artifact_dir / "eda" / "information_value.csv")
    psi = pd.read_csv(artifact_dir / "evaluation" / "psi.csv")
    selected = meta["selected_features"]
    sel_iv = iv[iv.feature.isin(selected)].iv
    sel_psi = psi[psi.feature.isin(selected)].psi.dropna()
    return {
        "version": meta.get("version"),
        "selected_features": selected,
        "n_selected_features": len(selected),
        "n_candidate_features": meta.get("n_candidate_features"),
        "oot_gini": oot["gini"], "oot_ks": oot["ks"], "oot_auc": oot["auc"],
        "oot_brier": oot["brier"],
        "oot_abs_calibration_gap": round(abs(oot["calibration_gap"]), 4),
        "oot_rank_order_breaks": int(oot["rank_order"]["n_breaks"]),
        "oot_rank_order_breaks_top5": int(oot["rank_order"]["n_breaks_top5"]),
        "oot_top_decile_lift": oot.get("top_decile_lift"),
        "train_gini": tr["gini"],
        "train_minus_oot_gini_gap": round(tr["gini"] - oot["gini"], 4),
        "score_psi_train_vs_oot": meta.get("score_psi_train_vs_oot"),
        "max_selected_feature_psi": (round(float(sel_psi.max()), 4)
                                     if len(sel_psi) else None),
        "max_kept_vif": round(float(vif[vif.action == "kept"].vif.max()), 3),
        "max_candidate_iv": round(float(iv.iv.max()), 4),
        "min_selected_iv": round(float(sel_iv.min()), 4) if len(sel_iv) else None,
        "bootstrap_gini_oot": meta.get("bootstrap_gini_oot"),
        "gate_summary": meta.get("gate_summary"),
        "oot_n": oot["n"], "oot_bad_rate": round(oot["bad_rate"], 4),
    }


def _engine_scores(version: str, frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    eng = DecisionEngine.get("recovery_risk", version)
    if eng is None:
        raise SystemExit(f"could not load recovery_risk {version}")
    rows = frame[[f for f in features if f in frame.columns]].to_dict(orient="records")
    out = eng.score_batch_detailed(rows)
    return np.array([r.probability if r.probability is not None else np.nan
                     for r in out], dtype=float)


def _stability(y: np.ndarray, scores: dict[str, np.ndarray],
               oot_df: pd.DataFrame) -> dict:
    """Gini/KS per out-of-time month and per DPD bucket for every version on
    the same rows, plus each version's decile table. Temporal stability is
    the spread of the monthly Gini; the bucket table is where DPD dominance
    would show, because within a bucket DPD has little left to say."""
    out: dict = {"by_month": [], "by_bucket": [], "deciles": {}}
    months = oot_df["month_index"].to_numpy()
    buckets = oot_df["dpd_bucket"].to_numpy()
    for m in sorted(set(months)):
        mk = months == m
        row = {"month_index": int(m), "n": int(mk.sum()),
               "bad_rate": round(float(y[mk].mean()), 4)}
        for v, p in scores.items():
            ks, _ = ev.ks_statistic(y[mk], p[mk])
            row[f"gini_{v}"] = round(ev.gini(y[mk], p[mk]), 4)
            row[f"ks_{v}"] = round(ks, 2)
        out["by_month"].append(row)
    for b in ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]:
        mk = buckets == b
        if mk.sum() < 50 or len(set(y[mk])) < 2:
            continue
        row = {"bucket": b, "n": int(mk.sum()),
               "bad_rate": round(float(y[mk].mean()), 4)}
        for v, p in scores.items():
            ks, _ = ev.ks_statistic(y[mk], p[mk])
            row[f"gini_{v}"] = round(ev.gini(y[mk], p[mk]), 4)
            row[f"ks_{v}"] = round(ks, 2)
        out["by_bucket"].append(row)
    for v, p in scores.items():
        out["deciles"][v] = ev.decile_table(y, p).to_dict(orient="records")
    for v in scores:
        g = [r[f"gini_{v}"] for r in out["by_month"]]
        out[f"monthly_gini_sd_{v}"] = round(float(np.std(g)), 4)
        out[f"monthly_gini_min_{v}"] = round(float(np.min(g)), 4)
        out[f"monthly_gini_max_{v}"] = round(float(np.max(g)), 4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="full")
    ap.add_argument("--version", default="2.0.0",
                    help="which CANDIDATE_SPECS entry to train")
    ap.add_argument("--also-compare", nargs="*", default=[],
                    help="further artifact versions to score on the identical "
                         "OOT rows (e.g. 2.0.0 when training 2.1.0)")
    ap.add_argument("--skip-reference", action="store_true",
                    help="do not re-fit the 1.x spec on this panel")
    a = ap.parse_args()

    d = DATA_ROOT / a.tier
    panel = pd.read_parquet(d / "panel.parquet")
    meta = json.loads((d / "dataset_metadata.json").read_text())
    if not meta.get("realism", {}).get("passed"):
        raise SystemExit("the ledger dataset failed its own realism checks; "
                         "rebuild it before training on it")

    spec = CANDIDATE_SPECS[("recovery_risk", a.version)]
    assert spec.version == a.version and spec.name == "recovery_risk"
    report_name = REPORT_NAMES[spec.version]
    assert spec.gates == RECOVERY_RISK.gates, "a gate moved"
    assert spec.target == RECOVERY_RISK.target
    missing = [f for f in spec.all_features if f not in panel.columns]
    if missing:
        raise SystemExit(f"panel lacks v{spec.version} candidates: {missing}")

    print(f"\n{'='*78}\n  recovery_risk v{spec.version} on the ledger panel ({a.tier})"
          f"\n{'='*78}")
    print(f"  rows {len(panel):,}   bad rate {panel[spec.target].mean():.4f}   "
          f"months {panel.month_index.nunique()}   candidates "
          f"{len(spec.all_features)} (1.x had {len(RECOVERY_RISK.all_features)})")

    # ── 1. the model ────────────────────────────────────────────────────────
    res = ModelTrainer(spec, panel, dataset_meta=meta).run(make_champion=False)
    m = res.metrics
    print(f"\n  selected {len(res.selected)}: {', '.join(res.selected)}")
    for label in ("train", "valid", "oot"):
        e = m[label]
        print(f"  {label:<6} n {e['n']:>7,}  bad {e['bad_rate']:.4f}  "
              f"Gini {e['gini']:.4f}  AUC {e['auc']:.4f}  KS {e['ks']:5.2f}  "
              f"Brier {e['brier']:.5f}  breaks {e['rank_order']['n_breaks']}")
    print(f"  challenger GBM oot Gini {m['oot_challenger']['gini']:.4f} "
          f"(champion_kind = {res.champion_kind})")
    print(f"\n{res.gates.to_string(index=False)}")
    print(f"  SPEC GATES: {'PASS' if res.passed else 'FAIL'}")
    v2_meta = json.loads((res.artifact_dir / "metadata.json").read_text())
    v2 = _summary(v2_meta, res.artifact_dir)

    # ── 2. the 1.x spec on the same world ───────────────────────────────────
    ref = None
    if not a.skip_reference:
        ref_spec = replace(RECOVERY_RISK, version=REFERENCE_VERSION,
                           training_panel="ledger")
        assert ref_spec.numeric_features == RECOVERY_RISK.numeric_features
        rres = ModelTrainer(ref_spec, panel, dataset_meta=meta).run(make_champion=False)
        ref = _summary(json.loads((rres.artifact_dir / "metadata.json").read_text()),
                       rres.artifact_dir)
        print(f"\n  reference (1.x spec, same panel) {REFERENCE_VERSION}: "
              f"selected {ref['n_selected_features']} "
              f"[{', '.join(ref['selected_features'])}]  oot Gini "
              f"{ref['oot_gini']:.4f}  KS {ref['oot_ks']:.2f}")

    # ── 3. checks 7-10 ──────────────────────────────────────────────────────
    oot_gini = m["oot"]["gini"]
    tr, oot_df = _split(panel, spec)
    dpd_only = _gini_from(["dpd"], tr, oot_df, spec.target)
    iv = pd.read_csv(res.artifact_dir / "eda" / "information_value.csv")
    max_iv = float(iv.iv.max())
    org = information_ceiling(panel, d, spec)
    leaks = leakage_probes(panel, spec)
    gap = org.get("oracle_gap_absolute")
    frac = org.get("fraction_of_oracle_captured")
    ratio = None if not dpd_only or not oot_gini else round(dpd_only / oot_gini, 4)
    checks = [
        ("7   OOT Gini in band", oot_gini,
         f"[{spec.gates.gini_target_low}, {spec.gates.gini_target_high}]",
         spec.gates.gini_target_low <= oot_gini <= spec.gates.gini_target_high,
         "GATED"),
        ("8   Gini(dpd)/Gini(full)", ratio, f"<= {DPD_DOMINANCE_MAX}", None,
         "DIAGNOSTIC"),
        ("9   max feature IV", max_iv, f"< {spec.gates.iv_max}",
         max_iv < spec.gates.iv_max, "GATED"),
        ("10a oracle gap, absolute", gap, f">= {ORACLE_GAP_MIN_ABS}",
         gap is not None and gap >= ORACLE_GAP_MIN_ABS, "GATED"),
        ("10b fraction captured", frac, f"<= {ORACLE_CAPTURE_MAX}",
         frac is not None and frac <= ORACLE_CAPTURE_MAX, "GATED"),
    ]
    print(f"\n{'='*78}\n  CHECKS 7-10 (Phase 2 definitions, unchanged)\n{'='*78}")
    for name, val, band, ok, kind in checks:
        v = "n/a" if val is None else f"{val:.4f}"
        status = kind if ok is None else ("PASS" if ok else "FAIL")
        print(f"  {name:<32} {v:>8}  {band:<16} {status}")
    print(f"  oracle {org.get('gini_oracle')}  observable {org.get('gini_observable')}"
          f"  hidden-only {org.get('gini_hidden_only')}")
    print(f"\n  LEAKAGE  shuffled {leaks['gini_on_shuffled_labels']}   honest "
          f"{leaks['gini_honest_reference']}   future-features "
          f"{leaks['gini_with_future_features']}   uplift {leaks['future_uplift']}")

    # ── 4. against the DEPLOYED model, on identical rows ───────────────────
    DecisionEngine.clear_cache()
    feats = list(spec.all_features)
    y = oot_df[spec.target].astype(int).to_numpy()
    ch = _engine_scores(spec.version, oot_df, feats)
    inc = _engine_scores(INCUMBENT, oot_df, feats)
    n_nan = int(np.isnan(ch).sum() + np.isnan(inc).sum())
    if n_nan:
        raise SystemExit(f"{n_nan} declined scores on the shared frame; the two "
                         f"sides do not describe the same rows")
    cmp = compare_to_incumbent(
        "recovery_risk", challenger_version=spec.version, challenger_scores=ch,
        incumbent_version=INCUMBENT, incumbent_scores=inc, y=y,
        segment_series=oot_df["dpd_bucket"].reset_index(drop=True))
    print(f"\n{'='*78}\n  INCUMBENT COMPARISON on {cmp.n_oot:,} identical OOT rows "
          f"(production scoring path)\n{'='*78}")
    print(f"  {'':<22}{'1.1.0 (deployed)':>18}{spec.version:>12}")
    for k in ("gini", "ks", "brier", "calibration_gap", "rank_order_breaks_top5"):
        print(f"  {k:<22}{cmp.incumbent[k]:>18}{cmp.challenger[k]:>12}")
    print(f"  uplift {cmp.gini_uplift:+.4f} against tolerance {cmp.uplift_tolerance:.4f}")
    for g in cmp.gates:
        print(f"  {g['gate']:<28} {str(g['observed']):>10}  {g['threshold']:<14} {g['result']}")
    for srow in cmp.segment:
        print(f"    {srow['segment']:<10} n {srow['n']:>6}  inc {srow['incumbent_gini']:.4f}"
              f"  v2 {srow['challenger_gini']:.4f}  {srow['uplift']:+.4f}")
    print(f"  VERDICT: {cmp.verdict}")

    # ── 4b. further versions on the SAME rows, and stability for all ───────
    scores = {INCUMBENT: inc, spec.version: ch}
    extra_cmps = {}
    for other in a.also_compare:
        po = _engine_scores(other, oot_df, feats)
        if np.isnan(po).any():
            raise SystemExit(f"{int(np.isnan(po).sum())} declined scores from {other}")
        scores[other] = po
        c2 = compare_to_incumbent(
            "recovery_risk", challenger_version=spec.version, challenger_scores=ch,
            incumbent_version=other, incumbent_scores=po, y=y,
            segment_series=oot_df["dpd_bucket"].reset_index(drop=True))
        extra_cmps[other] = c2.to_dict()
        print(f"\n  vs {other} on the same {c2.n_oot:,} rows: "
              f"{other} Gini {c2.incumbent['gini']} KS {c2.incumbent['ks']} | "
              f"{spec.version} Gini {c2.challenger['gini']} KS {c2.challenger['ks']} | "
              f"uplift {c2.gini_uplift:+.4f} tol {c2.uplift_tolerance:.4f} -> {c2.verdict}")
    stab = _stability(y, scores, oot_df.reset_index(drop=True))
    print(f"\n{'='*78}\n  STABILITY on the identical OOT rows\n{'='*78}")
    vs = list(scores)
    print("  " + "month".ljust(10) + "".join(f"{v:>12}" for v in vs) + "   (Gini)")
    for r in stab["by_month"]:
        print("  " + str(r["month_index"]).ljust(10)
              + "".join(f"{r['gini_' + v]:>12.4f}" for v in vs))
    print("  " + "sd".ljust(10) + "".join(f"{stab['monthly_gini_sd_' + v]:>12.4f}" for v in vs))
    print("  " + "bucket".ljust(10) + "".join(f"{v:>12}" for v in vs) + "   (Gini)")
    for r in stab["by_bucket"]:
        print("  " + r["bucket"].ljust(10)
              + "".join(f"{r['gini_' + v]:>12.4f}" for v in vs) + f"   n={r['n']}")

    # ── 5. no-degrade: same rows for performance, spec gates for properties ─
    inc_dir = registry.version_dir("recovery_risk", INCUMBENT)
    inc_meta = json.loads((inc_dir / "metadata.json").read_text())
    inc_sum = _summary(inc_meta, inc_dir)
    table = no_degrade_table(inc_sum, v2, cmp, spec)
    print(f"\n{'='*78}\n  NO-DEGRADE TABLE\n{'='*78}")
    print(f"  {'scope':<16}{'metric':<30}{'1.1.0':>12}{spec.version:>12}   result     basis")
    for r in table:
        print(f"  {r['scope']:<16}{r['metric']:<30}{str(r['incumbent']):>12}"
              f"{str(r['v2']):>12}   {r['result']:<10} {r['basis']}")
    print(f"  (1.1.0 artifact headline on ITS OWN world, book_simulator: Gini "
          f"{inc_sum['oot_gini']} KS {inc_sum['oot_ks']} — not comparable with a "
          f"ledger-world figure; on THIS world it scores Gini {cmp.incumbent['gini']})")
    degraded = [r["metric"] for r in table if r["result"] == "FAIL"]
    print(f"  NO-DEGRADE: {'PASS' if not degraded else 'FAIL — ' + ', '.join(degraded)}")

    gated_checks = [c for c in checks if c[4] == "GATED"]
    overall = (res.passed and all(c[3] for c in gated_checks)
               and cmp.passed and not degraded)
    print(f"\n  OVERALL: {'PASS' if overall else 'FAIL'}   "
          f"(champion.txt untouched: {registry.pointer_version('recovery_risk')})")

    doc = write_model_document(res.artifact_dir)
    report = {
        "version": spec.version, "incumbent": INCUMBENT,
        "tier": a.tier, "dataset_fingerprint": meta.get("config", {}).get("fingerprint"),
        "spec_gates_passed": bool(res.passed),
        "selected": res.selected, "metrics": m,
        "v2_summary": v2, "incumbent_artifact_summary": inc_sum,
        "reference_v1_spec_same_panel": ref,
        "checks_7_to_10": [{"check": c[0], "value": c[1], "band": c[2],
                            "status": c[4] if c[3] is None
                            else ("PASS" if c[3] else "FAIL")} for c in checks],
        "oracle": org, "leakage": leaks,
        "incumbent_comparison": cmp.to_dict(),
        "additional_comparisons": extra_cmps,
        "stability": stab,
        "no_degrade": table, "no_degrade_passed": not degraded,
        "overall_passed": bool(overall),
        "champion_after_run": registry.pointer_version("recovery_risk"),
        "SYNTHETIC_WARNING": meta.get("SYNTHETIC_WARNING"),
        "NOTE": (f"{spec.version} is written with make_champion=False. Promotion is a "
                 "separate, human act through registry.promote / the lifecycle "
                 "API, and nothing in this script performs it."),
    }
    for path in (res.artifact_dir / report_name, d / report_name):
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"  artifact  {res.artifact_dir}\n  document  {doc}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
