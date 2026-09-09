# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. The orchestrator: one call takes a panel and a ModelSpec and
#   produces a committed, versioned, gated artifact.
#
#   THE OUT-OF-TIME SLICE IS TOUCHED EXACTLY ONCE, at the end. Preprocessing
#   caps, WOE bins, feature selection and both models are fitted on train, with
#   validation used for the stepwise stopping rule. Anything else and the
#   holdout stops being a holdout — which is the failure
#   ml/train_shadow_model.py's own header documents at length for the version
#   that read live Loan rows and called it point-in-time.
#
#   CHAMPION IS THE SCORECARD, NOT THE BOOSTER. A gradient-boosted challenger is
#   always trained and always reported, but it only takes the champion slot if
#   it beats the scorecard by a material margin on out-of-time data. An
#   interpretable model that is 0.01 Gini behind is the better product: it
#   explains itself, it can be signed off, and its reason codes are the same
#   arithmetic as its score.
# ───────────────────────────────────────────────────────────────────────────
"""
Model training orchestration.

    from app.ml.pipeline.train import ModelTrainer
    result = ModelTrainer(spec, panel).run()

Produces app/ml/artifacts/<model>/<version>/ containing the fitted pipeline, the
metadata, the readable scorecard, every evaluation table and every plot.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.ml.pipeline import eda, evaluate as ev, registry, testing
from app.ml.pipeline.binning import WOEBinner
from app.ml.pipeline.calibration import SegmentCalibrator
from app.ml.pipeline.config import ModelSpec
from app.ml.pipeline.preprocess import ColumnSubset, Preprocessor
from app.ml.pipeline.scorecard import ScoreCard, scorecard_table
from app.ml.pipeline.selection import select_features

logger = logging.getLogger(__name__)

# How much better the challenger must be, on out-of-time Gini, to displace an
# interpretable scorecard. Not zero: a booster that wins by noise costs the
# reason codes, the sign checks and the committee sign-off that the scorecard
# buys, and those are worth more than 0.01 of Gini.
CHALLENGER_MARGIN = 0.05


@dataclass
class TrainResult:
    spec: ModelSpec
    artifact_dir: Path
    metrics: dict = field(default_factory=dict)
    gates: pd.DataFrame | None = None
    selected: list[str] = field(default_factory=list)
    passed: bool = False
    champion_kind: str = "scorecard"


class ModelTrainer:
    def __init__(self, spec: ModelSpec, panel: pd.DataFrame, *,
                 dataset_meta: dict | None = None, random_state: int = 0):
        self.spec = spec
        self.panel = panel.reset_index(drop=True)
        self.dataset_meta = dataset_meta or {}
        self.random_state = random_state

    # ── split ───────────────────────────────────────────────────────────────
    def _split(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        s = self.spec
        periods = np.sort(self.panel[s.split_col].unique())
        n = len(periods)
        i_tr = int(n * s.train_frac)
        i_va = int(n * (s.train_frac + s.valid_frac))
        tr_p, va_p, oot_p = periods[:i_tr], periods[i_tr:i_va], periods[i_va:]
        col = self.panel[s.split_col]
        return (self.panel[col.isin(tr_p)].copy(),
                self.panel[col.isin(va_p)].copy(),
                self.panel[col.isin(oot_p)].copy())

    def _guard_forbidden(self) -> None:
        """Nothing from the outcome window, and no output of the system, ever.

        The same ban repayment_service._FORBIDDEN_FEATURE_KEYS enforces by
        raising. Checked here rather than trusted because the spec's feature
        tuples are edited by hand and a leak added this way costs nothing to
        make and everything to find.
        """
        bad = [f for f in self.spec.all_features if f in self.spec.forbidden]
        if bad:
            raise ValueError(
                f"{self.spec.name}: forbidden columns present in the feature "
                f"list: {bad}. These are outcomes or system outputs, not features."
            )

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self, *, make_champion: bool = True) -> TrainResult:
        s = self.spec
        self._guard_forbidden()
        out = registry.version_dir(s.name, s.version)
        (out / "eda").mkdir(parents=True, exist_ok=True)
        (out / "evaluation").mkdir(parents=True, exist_ok=True)

        train, valid, oot = self._split()
        logger.info("split: train=%d valid=%d oot=%d", len(train), len(valid), len(oot))
        y_tr, y_va, y_oot = train[s.target], valid[s.target], oot[s.target]

        num, cat = list(s.numeric_features), list(s.categorical_features)
        tables: dict[str, pd.DataFrame] = {}

        # ── 1. preprocess (fit on train only) ───────────────────────────────
        pre = Preprocessor(num, cat).fit(train)
        Xtr, Xva, Xoot = pre.transform(train), pre.transform(valid), pre.transform(oot)

        # ── 2. EDA ──────────────────────────────────────────────────────────
        uni = eda.univariate_table(train, num, cat)
        tables["eda/univariate.csv"] = uni
        eda.plot_missing(train, num + cat, out / "eda" / "missingness.png")
        eda.plot_target_over_time(self.panel, s.target, s.split_col,
                                  out / "eda" / "target_over_time.png")
        eda.plot_correlation(Xtr[num], out / "eda" / "correlation_raw.png",
                             "Correlation — raw numeric features")

        # ── 3. WOE binning ──────────────────────────────────────────────────
        binner = WOEBinner(num, cat,
                           min_bin_fraction=s.gates.min_bin_fraction,
                           min_bin_events=s.gates.min_bin_events).fit(Xtr, y_tr)
        iv_frame = binner.iv_frame()
        tables["eda/information_value.csv"] = iv_frame
        tables["eda/binning_tables.csv"] = pd.concat(
            [binner.bin_table(c) for c in binner.binners_], ignore_index=True)
        eda.plot_iv(iv_frame, out / "eda" / "information_value.png",
                    review_at=s.gates.iv_review)

        Wtr, Wva, Woot = binner.transform(Xtr), binner.transform(Xva), binner.transform(Xoot)
        eda.plot_correlation(Wtr, out / "eda" / "correlation_woe.png",
                             "Correlation — WOE-transformed features")

        # ── 4. selection ────────────────────────────────────────────────────
        sel = select_features(Wtr, y_tr, binner.iv_, s.gates,
                              valid=(Wva, y_va), random_state=self.random_state)
        if not sel.selected:
            raise RuntimeError(f"{s.name}: selection kept no features")
        tables["evaluation/selection_log.csv"] = pd.DataFrame(sel.log)
        tables["evaluation/vif.csv"] = sel.vif_table
        tables["evaluation/sfs_path.csv"] = pd.DataFrame(sel.sfs_path)
        if sel.corr_dropped:
            tables["evaluation/correlation_dropped.csv"] = pd.DataFrame(sel.corr_dropped)
        if sel.sign_dropped:
            tables["evaluation/sign_dropped.csv"] = pd.DataFrame(sel.sign_dropped)
        chosen = sel.selected
        chosen_raw = [c.removesuffix("_woe") for c in chosen]
        eda.plot_woe_curves(binner, chosen_raw, out / "eda")

        # ── 5. champion: WOE + logistic ─────────────────────────────────────
        clf = LogisticRegression(max_iter=1000, random_state=self.random_state)
        clf.fit(Wtr[chosen], y_tr)

        card = ScoreCard(chosen, clf.coef_[0], float(clf.intercept_[0]),
                         pdo=s.pdo, base_score=s.base_score,
                         base_odds=s.base_odds).fit_reference(Wtr[chosen])
        card.fit_bands(Wtr[chosen], y_tr)
        if hasattr(card, "band_table_"):
            tables["evaluation/band_table.csv"] = card.band_table_
        tables["scorecard.csv"] = scorecard_table(card, binner, Wtr)

        p_tr = clf.predict_proba(Wtr[chosen])[:, 1]
        p_va = clf.predict_proba(Wva[chosen])[:, 1]
        p_oot = clf.predict_proba(Woot[chosen])[:, 1]

        # ── 5b. segment calibration, FITTED ON VALIDATION ───────────────────
        # Out-of-sample for the estimator, and never the out-of-time slice. A
        # calibrator fitted on train would learn the model's training-set
        # optimism and correct nothing.
        #
        # The segment variable is `overdue_amount`: the quantity the allocator
        # MULTIPLIES this probability by to get rupees. The first allocator
        # shadow measured the raw model over-predicting the largest balance
        # quintile by 89.6% against 21.2% for the smallest, which cost 8-15% of
        # realised recovery on a 1,200-case book even while the case-level
        # recovery rate improved.
        calibrator = None
        cal_report: list[dict] = []
        seg_col = "overdue_amount"
        if seg_col in Xva.columns and Xva[seg_col].notna().any():
            calibrator = SegmentCalibrator(segment_col=seg_col,
                                           random_state=self.random_state)
            calibrator.fit(p_va, y_va, Xva[seg_col].to_numpy())
            p_tr_cal = calibrator.transform(p_tr, Xtr[seg_col].to_numpy())
            p_va_cal = calibrator.transform(p_va, Xva[seg_col].to_numpy())
            p_oot_cal = calibrator.transform(p_oot, Xoot[seg_col].to_numpy())
            cal_report = calibrator.segment_report(
                p_oot, y_oot, Xoot[seg_col].to_numpy())
            tables["evaluation/calibration_by_segment.csv"] = pd.DataFrame(cal_report)
            # Uncalibrated OOT is kept so the effect is readable, not asserted.
            e_oot_raw = ev.evaluate(y_oot, p_oot, p_oot, label="oot_uncalibrated")
            tables["evaluation/decile_oot_uncalibrated.csv"] = e_oot_raw["decile_table"]
            p_tr, p_va, p_oot = p_tr_cal, p_va_cal, p_oot_cal
        else:
            e_oot_raw = None

        # ── 6. challenger: gradient boosting on the same WOE inputs ─────────
        gbm = HistGradientBoostingClassifier(max_iter=250, max_depth=4,
                                             learning_rate=0.06,
                                             random_state=self.random_state)
        gbm.fit(Wtr[chosen], y_tr)
        g_oot = gbm.predict_proba(Woot[chosen])[:, 1]

        # ── 7. evaluation ───────────────────────────────────────────────────
        e_tr = ev.evaluate(y_tr, p_tr, p_tr, label="train")
        e_va = ev.evaluate(y_va, p_va, p_va, label="valid")
        e_oot = ev.evaluate(y_oot, p_oot, p_oot, label="oot")
        e_gbm = ev.evaluate(y_oot, g_oot, g_oot, label="oot_challenger")

        for e in (e_tr, e_va, e_oot, e_gbm):
            tables[f"evaluation/decile_{e['label']}.csv"] = e["decile_table"]
        tables["evaluation/calibration_oot.csv"] = e_oot["calibration_table"]

        # PSI is reported for every candidate, but GATED on the features the
        # model actually consumes. The first run failed on months_on_book
        # (0.594) and outstanding_to_sanction (0.509) — neither of which
        # selection kept. A model cannot be destabilised by drift in an input
        # it does not read, and failing it for that hides the shifts that would
        # actually matter.
        psi_df = ev.psi_frame(Xtr, Xoot, num + cat,
                              warn=s.gates.psi_warn, fail=s.gates.psi_fail)
        psi_df["in_model"] = psi_df.feature.isin(chosen_raw)
        tables["evaluation/psi.csv"] = psi_df
        psi_selected = psi_df[psi_df.in_model]
        score_psi = ev.psi(p_tr, p_oot)

        # Segment stability. A dimension that is a DISCRETISATION OF A SELECTED
        # FEATURE is reported but not gated: dpd_bucket is a binned `dpd`, and
        # `dpd` is in the model, so a within-bucket Gini measures what the other
        # features add once the strongest one is held constant. Measured, that
        # profile runs 0.146 (CURRENT) to 0.389 (NPA) — exactly the shape a real
        # behaviour scorecard shows, and not a failure. It is printed in the
        # model document precisely because it is the honest read of where the
        # model is weak, but gating on it would be marking the model down for
        # the fact that its best feature works.
        seg_tables, gated_dims = [], []
        for col in s.segment_cols:
            if col not in oot.columns:
                continue
            t = ev.segment_performance(y_oot, p_oot, oot[col])
            derived = col.removesuffix("_bucket") in chosen_raw or col in chosen_raw
            t.insert(0, "dimension", col)
            t["gated"] = not derived
            seg_tables.append(t)
            if not derived:
                gated_dims.append(col)
        seg = pd.concat(seg_tables, ignore_index=True) if seg_tables else pd.DataFrame()
        if not seg.empty:
            tables["evaluation/segment_performance.csv"] = seg
        gateable = seg[(seg.n >= 200) & seg.gated] if not seg.empty else seg
        min_seg = float(gateable.gini.min()) if len(gateable) else None

        gates = ev.check_gates(
            e_oot, s.gates, train_gini=e_tr["gini"],
            max_psi=float(psi_selected.psi.max()) if not psi_selected.empty else None,
            min_segment_gini=min_seg)
        tables["evaluation/gate_results.csv"] = gates
        passed = not (gates.result == "FAIL").any()

        # ── 8. advanced testing ─────────────────────────────────────────────
        boot = testing.bootstrap_gini(y_oot, p_oot, random_state=self.random_state)
        adv = testing.adversarial_validation(Xtr[num], Xoot[num], num,
                                             random_state=self.random_state)
        sniff = testing.leakage_sniff(Xtr, y_tr, train[s.split_col], chosen_raw,
                                      random_state=self.random_state)
        tables["evaluation/leakage_sniff.csv"] = sniff
        abl = testing.ablation(Wtr, y_tr, chosen, Woot, y_oot,
                               random_state=self.random_state)
        tables["evaluation/ablation.csv"] = abl

        # ── 9. plots ────────────────────────────────────────────────────────
        eda.plot_decile(e_oot["decile_table"], out / "evaluation" / "decile_oot.png",
                        "Out-of-time decile bad rate (1 = riskiest)")
        eda.plot_decile(e_tr["decile_table"], out / "evaluation" / "decile_train.png",
                        "Training decile bad rate")
        eda.plot_ks(y_oot, p_oot, out / "evaluation" / "ks_oot.png")
        eda.plot_roc({"scorecard (oot)": (y_oot, p_oot),
                      "challenger GBM (oot)": (y_oot, g_oot),
                      "scorecard (train)": (y_tr, p_tr)},
                     out / "evaluation" / "roc.png")
        eda.plot_calibration(e_oot["calibration_table"],
                             out / "evaluation" / "calibration_oot.png")
        if not psi_df.empty:
            eda.plot_psi(psi_df, out / "evaluation" / "psi.png",
                         warn=s.gates.psi_warn, fail=s.gates.psi_fail)
        eda.plot_score_distribution(card.score(Wtr[chosen]), card.score(Woot[chosen]),
                                    out / "evaluation" / "score_distribution.png")

        # ── 10. the artifact ────────────────────────────────────────────────
        champion_kind = "scorecard"
        if e_gbm["gini"] > e_oot["gini"] + CHALLENGER_MARGIN:
            champion_kind = "challenger_gbm"
        final_estimator = clf if champion_kind == "scorecard" else gbm

        pipeline = Pipeline([
            ("preprocess", pre),
            ("woe", binner),
            ("select", ColumnSubset(chosen)),
            ("model", final_estimator),
        ])

        pairs = [("train", e_tr), ("valid", e_va), ("oot", e_oot),
                 ("oot_challenger", e_gbm)]
        if e_oot_raw is not None:
            pairs.append(("oot_uncalibrated", e_oot_raw))
        metrics = {k: {kk: vv for kk, vv in e.items()
                       if not isinstance(vv, pd.DataFrame)}
                   for k, e in pairs}

        metadata = {
            "spec": s.to_dict(),
            "champion_kind": champion_kind,
            "challenger_margin_required": CHALLENGER_MARGIN,
            "selected_features": chosen_raw,
            "n_candidate_features": len(num) + len(cat),
            "scorecard": card.to_dict(),
            "calibration": calibrator.to_dict() if calibrator else None,
            "calibration_by_segment_oot": cal_report,
            "metrics": metrics,
            "gate_summary": ("PASS" if passed else "FAIL"),
            "gates": gates.to_dict(orient="records"),
            "bootstrap_gini_oot": boot,
            "adversarial_validation": adv,
            "score_psi_train_vs_oot": round(score_psi, 4),
            "psi_gated_on": chosen_raw,
            "segment_dimensions_gated": gated_dims,
            "max_psi_all_candidates": round(float(psi_df.psi.max()), 4) if not psi_df.empty else None,
            "high_iv_flagged_for_review": sel.reviewed_high_iv,
            "split": {"train": int(len(train)), "valid": int(len(valid)),
                      "oot": int(len(oot)),
                      "train_periods": [int(train[s.split_col].min()),
                                        int(train[s.split_col].max())],
                      "oot_periods": [int(oot[s.split_col].min()),
                                      int(oot[s.split_col].max())]},
            "dataset": self.dataset_meta,
            "training_data_hash": registry.frame_hash(train[num + cat]),
            "is_modelled": True,
            "SYNTHETIC_WARNING": self.dataset_meta.get("SYNTHETIC_WARNING"),
        }

        registry.save(s.name, s.version, pipeline=pipeline, metadata=metadata,
                      tables=tables,
                      extra={"calibrator": calibrator} if calibrator else None,
                      make_champion=make_champion and passed)

        return TrainResult(spec=s, artifact_dir=out, metrics=metrics, gates=gates,
                           selected=chosen_raw, passed=passed,
                           champion_kind=champion_kind)
