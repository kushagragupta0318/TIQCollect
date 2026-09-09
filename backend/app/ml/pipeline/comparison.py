# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. Challenger against the model that is ACTUALLY DEPLOYED.
#
#   `train.py`'s `champion_kind` picks the scorecard or the GBM from inside ONE
#   training run. It is not an incumbent comparison and the 2026-09-09 audit
#   said so: a candidate that cleared every absolute gate was promotable even if
#   it was worse than the model already running, because nothing had scored the
#   incumbent on the candidate's data.
#
#   That is the failure this module exists to prevent, and it is not
#   hypothetical: absolute gates are wide (gini_min 0.25 against a champion at
#   0.5136), so almost any competent refit passes them while losing.
# ───────────────────────────────────────────────────────────────────────────
"""Score both models on the SAME out-of-time rows and decide on stated rules.

    result = compare_to_incumbent(model, challenger_pipeline, oot, features)

EVERY THRESHOLD IS DERIVED FROM SAMPLING ERROR, NOT PICKED. The question a
comparison gate has to answer is "is this difference bigger than the noise in
measuring it", and Hanley-McNeil gives that number from the incumbent's own AUC
and the class balance of the frame being scored. A fixed 0.02 would be far too
strict on 600 rows and far too loose on 60,000.
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

from app.ml.pipeline import evaluate as ev

logger = logging.getLogger(__name__)

#: How many standard errors of the Gini difference the challenger must clear to
#: count as BETTER rather than as indistinguishable. Two is the conventional
#: 95%-ish reading and is the same standard the 500-row monitoring floor was
#: derived against, so the two numbers are consistent with each other.
UPLIFT_SIGMA = 2.0

#: A floor under the tolerance, so an enormous frame cannot make a
#: 0.001 Gini difference "significant" and churn the champion for nothing.
#: 0.01 Gini is the smallest difference anyone would act on.
MIN_MATERIAL_UPLIFT = 0.01

#: Brier is a cost: lower is better. A challenger may be no worse than the
#: incumbent by more than this, expressed as a SHARE of the incumbent's Brier
#: so it scales with the base rate rather than assuming one.
MAX_BRIER_REGRESSION_PCT = 0.05

#: KS is noisier than Gini at the same n, so it is a guard against collapse
#: rather than a fine comparison: a challenger may give up at most this share
#: of the incumbent's KS.
MAX_KS_REGRESSION_PCT = 0.10

#: Calibration gap is signed (mean predicted - observed). The challenger's
#: ABSOLUTE gap may exceed the incumbent's by at most this much, because a
#: better-ordering but badly-calibrated model would still damage the allocator:
#: `expected_case_inr = collectable x probability` consumes the probability
#: itself, not the rank.
MAX_CALIBRATION_REGRESSION = 0.05


def gini_se(auc: float, n_pos: int, n_neg: int) -> float:
    """Hanley-McNeil standard error of an AUC, doubled onto the Gini scale.

    Gini = 2*AUC - 1, so SE(Gini) = 2*SE(AUC). Same estimator the monitoring
    threshold note uses; kept in one place so the two cannot drift.
    """
    if n_pos <= 0 or n_neg <= 0:
        return float("inf")
    a = max(min(float(auc), 0.999999), 0.500001)
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    var = (a * (1 - a) + (n_pos - 1) * (q1 - a * a)
           + (n_neg - 1) * (q2 - a * a)) / (n_pos * n_neg)
    return 2.0 * math.sqrt(max(var, 0.0))


@dataclass
class SideMetrics:
    version: str
    gini: float
    auc: float
    ks: float
    brier: float
    calibration_gap: float
    rank_order_breaks: int
    rank_order_breaks_top5: int
    n: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ComparisonResult:
    comparison_run_id: str
    model_name: str
    challenger_version: str
    incumbent_version: str
    challenger: dict
    incumbent: dict
    gates: list = field(default_factory=list)
    passed: bool = False
    verdict: str = "rejected"
    gini_uplift: float = 0.0
    uplift_tolerance: float = 0.0
    n_oot: int = 0
    reasons: list = field(default_factory=list)
    segment: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _side(version: str, y: np.ndarray, p: np.ndarray) -> SideMetrics:
    g = ev.gini(y, p)
    ks, _ = ev.ks_statistic(y, p)
    ro = ev.rank_order_breaks(ev.decile_table(y, p))
    return SideMetrics(
        version=version,
        gini=round(float(g), 6),
        auc=round(float((g + 1) / 2), 6),
        ks=round(float(ks), 4),
        brier=round(float(brier_score_loss(y, p)), 6),
        calibration_gap=round(float(p.mean() - y.mean()), 6),
        rank_order_breaks=int(ro["n_breaks"]),
        rank_order_breaks_top5=int(ro["n_breaks_top5"]),
        n=int(len(y)),
    )


def _gate(name: str, observed, threshold: str, ok: bool, why: str) -> dict:
    return {"gate": name, "observed": observed, "threshold": threshold,
            "result": "PASS" if ok else "FAIL", "reason": why}


def compare_to_incumbent(
    model_name: str,
    *,
    challenger_version: str,
    challenger_scores: np.ndarray,
    incumbent_version: str,
    incumbent_scores: np.ndarray,
    y: np.ndarray,
    segment_series: pd.Series | None = None,
) -> ComparisonResult:
    """Both score vectors must be over the SAME rows in the SAME order.

    The caller is responsible for that and it is the only thing that makes the
    comparison meaningful — two Ginis from two frames are two facts about two
    populations, not a comparison.
    """
    y = np.asarray(y).astype(int)
    ch = _side(challenger_version, y, np.asarray(challenger_scores, dtype=float))
    inc = _side(incumbent_version, y, np.asarray(incumbent_scores, dtype=float))

    n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
    # The tolerance is a property of THIS frame and THIS incumbent, computed
    # fresh every comparison. Two independent measurements on shared rows are
    # positively correlated, so treating them as independent (the sqrt(2) below)
    # is conservative — it demands a slightly larger uplift than strictly
    # necessary, which is the safe direction for replacing a working model.
    se = gini_se(inc.auc, n_pos, n_neg) * math.sqrt(2.0)
    tolerance = max(UPLIFT_SIGMA * se, MIN_MATERIAL_UPLIFT)
    uplift = round(ch.gini - inc.gini, 6)

    gates = [
        _gate("gini_materially_better", uplift, f"> +{tolerance:.4f}",
              uplift > tolerance,
              "the challenger must beat the incumbent by more than the "
              "sampling error of the difference; within tolerance the "
              "incumbent is kept, because replacing a proven model with an "
              "indistinguishable one is churn with a rollback attached"),
        _gate("brier_not_worse", ch.brier,
              f"<= {inc.brier * (1 + MAX_BRIER_REGRESSION_PCT):.6f}",
              ch.brier <= inc.brier * (1 + MAX_BRIER_REGRESSION_PCT),
              "the allocator multiplies by the probability itself, so a "
              "better-ordering but worse-calibrated model still loses money"),
        _gate("ks_not_collapsed", ch.ks,
              f">= {inc.ks * (1 - MAX_KS_REGRESSION_PCT):.4f}",
              ch.ks >= inc.ks * (1 - MAX_KS_REGRESSION_PCT),
              "separation must not fall away even where Gini holds"),
        _gate("calibration_not_worse", abs(ch.calibration_gap),
              f"<= {abs(inc.calibration_gap) + MAX_CALIBRATION_REGRESSION:.4f}",
              abs(ch.calibration_gap) <= abs(inc.calibration_gap)
              + MAX_CALIBRATION_REGRESSION,
              "expected recovery is a rupee figure derived from this number"),
        _gate("rank_order_top5_not_worse", ch.rank_order_breaks_top5,
              f"<= {inc.rank_order_breaks_top5}",
              ch.rank_order_breaks_top5 <= inc.rank_order_breaks_top5,
              "the top deciles are the end of the book the business acts on"),
    ]

    segment_rows: list = []
    if segment_series is not None and len(segment_series) == len(y):
        try:
            for label, idx in pd.Series(range(len(y))).groupby(
                    segment_series.reset_index(drop=True)).groups.items():
                sel = np.asarray(list(idx))
                if len(sel) < 100 or len(set(y[sel])) < 2:
                    continue
                cg = ev.gini(y[sel], np.asarray(challenger_scores)[sel])
                ig = ev.gini(y[sel], np.asarray(incumbent_scores)[sel])
                segment_rows.append({"segment": str(label), "n": int(len(sel)),
                                     "challenger_gini": round(float(cg), 4),
                                     "incumbent_gini": round(float(ig), 4),
                                     "uplift": round(float(cg - ig), 4)})
        except Exception as exc:                       # pragma: no cover
            logger.warning("ml.comparison.segment_failed error=%s", exc)

    passed = all(g["result"] == "PASS" for g in gates)
    if passed:
        verdict = "challenger_better"
    elif abs(uplift) <= tolerance:
        # Named separately from a loss. "Indistinguishable" and "worse" call for
        # the same ACTION here (keep the incumbent) but they are different
        # findings, and collapsing them would hide a model that is ready except
        # for needing more data.
        verdict = "equivalent_keep_incumbent"
    else:
        verdict = "challenger_worse"

    return ComparisonResult(
        comparison_run_id=f"cmp-{uuid.uuid4().hex[:12]}",
        model_name=model_name,
        challenger_version=challenger_version,
        incumbent_version=incumbent_version,
        challenger=ch.to_dict(), incumbent=inc.to_dict(),
        gates=gates, passed=passed, verdict=verdict,
        gini_uplift=uplift, uplift_tolerance=round(tolerance, 6),
        n_oot=int(len(y)),
        reasons=[f"{g['gate']}: observed {g['observed']} against "
                 f"{g['threshold']}" for g in gates if g["result"] == "FAIL"],
        segment=segment_rows,
    )
