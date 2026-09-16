# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-16 — GAM serving. The production-readiness audit found this engine
#   could not load recovery_risk 2.2.0 (an exactly-additive boosted model): it
#   assumed a sklearn Pipeline with WOE steps and a points table. It now reads
#   the artifact's `model_type`; a "gam" artifact is a `gam.GamModel` that
#   takes the adapter's raw vector, and its band comes from the artifact's
#   probability bands, its reason codes from the exact per-tree decomposition
#   (`_explain_gam`). The 1.x / 2.0 / 2.1 path is untouched — same
#   predict_proba, same calibrator call, same ScoreCard.explain — and every
#   ScoreResult now carries `versions` (empty dict on the old path beyond
#   version + artifact hash), so a served score names what produced it.
# 2026-09-08 — NEW. The serving seam: load a pickled model once, score a dict,
#   return a probability, a band, reason codes and — always — whether the number
#   came from a model at all.
#
#   `is_modelled` TRAVELS ON THE OBJECT. That is this repo's existing rule
#   (ScoreOutcome, RepaymentScore, LLMResult.ai_generated) and it matters more
#   here, not less: a DecisionEngine that cannot load its artifact still answers,
#   and it answers with the hand-weighted fallback. A caller that forgets to
#   check would otherwise render a scorecard behind an "AI" chip.
#
#   MISSING FEATURES ARE A FIRST-CLASS CASE, NOT AN ERROR. The models are fitted
#   on a panel that carries fields the live schema does not yet have
#   (address_vintage_months, phone_verified, mail_returned_count). Rather than
#   refuse to score, the engine passes NaN and the WOE binner routes it to the
#   feature's Missing bin — which is exactly what that bin was fitted for. What
#   it will NOT do is hide it: every result carries `feature_coverage`, and
#   below a floor the engine declines and says why.
# ───────────────────────────────────────────────────────────────────────────
"""
Model serving.

    from app.ml.pipeline.engine import DecisionEngine

    engine = DecisionEngine.get("recovery_risk")
    out = engine.score({"dpd": 62, "cibil_score": 611, ...})
    out.probability      # 0.81   P(no material payment next cycle)
    out.points           # 548    scorecard points, higher = safer
    out.band             # "E"
    out.reason_codes     # [{feature, points, points_lost}, ...]
    out.is_modelled      # True

Engines are cached per (model, version) — a joblib load is expensive and the
artifact is immutable for a given version, so loading it per request would be
pure waste.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd

from app.ml.pipeline import registry
from app.ml.pipeline.gam import GamModel, ProbabilityBands, points_from_logit, reason_codes
from app.ml.pipeline.scorecard import DEFAULT_BANDS, ScoreCard, band_for

logger = logging.getLogger(__name__)

# Below this share of the model's own features being supplied, the engine
# declines rather than scoring. A scorecard with two of seven inputs present is
# not a cautious estimate, it is the intercept plus noise, and returning it with
# a plausible-looking probability is worse than returning nothing.
MIN_FEATURE_COVERAGE = 0.60


@dataclass
class ScoreResult:
    """One scored entity. Everything a caller needs to render it honestly."""

    probability: float | None
    points: int | None
    band: str | None
    model: str
    version: str
    is_modelled: bool
    reason_codes: list[dict] = field(default_factory=list)
    feature_points: dict[str, int] = field(default_factory=dict)
    feature_coverage: float = 0.0
    missing_features: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    # 2026-09-16 — what produced this number: artifact version and hash, and
    # for a GAM the feature-definition, calibration, band-table, reason-code
    # and background versions it was scored with. Empty on a declined score.
    versions: dict = field(default_factory=dict)
    # GAM only: intercept + every contribution == logit, exactly. Empty for a
    # scorecard (its per-feature points are `feature_points`).
    contributions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class DecisionEngine:
    """Loads one model artifact and scores against it."""

    _cache: dict[tuple[str, str], "DecisionEngine"] = {}
    _lock = threading.Lock()

    def __init__(self, model: str, version: str = "champion"):
        self.model = model
        self.pipeline, self.metadata = registry.load(model, version)
        self.version = self.metadata.get("version", version)
        self.spec = self.metadata.get("spec", {})
        self.expected_features: list[str] = (
            list(self.spec.get("numeric_features", ()))
            + list(self.spec.get("categorical_features", ()))
        )
        self.selected: list[str] = self.metadata.get("selected_features", [])
        # 2026-09-15. Selected features whose NaN is an observation rather
        # than a missing input. Absent from every 1.x artifact, so their
        # coverage arithmetic is exactly what it was.
        self.abstaining: set[str] = set(self.spec.get("abstaining_features", ()) or ())

        # The calibrator sits beside the pipeline, not inside it: it needs the
        # RAW overdue_amount, which the WOE transform has already replaced by
        # the time the estimator runs. Absent for a model trained before 1.1.0,
        # in which case scores are returned uncalibrated and say so.
        self.calibrator = registry.load_extra(model, version, "calibrator")
        self.calibration_meta = self.metadata.get("calibration")

        # 2026-09-16 — the version stamps a served score carries. Every
        # artifact has a version and a hash; a 2.2.0 GAM adds the rest.
        self.model_type: str = self.metadata.get("model_type") or "woe_logistic"
        self.versions: dict = {
            "model_artifact": self.version,
            "artifact_sha256": self.metadata.get("artifact_sha256"),
            **{k: v for k, v in (self.metadata.get("versions") or {}).items()
               if k != "model_artifact"},
        }

        # A GAM explains itself from its own trees rather than from a points
        # table: bands on the calibrated probability, reason codes from the
        # exact per-feature decomposition. `gam.py` is the one definition.
        self.gam: GamModel | None = None
        self.bands: ProbabilityBands | None = None
        if self.model_type == "gam":
            if not isinstance(self.pipeline, GamModel):
                raise TypeError(f"{model}/{self.version} says model_type=gam but the artifact "
                                f"is {type(self.pipeline).__name__}")
            self.gam = self.pipeline
            self.gam.tree_groups()             # refuses an undeclared interaction
            rb = self.metadata.get("risk_bands")
            self.bands = ProbabilityBands.from_dict(rb) if rb else None
            pts = self.metadata.get("points") or {}
            self._points_scale = {"pdo": pts.get("pdo", 20), "base_score": pts.get("base_score", 600),
                                  "base_odds": pts.get("base_odds", 50.0)}

        sc = self.metadata.get("scorecard") or {}
        self.card: ScoreCard | None = None
        if sc.get("features"):
            self.card = ScoreCard(
                sc["features"], np.asarray(sc["coefficients"], dtype=float),
                float(sc["intercept"]), pdo=sc.get("pdo", 20),
                base_score=sc.get("base_score", 600),
                base_odds=sc.get("base_odds", 50.0),
                bands=[tuple(b) for b in sc.get("bands", DEFAULT_BANDS)],
            )
            self.card.max_points_ = sc.get("max_points", {})

    # ── cached construction ─────────────────────────────────────────────────
    @classmethod
    def get(cls, model: str, version: str | None = None) -> "DecisionEngine | None":
        """Return a cached engine, or None if the artifact cannot be loaded.

        Returning None rather than raising is deliberate: a missing or corrupt
        artifact must degrade the product to its existing scorecard, not take
        down the endpoint that happened to ask for a score first.

        **`version=None` means `settings.ML_MODEL_VERSION`**, which is the
        rollout gate — not the literal string "champion".

        2026-09-09. This parameter defaulted to `"champion"` and **every one of
        the six call sites in `services/ml_scoring_service.py` omitted it**, so
        `ML_MODEL_VERSION` was read in exactly one place: the `/manager/ml/health`
        endpoint, which reported it as `configured_version`. Pinning the setting
        to `1.0.0` to roll back therefore changed nothing except the health
        endpoint's claim about itself — it would have confirmed the rollback
        while 1.1.0 went on serving every score. A gate that cannot gate is
        worse than no gate, because it is believed.

        Resolved HERE rather than at the call sites so there is one place that
        decides, per the one-definition rule; an explicit `version=` still wins,
        which is what the training and comparison scripts need.
        """
        # Imported here, not at module scope, so `ml/pipeline` stays loadable by
        # the training scripts without pulling in the app's settings object.
        from app.core.config import settings

        version = version or settings.ML_MODEL_VERSION or "champion"
        key = (model, version)
        if key in cls._cache:
            return cls._cache[key]
        with cls._lock:
            if key in cls._cache:
                return cls._cache[key]
            try:
                cls._cache[key] = cls(model, version)
            except Exception as exc:
                logger.warning("ml.engine.load_failed model=%s version=%s error=%s",
                               model, version, exc)
                return None
            return cls._cache[key]

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    # ── what this PROCESS is actually serving ───────────────────────────────
    # 2026-09-09. The cache is keyed on the version string as ASKED FOR, so the
    # entry for ("recovery_risk", "champion") holds whichever artifact was
    # resolved the first time anybody asked. Promotion rewrites champion.txt and
    # this process keeps serving the old model until it reloads.
    #
    # That is not a bug to paper over — a scoring run that swapped models
    # halfway would be worse. It IS a state that has to be visible, because with
    # several API instances the fleet can be split across two champions and
    # every one of them would report itself healthy.

    @classmethod
    def serving_state(cls, model: str) -> dict:
        """Loaded version vs pointer version, for THIS process.

        `instance` identifies the process, because the answer is per-process and
        a single reply cannot speak for a fleet. A deployment running more than
        one API container has to ask each of them.
        """
        import os
        import socket

        from app.ml.pipeline import registry

        pointer = registry.pointer_version(model)
        loaded = sorted({
            eng.version for (m, _asked), eng in cls._cache.items() if m == model
        })
        # What a NEW request would get without touching the cache.
        configured = None
        try:
            from app.core.config import settings
            configured = settings.ML_MODEL_VERSION or "champion"
        except Exception:                                   # pragma: no cover
            pass
        expected = pointer if configured in (None, "champion") else configured
        return {
            "model": model,
            "instance": f"{socket.gethostname()}:{os.getpid()}",
            "configured_version": configured,
            "pointer_version": pointer,
            "expected_version": expected,
            "loaded_versions": loaded,
            # False only when something IS loaded and it is not what the
            # pointer/config says. Nothing loaded yet is not a mismatch.
            "serving_matches_pointer": (
                not loaded or (expected is not None and loaded == [expected])),
        }

    @classmethod
    def reload(cls, model: str) -> dict:
        """Drop this process's cached engines for one model and re-resolve.

        Called immediately after a promotion so the promoting process is not the
        one serving a stale model. Other instances pick it up when they reload
        or restart; `serving_state` is how that is checked rather than assumed.
        """
        with cls._lock:
            for key in [k for k in cls._cache if k[0] == model]:
                cls._cache.pop(key, None)
        cls.get(model)
        return cls.serving_state(model)

    # ── scoring ─────────────────────────────────────────────────────────────
    def _coverage(self, features: dict[str, Any]) -> tuple[float, list[str]]:
        """What share of the model's OWN inputs were supplied, and which were not.

        One definition, used by both the single-row and the batch path — the
        batch path used to have no coverage notion at all, which is how the
        floor below came to be unenforced on the only path the product serves.
        """
        def _null(v) -> bool:
            return v is None or (isinstance(v, float) and np.isnan(v))

        # A feature is MISSING when its key is absent from the vector (the
        # adapter did not produce it — a break), or when it is null and the
        # spec does not declare it abstaining. A declared-abstaining feature
        # that is present-but-null is the Missing bin doing its job, not a
        # missing input; counting it against the floor declined 3,202 honest
        # rows on the first 2.0.0 run.
        missing = [f for f in self.selected
                   if f not in features
                   or (f not in self.abstaining and _null(features.get(f)))]
        return 1.0 - (len(missing) / max(len(self.selected), 1)), missing

    def _row(self, features: dict[str, Any]) -> dict[str, Any]:
        return {f: features.get(f, np.nan) for f in self.expected_features}

    def _frame(self, features: dict[str, Any]) -> tuple[pd.DataFrame, float, list[str]]:
        """One-row frame with every expected column, NaN where unsupplied."""
        coverage, missing = self._coverage(features)
        return pd.DataFrame([self._row(features)]), coverage, missing

    def score(self, features: dict[str, Any]) -> ScoreResult:
        X, coverage, missing = self._frame(features)

        if coverage < MIN_FEATURE_COVERAGE:
            return ScoreResult(
                probability=None, points=None, band=None,
                model=self.model, version=self.version, is_modelled=False,
                feature_coverage=round(coverage, 3), missing_features=missing,
                fallback_reason=(
                    f"only {coverage:.0%} of the model's {len(self.selected)} "
                    f"features were supplied (floor {MIN_FEATURE_COVERAGE:.0%}); "
                    f"missing: {', '.join(missing)}"),
            )

        try:
            prob = float(self.pipeline.predict_proba(X)[0, 1])
            prob = self._calibrate_one(prob, features)
        except Exception as exc:
            logger.warning("ml.engine.score_failed model=%s error=%s", self.model, exc)
            return ScoreResult(
                probability=None, points=None, band=None,
                model=self.model, version=self.version, is_modelled=False,
                feature_coverage=round(coverage, 3), missing_features=missing,
                fallback_reason=f"scoring raised {type(exc).__name__}: {exc}",
            )

        points, band, reasons, fpoints, contrib = None, None, [], {}, {}
        if self.gam is not None:
            try:
                points, band, reasons, contrib = self._explain_gam(X, np.array([prob]))[0]
            except Exception as exc:                        # pragma: no cover
                logger.warning("ml.engine.explain_failed model=%s error=%s", self.model, exc)
        elif self.card is not None:
            try:
                # Step through the fitted transformers explicitly rather than
                # slicing. `pipeline[:-1]` builds a NEW Pipeline that sklearn
                # regards as unfitted — it warns today and raises from 1.8 —
                # even though every step inside it is fitted. Walking the steps
                # uses the same objects with no reconstruction.
                woe = X
                for _, step in self.pipeline.steps[:-1]:
                    woe = step.transform(woe)
                out = self.card.explain(woe, prob)
                points, band = out.points, out.band
                reasons, fpoints = out.reason_codes, out.feature_points
            except Exception as exc:                        # pragma: no cover
                logger.warning("ml.engine.explain_failed model=%s error=%s",
                               self.model, exc)

        return ScoreResult(
            probability=round(prob, 6), points=points, band=band,
            model=self.model, version=self.version, is_modelled=True,
            reason_codes=reasons, feature_points=fpoints,
            feature_coverage=round(coverage, 3), missing_features=missing,
            versions=dict(self.versions), contributions=contrib,
        )

    def _explain_gam(self, X: pd.DataFrame, prob_cal: np.ndarray) -> list[tuple]:
        """(points, band, reason_codes, contributions) per row.

        The band is cut on the CALIBRATED probability — the number the
        allocator consumes; points are the scorecard scaling of the raw
        logit, for display; the reason codes are the largest centred
        contributions, and `contributions` carries every one of them plus the
        intercept and the logit (rounded to 6 decimals, in logits) so the
        stored row reconciles: intercept + sum(contributions) == logit.
        """
        contrib = self.gam.contributions(X)
        lg = contrib["logit"].to_numpy()
        pts = points_from_logit(lg, **self._points_scale)
        records = contrib.to_dict("records")
        raw_rows = X[self.gam.features].to_dict("records")
        bands = self.bands.assign(prob_cal) if self.bands is not None else [None] * len(X)
        out = []
        for i, (row, values) in enumerate(zip(records, raw_rows)):
            values = {k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in values.items()}
            reasons = reason_codes(pd.Series(row), values, self.gam.pairs)
            fcontrib = {k: round(float(v), 6) for k, v in row.items()}
            out.append((int(pts[i]), str(bands[i]) if bands[i] is not None else None, reasons, fcontrib))
        return out

    def _calibrate_one(self, prob: float, features: dict[str, Any]) -> float:
        if self.calibrator is None:
            return prob
        seg = features.get(self.calibrator.segment_col)
        return self.calibrator.transform_one(prob, seg)

    def score_batch(self, rows: list[dict[str, Any]]) -> list[float | None]:
        """Probabilities for many rows in one call.

        The nightly scorer runs over the whole book; a per-row predict_proba on
        a sklearn Pipeline is dominated by call overhead, and this repo already
        learned that lesson once — RepaymentService._load bulk-loads because
        fifteen minutes is the entire margin between ingest and allocation.

        Thin wrapper over `score_batch_detailed` since 2026-09-09, so there is
        ONE batch probability in the codebase rather than two that can drift.
        """
        return [r.probability for r in self.score_batch_detailed(rows)]

    def score_batch_detailed(self, rows: list[dict[str, Any]]) -> list[ScoreResult]:
        """The batch path, with the same guards and the same detail as `score`.

        2026-09-09 — WHY THIS EXISTS. `score_batch` returned bare probabilities,
        and the production planner is its only caller, so two properties of the
        single-row path were silently absent from every score the product
        actually served:

          * THE COVERAGE FLOOR WAS NOT APPLIED. `score()` declines below
            MIN_FEATURE_COVERAGE because a scorecard with two of four inputs is
            the intercept plus noise. `score_batch` scored it anyway and handed
            the allocator a plausible-looking number. Measured on the live book
            the day this was found: min coverage 1.000 across 11,817 rows, so
            nothing had yet been mis-served — the guard was simply not there to
            catch the day it stops being 1.000. Fault #2 of 2026-09-08 is
            precisely that day: `ptp_kept_ratio` vanished, coverage fell to 0.75,
            and the only thing between that and a scored-on-nothing borrower is
            this floor.
          * POINTS, BAND AND REASON CODES WERE NEVER COMPUTED. `ModelPrediction`
            has columns for all three and `score_cases_and_log` assigns them —
            from a result that never carried them. 11,817 served rows: points
            NULL, band NULL, reason_codes []. So "why did this borrower score
            badly" was unanswerable for exactly the scores the product served,
            while the unused single-row path recorded it in full.

        THE PROBABILITY IS UNCHANGED, by construction rather than by assertion:
        it is the same predict_proba and the same calibrator call the previous
        `score_batch` made, and `score_batch` now delegates here rather than
        duplicating it. The explanation is derived from the SAME transformed
        frame, so points and probability cannot describe different rows.
        """
        if not rows:
            return []

        # ONE frame for the whole batch, not 900 concatenated one-row frames:
        # the call overhead this method exists to avoid is not worth trading for
        # a tidier loop.
        cov_miss = [self._coverage(r) for r in rows]
        X = pd.DataFrame([self._row(r) for r in rows])

        def _declined(i: int, reason: str) -> ScoreResult:
            cov, miss = cov_miss[i]
            return ScoreResult(probability=None, points=None, band=None,
                               model=self.model, version=self.version,
                               is_modelled=False, feature_coverage=round(cov, 3),
                               missing_features=miss, fallback_reason=reason)

        try:
            raw = self.pipeline.predict_proba(X)[:, 1]
            if self.calibrator is not None:
                seg = pd.to_numeric(
                    X.get(self.calibrator.segment_col), errors="coerce").to_numpy()
                raw = self.calibrator.transform(raw, seg)
        except Exception as exc:
            logger.warning("ml.engine.batch_failed model=%s error=%s", self.model, exc)
            return [_declined(i, f"batch scoring raised {type(exc).__name__}: {exc}")
                    for i in range(len(rows))]

        woe = None
        gam_ex: list[tuple] | None = None
        if self.gam is not None:
            try:
                gam_ex = self._explain_gam(X, raw)
            except Exception as exc:                        # pragma: no cover
                logger.warning("ml.engine.batch_explain_failed model=%s error=%s",
                               self.model, exc)
        elif self.card is not None:
            try:
                woe = X
                for _, step in self.pipeline.steps[:-1]:
                    woe = step.transform(woe)
            except Exception as exc:                        # pragma: no cover
                logger.warning("ml.engine.batch_explain_failed model=%s error=%s",
                               self.model, exc)
                woe = None

        out: list[ScoreResult] = []
        for i, (cov, miss) in enumerate(cov_miss):
            if cov < MIN_FEATURE_COVERAGE:
                out.append(_declined(i, (
                    f"only {cov:.0%} of the model's {len(self.selected)} features "
                    f"were supplied (floor {MIN_FEATURE_COVERAGE:.0%}); "
                    f"missing: {', '.join(miss)}")))
                continue
            prob = round(float(raw[i]), 6)
            points = band = None
            reasons: list[dict] = []
            fpoints: dict[str, int] = {}
            contrib: dict = {}
            if gam_ex is not None:
                points, band, reasons, contrib = gam_ex[i]
            elif woe is not None:
                try:
                    ex = self.card.explain(woe.iloc[[i]], prob)
                    points, band = ex.points, ex.band
                    reasons, fpoints = ex.reason_codes, ex.feature_points
                except Exception as exc:                    # pragma: no cover
                    logger.warning("ml.engine.explain_failed model=%s error=%s",
                                   self.model, exc)
            out.append(ScoreResult(
                probability=prob, points=points, band=band, model=self.model,
                version=self.version, is_modelled=True, reason_codes=reasons,
                feature_points=fpoints, feature_coverage=round(cov, 3),
                missing_features=miss, versions=dict(self.versions),
                contributions=contrib))
        return out

    # ── introspection ───────────────────────────────────────────────────────
    def health(self) -> dict:
        m = self.metadata.get("metrics", {}).get("oot", {})
        return {
            "model": self.model,
            "version": self.version,
            "loaded": True,
            "is_modelled": True,
            "champion_kind": self.metadata.get("champion_kind"),
            "trained_at": self.metadata.get("saved_at"),
            "gate_summary": self.metadata.get("gate_summary"),
            "features": self.selected,
            "n_features": len(self.selected),
            "metrics_oot": {"gini": m.get("gini"), "ks": m.get("ks"),
                            "bad_rate": m.get("bad_rate"),
                            "top_decile_lift": m.get("top_decile_lift")},
            "model_type": self.model_type,
            "versions": self.versions,
            "calibrated": self.calibrator is not None,
            "calibration": self.calibration_meta,
            "synthetic": bool(self.metadata.get("SYNTHETIC_WARNING")),
            "SYNTHETIC_WARNING": self.metadata.get("SYNTHETIC_WARNING"),
            "artifact_sha256": self.metadata.get("artifact_sha256"),
            "library_versions": self.metadata.get("library_versions"),
        }


def health_all() -> dict:
    """Status of every registered model. Mirrors GET /manager/ai/health's shape.

    Reports models that FAILED to load as well as those that did, because "the
    ML health endpoint returned 200 and listed nothing" is indistinguishable
    from "there are no models" unless it says so.
    """
    from app.ml.pipeline.config import REGISTRY

    out, loaded = [], 0
    for name in REGISTRY:
        eng = DecisionEngine.get(name)
        if eng is None:
            out.append({"model": name, "loaded": False, "is_modelled": False,
                        "error": "no champion artifact, or it failed to load"})
        else:
            out.append(eng.health())
            loaded += 1
    return {"models": out, "n_registered": len(REGISTRY), "n_loaded": loaded}
