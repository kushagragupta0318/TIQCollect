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

# ── the metrics that were computed and never consulted ──────────────────────
# 2026-09-09. KS, Brier, calibration gap and SCORE PSI were all computed, all
# reported, and none of them could trigger anything: only Gini, rank order,
# feature PSI and a missing champion feature did. The audit called that out and
# it is fixed here by ADDING triggers, never by moving an existing one.
#
# Every threshold below is RELATIVE to the model's own development figure, the
# same discipline the Gini trigger already follows: a model that shipped at KS
# 39.7 and now runs at 27.8 has lost 30% of its separation, and that is the
# number that matters rather than whether 27.8 is "good".
#
#   metric            baseline (artifact)   threshold   direction        action
#   ----------------  -------------------   ---------   --------------   ------
#   KS                ks (OOT)              -30% rel.   lower = worse    RETRAIN
#   Brier             brier (OOT)           +25% rel.   higher = worse   RETRAIN
#   calibration gap   0 (perfect)           0.10 abs.   |gap| grows      RETRAIN
#   score PSI         oldest third of win.  0.25 abs.   higher = drift   RETRAIN
#   bad rate shift    bad_rate (OOT)        informational only
#
# WHY THESE NUMBERS.
#   KS at -30%: KS is noisier than Gini at the same n (it reads one point of the
#   distribution, not the whole ranking), so a tighter bound than Gini's 20%
#   would fire on sampling variation. 30% is Gini's trigger widened by half to
#   match that noise, and it is a COLLAPSE detector, not a fine one.
#
#   Brier at +25%: Brier is a cost, so the sign flips. It is bounded and small
#   (0.170 at development), which makes relative movement the only readable
#   scale. 25% is deliberately looser than Gini's 20% because Brier moves with
#   the base rate as well as with skill — a book that genuinely gets worse
#   raises Brier without the model having decayed at all.
#
#   Calibration gap at 0.10 absolute: this one is NOT relative, because its
#   baseline is zero by construction — a calibrated model's mean prediction
#   equals the observed rate. 0.10 is where the allocator starts to be misled:
#   `expected_case_inr = collectable x probability`, so a 10-point gap misprices
#   every case by 10 points of its balance. Development sits at -0.0097.
#
#   Score PSI at 0.25: the SAME constant as feature PSI, deliberately, because
#   it is the same statistic on a different variable and two numbers for one
#   idea is how this repo's worst bugs start. It was already computed and
#   already reported; it simply never gated anything.
#
# None of these fires below MIN_ROWS_FOR_PERFORMANCE or on a one-class cohort:
# they live inside the same branch as the Gini trigger, which those two
# conditions already guard. That is what stops a thin or degenerate cohort from
# manufacturing a retrain.
KS_RELATIVE_DROP_THRESHOLD = 0.30
BRIER_RELATIVE_RISE_THRESHOLD = 0.25
CALIBRATION_GAP_THRESHOLD = 0.10
SCORE_PSI_RETRAIN_THRESHOLD = PSI_RETRAIN_THRESHOLD

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

    # DISTINCT (entity, as_of), not a row count. A re-plan re-scores the whole
    # pool and writes a fresh prediction per case, so the same account-day can
    # hold seven, eight or nine near-identical rows: measured on the live book,
    # nine runs for one plan date produced 7-9 predictions for most cases.
    # Counting rows would let a manager clicking "Re-Plan" repeatedly walk the
    # monitor over its own readiness threshold without a single new borrower,
    # and would then estimate a Gini from eight copies of every error.
    #
    # `entity_id` rather than `case_id` because it is NOT NULL, so a loan-level
    # prediction cannot collapse a whole cohort into one NULL group.
    # `is_modelled` matches `_load`, and it has to. Since 2026-09-09 a
    # below-coverage borrower is RECORDED as a declined prediction — probability
    # NULL, is_modelled False, fallback_reason set — which is the right thing to
    # keep, and exactly the wrong thing to count. Without this filter the gate
    # could open at 500 rows the monitor then drops, reporting a discrimination
    # figure computed on fewer observations than the gate promised.
    def _distinct(*extra):
        q = db.query(ModelPrediction.entity_id, ModelPrediction.as_of_date).filter(
            ModelPrediction.model_name == model,
            ModelPrediction.is_modelled.is_(True),
            ModelPrediction.actual_outcome.isnot(None), *extra)
        return q.distinct().count()

    n_all = _distinct()
    n_right_model = _distinct(ModelPrediction.model_version == version)
    n_both = _distinct(ModelPrediction.model_version == version,
                       ModelPrediction.outcome_definition_version == odv)

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
    #: Set ONLY when the cohort cleared the row threshold but carries one class,
    #: so no discrimination figure exists to put in `performance`. Kept separate
    #: precisely because `performance` being non-empty is what the final verdict
    #: block reads as "metrics were computed" — putting counts there would have
    #: made a single-class cohort report `healthy`.
    outcome_variation: dict = field(default_factory=dict)
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
    rows = q.order_by(ModelPrediction.scored_at.asc()).all()

    # ONE ROW PER (entity, as_of). See `readiness` for why: a re-plan re-scores
    # the whole pool, so the same account-day can carry nine copies of one
    # observation. Pooling them does not add information — it repeats it — and
    # every metric below assumes independent rows. The LAST scored wins, because
    # it is the one the final plan actually used.
    excluded = {"other_model_version": 0, "other_outcome_definition": 0,
                "duplicate_rescores": 0}
    seen: dict[tuple, int] = {}
    deduped = []
    for r in rows:
        key = (r.entity_id, r.as_of_date)
        if key in seen:
            deduped[seen[key]] = r          # later scored_at replaces earlier
            excluded["duplicate_rescores"] += 1
        else:
            seen[key] = len(deduped)
            deduped.append(r)
    rows = deduped

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
    n_classes = int(matured.actual_outcome.nunique()) if rep.n_matured else 0
    if rep.n_matured >= MIN_ROWS_FOR_PERFORMANCE and n_classes > 1:
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

        # ── KS, Brier and calibration: computed since 2026-09-08, consulted
        # since 2026-09-09. Each is relative to the artifact's own development
        # figure except calibration, whose baseline is zero by construction.
        dev_ks = dev.get("ks")
        if dev_ks:
            ks_drop = (dev_ks - ks) / dev_ks
            rep.performance["ks_relative_drop"] = round(ks_drop, 4)
            if ks_drop > KS_RELATIVE_DROP_THRESHOLD:
                rep.retrain_recommended = True
                rep.reasons.append(
                    f"KS has fallen {ks_drop:.0%} below its development value "
                    f"({ks:.2f} against {dev_ks:.2f})")
        dev_brier = dev.get("brier")
        if dev_brier:
            brier_rise = (brier - dev_brier) / dev_brier
            rep.performance["brier_relative_rise"] = round(brier_rise, 4)
            if brier_rise > BRIER_RELATIVE_RISE_THRESHOLD:
                rep.retrain_recommended = True
                rep.reasons.append(
                    f"Brier score has risen {brier_rise:.0%} above its "
                    f"development value ({brier:.5f} against {dev_brier:.5f}) "
                    f"— the probability the allocator multiplies by is less "
                    f"accurate, whatever the ranking says")
        gap = rep.performance["calibration_gap"]
        if abs(gap) > CALIBRATION_GAP_THRESHOLD:
            rep.retrain_recommended = True
            rep.reasons.append(
                f"calibration is off by {gap:+.3f} — the model predicts a "
                f"{'higher' if gap > 0 else 'lower'} bad rate than occurred, "
                f"and expected recovery is that probability times the "
                f"collectable balance")
    elif rep.n_matured >= MIN_ROWS_FOR_PERFORMANCE:
        # ENOUGH ROWS, ONE CLASS. 2026-09-09: this branch used to fall into the
        # message below and report "only 1884 matured outcomes; performance
        # needs 500" — self-contradictory, and the line that would actually have
        # appeared on 2026-10-08, because the demo book holds no payments after
        # 2026-09-08 and every matured row would have labelled NOT_RECOVERED.
        #
        # Discrimination is undefined without both classes: AUC asks how well
        # the score separates two groups and there is only one group. That is a
        # fact about the COHORT, not a shortfall in it, and the two need
        # different names or the reader is told to wait for rows that have
        # already arrived.
        only = ("every outcome is 'not recovered'"
                if float(matured.actual_outcome.mean()) >= 0.5
                else "every outcome is 'recovered'")
        rep.outcome_variation = {
            "n_matured": rep.n_matured,
            "n_classes": n_classes,
            "bad_rate_live": round(float(matured.actual_outcome.mean()), 4),
            "required_rows": MIN_ROWS_FOR_PERFORMANCE,
        }
        rep.reasons.append(
            f"{rep.n_matured} matured outcomes, which clears the {MIN_ROWS_FOR_PERFORMANCE} "
            f"threshold, but they contain ONE class — {only}. Gini, KS and AUC "
            f"are undefined without both, so no discrimination figure is "
            f"reported rather than a misleading one. This is a property of the "
            f"cohort, not too little data")
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
            score_psi = rep.stability["score_psi"]
            if score_psi is not None and score_psi > SCORE_PSI_RETRAIN_THRESHOLD:
                rep.retrain_recommended = True
                rep.reasons.append(
                    f"the SCORE distribution has shifted past PSI "
                    f"{SCORE_PSI_RETRAIN_THRESHOLD} ({score_psi}) — the served "
                    f"population is no longer the one the bands were fitted on, "
                    f"which moves every band boundary even where each feature "
                    f"individually looks stable")
            if worst > PSI_RETRAIN_THRESHOLD:
                rep.retrain_recommended = True
                shifted = psi_df[psi_df.psi > PSI_RETRAIN_THRESHOLD].feature.tolist()
                rep.reasons.append(
                    f"population has shifted past PSI {PSI_RETRAIN_THRESHOLD} on "
                    f"{', '.join(shifted)}")
            # 2026-09-16 — MISSINGNESS, which PSI cannot see: `evaluate.psi`
            # drops NaN on both sides, so a feature whose missing share moves
            # from 49% to 37% reads "stable" (measured on the reference GAM's
            # own inputs). Its own check, its own threshold, its own action;
            # `evaluate.psi` is unchanged. A breach is a serving/data finding
            # like a missing champion feature, and is treated the same way.
            if feats:
                from app.ml.pipeline.missingness import breached_features, missingness_report
                miss_rep = missingness_report(ref, cur, feats)
                rep.stability["missingness"] = miss_rep
                breached = breached_features(miss_rep)
                if breached:
                    rep.retrain_recommended = True
                    detail = "; ".join(
                        f"{r['feature']} {r['baseline_absent_rate']:.1%} -> {r['current_absent_rate']:.1%}"
                        for r in miss_rep if r["breached"] and r["baseline_absent_rate"] is not None)
                    rep.reasons.append(
                        f"the share of rows with NO observation moved on {', '.join(breached)} "
                        f"({detail}) — invisible to PSI, which drops missing values; see "
                        f"ml/pipeline/missingness.py for the action")

    if rep.retrain_recommended:
        # Stability runs on every row regardless of labels, so a missing
        # champion feature or a PSI break still wins — correctly: that is a
        # serving fault, and it does not need outcomes to be true.
        rep.verdict = "retrain_recommended"
    elif rep.performance:
        rep.verdict = "healthy"
    elif rep.outcome_variation:
        # Enough rows, one class. Distinct from `insufficient_data`, which means
        # "come back when more rows mature" — these rows have already matured
        # and more of the same will not help.
        rep.verdict = "insufficient_outcome_variation"
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
