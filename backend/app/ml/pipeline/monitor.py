# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Closes the loop: reads back what the models predicted,
#   compares it with what happened, and says whether the model has decayed.
#
#   A MONITOR THAT ONLY REPORTS IS NOT A MONITOR. Every check here returns a
#   verdict and a retrain recommendation, because the failure mode of model
#   monitoring is a dashboard nobody reads while a scorecard quietly stops
#   working. The triggers are explicit and few: population shift past a
#   threshold, or a relative Gini drop past a threshold.
#
#   PROMOTION STAYS MANUAL. This recommends; it never retrains and never
#   promotes. That mirrors the rollout-gate discipline already in the codebase
#   (REPAYMENT_WRITE_RISK_SCORE and friends default False): an automated system
#   that can replace its own decision model in production without a person is a
#   larger commitment than anything else in this repo, and it is not one this
#   change is making.
# ───────────────────────────────────────────────────────────────────────────
"""
Post-deployment model monitoring.

    from app.ml.pipeline.monitor import monitor_model
    report = monitor_model(db, "recovery_risk")

Reads `model_predictions`, computes discrimination on rows whose outcome has
matured and stability on all of them, and returns a report with a verdict.

MATURED ROWS ONLY, FOR PERFORMANCE. `actual_outcome IS NULL` means "not yet
known", never "no". Counting unmatured rows as non-events is the single easiest
way to manufacture a reassuring number, and it gets more reassuring the more
recent data you add.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss
from sqlalchemy.orm import Session

from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.engine import DecisionEngine
from app.models.model_prediction import ModelPrediction

logger = logging.getLogger(__name__)

# Retrain triggers. Both are relative to the model's OWN development-time
# figures, taken from its artifact metadata, not to an absolute standard — a
# model that shipped at Gini 0.52 and now runs at 0.41 has lost 21% of its
# discrimination, and that is the number that matters, not whether 0.41 is
# "good".
PSI_RETRAIN_THRESHOLD = 0.25
GINI_RELATIVE_DROP_THRESHOLD = 0.20
MIN_ROWS_FOR_PERFORMANCE = 500

#: The gate the nightly task consults before running anything expensive.
#: Same number as MIN_ROWS_FOR_PERFORMANCE and deliberately a separate name:
#: one is "how many rows before a Gini means something", the other is "how many
#: before we bother looking". They coincide today; conflating them would hide a
#: decision.
#:
#: 500 is not arbitrary. At the model's development bad rate (0.718) and OOT
#: AUC (0.757), Hanley-McNeil gives SE(AUC) ~= 0.022 at n=500, so a 95% interval
#: on Gini spans about +/- 0.085 — wide, but narrow enough to see the 20%
#: relative drop that triggers a retrain. Below it the interval swallows the
#: trigger and the verdict would be noise wearing a number.
MIN_MATURED_FOR_MONITORING = 500


@dataclass
class Readiness:
    """Whether there is enough matured, VERSION-CONSISTENT data to judge."""

    ready: bool
    n_matured: int
    required: int
    model: str
    model_version: str | None
    outcome_definition_version: str | None
    n_excluded_other_model_version: int = 0
    n_excluded_other_outcome_version: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @property
    def status(self) -> str:
        return "ready" if self.ready else "not_ready"


def readiness(db: Session, model: str, *, version: str | None = None,
              outcome_definition_version: str | None = None,
              min_matured: int | None = None) -> Readiness:
    """A COUNT-only gate. Cheap enough to run every night before outcomes exist.

    Deliberately does NOT build a DataFrame: the whole point is that the nightly
    task can ask "is there anything to look at yet?" for the price of two
    aggregate queries, every night from now until outcomes mature, without
    loading a row.

    It counts only rows that are consistent on BOTH versions — see
    `_load`'s note. A count that pools two model versions or two outcome
    definitions is not a smaller version of the right answer; it is a different
    question with the same units.
    """
    from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION

    required = min_matured if min_matured is not None else MIN_MATURED_FOR_MONITORING
    version = version or serving_version(model)
    odv = outcome_definition_version or OUTCOME_DEFINITION_VERSION

    matured = db.query(ModelPrediction).filter(
        ModelPrediction.model_name == model,
        ModelPrediction.actual_outcome.isnot(None),
    )
    n_all = matured.count()
    n_right_model = matured.filter(
        ModelPrediction.model_version == version).count()
    n_both = matured.filter(
        ModelPrediction.model_version == version,
        ModelPrediction.outcome_definition_version == odv).count()

    return Readiness(
        ready=n_both >= required,
        n_matured=n_both,
        required=required,
        model=model,
        model_version=version,
        outcome_definition_version=odv,
        n_excluded_other_model_version=n_all - n_right_model,
        n_excluded_other_outcome_version=n_right_model - n_both,
    )


def serving_version(model: str) -> str | None:
    """The version that is ACTUALLY serving, resolved the way scoring resolves it.

    `monitor_model(version=None)` used to mean "every version at once", and it
    then reported `rep.version` as the modal one — labelling a pooled population
    with a single version string. Monitoring the wrong thing under a confident
    label is worse than not monitoring. The default is now the serving version,
    and "all versions" has to be asked for.
    """
    engine = DecisionEngine.get(model)
    return engine.version if engine else None


@dataclass
class MonitorReport:
    model: str
    version: str | None = None
    #: Which labelling rule produced the outcomes this report was computed on.
    #: A Gini is meaningless without it — the same scores against two target
    #: definitions are two different measurements.
    outcome_definition_version: str | None = None
    n_predictions: int = 0
    n_matured: int = 0
    verdict: str = "insufficient_data"
    retrain_recommended: bool = False
    reasons: list[str] = field(default_factory=list)
    performance: dict = field(default_factory=dict)
    stability: dict = field(default_factory=dict)
    #: What was left out to keep the population version-consistent, so an
    #: exclusion is visible rather than inferred from a row count that looks low.
    excluded: dict = field(default_factory=dict)
    decile_table: pd.DataFrame | None = None
    calibration_table: pd.DataFrame | None = None

    def to_dict(self) -> dict:
        skip = {"decile_table", "calibration_table"}
        d = {k: v for k, v in self.__dict__.items() if k not in skip}
        for name in skip:
            tbl = getattr(self, name)
            if tbl is not None:
                d[name] = tbl.to_dict(orient="records")
        return d


def _load(db: Session, model: str, version: str | None,
          since: date | None, outcome_definition_version: str | None,
          ) -> tuple[pd.DataFrame, dict]:
    """Version-consistent predictions, plus a count of what that excluded.

    TWO VERSIONS GATE THIS POPULATION, and they gate it differently.

    `model_version` filters EVERY row: scores from 1.0.0 and 1.1.0 are different
    numbers and pooling them measures neither model.

    `outcome_definition_version` filters only the MATURED rows, and it has to be
    applied to the label rather than to the row — an unmatured prediction has no
    outcome definition yet, so excluding it would shrink the stability
    population (which is computed on everything, matured or not) for a reason
    that has nothing to do with stability.

    Before 2026-09-09 neither filter was applied by default: `version` defaulted
    to None meaning "all", and `outcome_definition_version` did not exist here at
    all. The report then set `version` to the modal value, so a mixed population
    was labelled with one version string and read as if it described that model.
    """
    q = db.query(ModelPrediction).filter(ModelPrediction.model_name == model,
                                         ModelPrediction.is_modelled.is_(True))
    if since:
        q = q.filter(ModelPrediction.as_of_date >= since)
    rows = q.all()

    excluded = {"other_model_version": 0, "other_outcome_definition": 0}
    kept = []
    for r in rows:
        if version and r.model_version != version:
            excluded["other_model_version"] += 1
            continue
        outcome = r.actual_outcome
        if (outcome is not None and outcome_definition_version
                and r.outcome_definition_version != outcome_definition_version):
            # Keep the row for stability, drop only its LABEL. The features are
            # still a valid observation of the served population.
            excluded["other_outcome_definition"] += 1
            outcome = None
        kept.append({
            "as_of_date": r.as_of_date,
            "model_version": r.model_version,
            "outcome_definition_version": r.outcome_definition_version,
            "probability": r.probability,
            "points": r.points,
            "band": r.band,
            "actual_outcome": outcome,
            **(r.features or {}),
        })
    if not kept:
        return pd.DataFrame(), excluded
    return pd.DataFrame(kept), excluded


def monitor_model(db: Session, model: str, *, version: str | None = None,
                  outcome_definition_version: str | None = None,
                  lookback_days: int = 180,
                  all_versions: bool = False) -> MonitorReport:
    """Judge one model version against one outcome definition.

    `version=None` means THE SERVING VERSION, not "all of them". Pass
    `all_versions=True` to deliberately pool, which is a question about the
    served population rather than about a model, and says so in the report.
    """
    from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION

    since = (datetime.now(timezone.utc).date() - timedelta(days=lookback_days))
    resolved = None if all_versions else (version or serving_version(model))
    odv = (None if all_versions else
           (outcome_definition_version or OUTCOME_DEFINITION_VERSION))

    df, excluded = _load(db, model, resolved, since, odv)
    rep = MonitorReport(model=model, version=resolved,
                        outcome_definition_version=odv, excluded=excluded)
    if df.empty:
        rep.reasons.append("no predictions recorded in the window")
        return rep

    rep.n_predictions = int(len(df))
    if resolved is None:
        # Pooled on purpose. Never present that as a single version.
        seen = sorted(v for v in df.model_version.dropna().unique())
        rep.version = "|".join(seen) if len(seen) > 1 else (seen[0] if seen else None)
        if len(seen) > 1:
            rep.reasons.append(
                f"POOLED across model versions {seen} — discrimination here "
                f"describes no single model")

    matured = df[df.actual_outcome.notna()].copy()
    rep.n_matured = int(len(matured))

    engine = DecisionEngine.get(model, resolved or "champion")
    dev = ((engine.metadata.get("metrics") or {}).get("oot") or {}) if engine else {}
    dev_gini = dev.get("gini")

    # ── discrimination and calibration, on matured rows only ────────────────
    if rep.n_matured >= MIN_ROWS_FOR_PERFORMANCE and matured.actual_outcome.nunique() > 1:
        y = matured.actual_outcome.astype(int).to_numpy()
        s = matured.probability.astype(float).to_numpy()
        live_gini = ev.gini(y, s)
        ks, _ = ev.ks_statistic(y, s)
        dt = ev.decile_table(y, s)
        ro = ev.rank_order_breaks(dt)
        rep.decile_table = dt
        # `probability` IS the calibrated P(bad) the allocator consumed, so the
        # Brier score here measures the number that was actually acted on — not
        # a rescaled proxy for it.
        brier = float(brier_score_loss(y, s))
        rep.calibration_table = ev.calibration_table(y, s)
        rep.performance = {
            "n": int(len(y)),
            "auc_live": round((live_gini + 1) / 2, 4),
            "gini_live": round(live_gini, 4),
            "gini_at_development": dev_gini,
            "gini_relative_drop": (round((dev_gini - live_gini) / dev_gini, 4)
                                   if dev_gini else None),
            "ks_live": round(ks, 2),
            "ks_at_development": dev.get("ks"),
            "brier_live": round(brier, 5),
            "brier_at_development": dev.get("brier"),
            # Positive => the model predicts more risk than actually occurred.
            "calibration_gap": round(float(s.mean() - y.mean()), 4),
            "mean_predicted": round(float(s.mean()), 4),
            "bad_rate_live": round(float(y.mean()), 4),
            "recovery_rate_live": round(1.0 - float(y.mean()), 4),
            "bad_rate_at_development": dev.get("bad_rate"),
            "rank_order_breaks": ro["n_breaks"],
        }
        drop = rep.performance["gini_relative_drop"]
        if drop is not None and drop > GINI_RELATIVE_DROP_THRESHOLD:
            rep.retrain_recommended = True
            rep.reasons.append(
                f"Gini has fallen {drop:.0%} below its development value "
                f"({live_gini:.3f} against {dev_gini:.3f})")
        if ro["n_breaks_top5"] > 0:
            rep.retrain_recommended = True
            rep.reasons.append(
                f"rank order is broken in the top five deciles at "
                f"{ro['breaks']} — the end of the book the business acts on")
    else:
        rep.reasons.append(
            f"only {rep.n_matured} matured outcomes; performance needs "
            f"{MIN_ROWS_FOR_PERFORMANCE}. Outcomes that have not matured are NULL, "
            f"never 0 — counting them as non-events would invent a good number")

    # ── stability, on everything ────────────────────────────────────────────
    # Reference is the OLDEST third of the window rather than the training set:
    # the training frame is not carried in the artifact (deliberately — it can
    # be large and it is regenerable), and drift within the served population is
    # the question a live monitor is actually asking.
    champion = list(engine.selected) if engine else []
    feats = [f for f in champion if f in df.columns]
    # A champion feature ABSENT from the served vectors is not a stability
    # finding, it is a serving fault — that is exactly how `ptp_kept_ratio`
    # went missing for a full cycle while coverage stayed above its floor.
    # Reported here rather than skipped over, because a shorter PSI table is
    # otherwise indistinguishable from a healthy one.
    missing = [f for f in champion if f not in df.columns]

    if len(df) >= 200:
        cut = df.as_of_date.quantile(0.33) if df.as_of_date.notna().any() else None
        ref, cur = (df[df.as_of_date <= cut], df[df.as_of_date > cut]) if cut else (df, df)
        if len(ref) >= 50 and len(cur) >= 50:
            # Score PSI does not depend on the feature columns, so it is
            # computed unconditionally. It used to sit inside the feature
            # branch, which meant a model whose features were all missing
            # reported no drift at all rather than the drift plus the fault.
            psi_df = ev.psi_frame(ref, cur, feats) if feats else pd.DataFrame()
            worst = float(psi_df.psi.max()) if not psi_df.empty else 0.0
            rep.stability = {
                "reference_rows": int(len(ref)),
                "current_rows": int(len(cur)),
                "score_psi": round(ev.psi(ref.probability, cur.probability), 4),
                "max_feature_psi": round(worst, 4) if feats else None,
                "champion_features": champion,
                "features_missing_from_predictions": missing,
                "per_feature": psi_df.to_dict(orient="records"),
            }
            if missing:
                rep.retrain_recommended = True
                rep.reasons.append(
                    f"champion features absent from the served vectors: "
                    f"{', '.join(missing)} — the model is scoring without inputs "
                    f"it was selected on, which is a serving fault, not drift")
            if worst > PSI_RETRAIN_THRESHOLD:
                rep.retrain_recommended = True
                shifted = psi_df[psi_df.psi > PSI_RETRAIN_THRESHOLD].feature.tolist()
                rep.reasons.append(
                    f"population has shifted past PSI {PSI_RETRAIN_THRESHOLD} on "
                    f"{', '.join(shifted)}")

    if rep.retrain_recommended:
        rep.verdict = "retrain_recommended"
    elif rep.performance:
        rep.verdict = "healthy"
    else:
        rep.verdict = "insufficient_data"

    if not rep.reasons:
        rep.reasons.append("within thresholds on discrimination and stability")
    return rep


# ─── REMOVED 2026-09-09: `attach_outcomes(db, model, horizon_days, outcome_fn)`
#
# This module used to carry its OWN labeller — a generic loop that took an
# `outcome_fn` callback, on the reasoning that "the outcome definition belongs
# to the model, not to this module". The reasoning was right and the code was
# the wrong way to act on it. `app/ml/pipeline/outcomes.py` is now the one place
# the `recovery_risk` label is defined, and `workers/tasks/model_outcomes.py`
# is the one thing that runs it.
#
# It was DEAD — nothing imported it, verified across app/, scripts/ and tests/.
# Removing dead code is housekeeping; removing THIS dead code is a correctness
# fix, because of what it wrote:
#
#     row.actual_outcome        <- set
#     row.outcome_attached_at   <- set
#     row.outcome_horizon_days  <- set
#     row.outcome_definition_version   <- NEVER SET
#     row.outcome_status               <- NEVER SET
#
# So a caller who reached for it — and the name, the signature and the position
# beside `monitor_model` all invite that — would have written labels with no
# record of WHICH RULE produced them, into the same column the versioned
# labeller writes. Rows labelled under two definitions answer different
# questions; that is the entire reason `outcome_definition_version` exists, and
# this function silently opted out of it. It is also the exact shape of defect
# this repo has been bitten by repeatedly: two risk_score formulas, two
# recovery_potential writers, seven DPD→bucket spellings.
#
# Use `app.ml.pipeline.outcomes.attach_outcomes(db, model_name)`.
# `tests/test_model_outcomes.py` covers it; `test_only_one_module_writes_the
# _outcome_label` in `tests/test_label_comparison.py` is the tripwire against
# a third copy appearing.
# ───────────────────────────────────────────────────────────────────────────
