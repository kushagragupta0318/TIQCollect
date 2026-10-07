"""What scores this product, shown to a bank's model-risk reader (GET /bank/models,
GET /bank/loans/{id}/explanation).

The six scoring layers ship and log every served score to ml.model_predictions,
with its version, band, coverage and reason codes. Nothing served them. This
module is the read side: facts from the artifact and the prediction rows, never
restated figures, and the model's limits said by the vendor before anyone asks.

Polarity: `probability` on a prediction row is P(no material payment in the next
cycle) — the risk event (ml/pipeline/config.py, TARGET POLARITY). Everything
here returns both it and 1 − p, labelled, so no screen can show one as the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.ml.empirical_bayes import MULTIPLIER_BOUNDS as EB_BOUNDS
from app.ml.pipeline.engine import MIN_FEATURE_COVERAGE
from app.ml.pipeline.monitor import MIN_MATURED_FOR_MONITORING, readiness, serving_version
from app.ml.pipeline.registry import resolve_version, version_dir
from app.ml.recovery_scorecard import RECOVERY_SCORECARD_VERSION
from app.ml.repayment_scorecard import SCORECARD_VERSION
from app.ml.visit_priority import SCORE_VERSION as VISIT_PRIORITY_VERSION
from app.models.loan import Loan
from app.models.model_prediction import ModelPrediction
from app.services.global_allocator import GlobalAllocator
from app.services.placement_read_service import apply_region_limit

TRAINED_MODEL = "recovery_risk"

# ADR 0008: the serving artifact rescored with the borrower-stance inputs at
# NONE, as production recorded them (tiqcollect-ce, "ML-1 C", 2026-09-24). Keyed
# by version: the figures describe that artifact and no other.
LIVE_EQUIVALENT = {
    "2.2.0": {"gini": 0.4796, "ks": 35.74, "measured_on": "2026-09-24",
              "basis": "the borrower-stance inputs held at 'not recorded', as the product recorded them"},
}

# ML-1 option A merged (142311f): the visit and call forms record the stance.
STANCE_CAPTURE_SINCE = date(2026, 9, 28)
STANCE_FEATURE = "latest_disposition"
# Both inputs the live-equivalent measurement held at NONE (ADR 0008).
STANCE_FEATURES = (STANCE_FEATURE, "disposition_recency_class")
_NOT_RECORDED = {None, "", "NONE"}

# The six layers, as a set. Deliberately separate: no layer's output is another's
# input (ADR 0005 §2; the 2026-09-11 ablation measured every such arm at -0.0001
# to -0.0006 OOT Gini). `kind` is what the layer IS, so nothing hand-weighted
# can be read as a trained model.
_LAYERS: tuple[dict, ...] = (
    {"key": "recovery_risk", "name": "Recovery risk", "kind": "TRAINED_MODEL", "is_modelled": True,
     "decides": "The chance that an account makes no material payment in the next cycle.",
     "method": "15-feature additive model (GAM) with a calibrator, trained and validated out of time.",
     "acts_on": "Nightly allocation: which agent gets which case (ADR 0002), and the bank's placement engine.",
     "evidence": "Out-of-time Gini and KS, calibration, stability and reason codes that reconcile to the score."},
    {"key": "repayment_scorecard", "name": "Repayment likelihood", "kind": "SCORECARD", "is_modelled": False,
     "decides": "How likely the borrower is to pay, shown to the agent and manager on a case.",
     "method": "Hand-weighted scorecard; a factor with no evidence abstains instead of scoring zero.",
     "acts_on": "Case detail for agents and managers. Decision support only; never suppresses a visit.",
     "evidence": "None claimed. A scorecard has no Gini because it is not a model (ADR 0005)."},
    {"key": "recovery_scorecard", "name": "Recovery potential", "kind": "SCORECARD", "is_modelled": False,
     "decides": "Expected recovery rate over 30, 60 and 90 days.",
     "method": "Hand-weighted scorecard, versioned like the repayment scorecard.",
     "acts_on": "Snapshots read by manager analytics.",
     "evidence": "None claimed; a hand-weighted rule, not a trained model."},
    {"key": "visit_priority", "name": "Visit priority", "kind": "SCORECARD", "is_modelled": False,
     "decides": "How urgent each case is for tomorrow's plan (promises falling due, the NPA line, value).",
     "method": "Hand-weighted priority score.",
     "acts_on": "Which cases the nightly plan takes first, and up to {priority_uplift} extra weight for a case in the assignment.",
     "evidence": "None claimed; a hand-weighted rule."},
    {"key": "agent_competency", "name": "Agent competency", "kind": "SHRINKAGE", "is_modelled": False,
     "decides": "How strong each agent is on each kind of case, without over-reading a small record.",
     "method": "Closed-form empirical-Bayes shrinkage toward the team's average.",
     "acts_on": "A bounded multiplier ({eb_low} to {eb_high}) in the nightly allocation.",
     "evidence": "A formula, not a fit: no weights to validate."},
    {"key": "allocator", "name": "Case-to-agent allocation", "kind": "OPTIMISER", "is_modelled": False,
     "decides": "The day's assignment of cases to agents.",
     "method": "Hungarian assignment over a cost matrix combining the layers above with distance and capacity.",
     "acts_on": "Every agent's planned day, recorded with the reason each case went where it did.",
     "evidence": "Optimal for its objective by construction; the weights are fixed in code (ADR 0002), not learned."},
)

_LAYER_VERSIONS = {
    "repayment_scorecard": SCORECARD_VERSION,
    "recovery_scorecard": RECOVERY_SCORECARD_VERSION,
    "visit_priority": VISIT_PRIORITY_VERSION,
}


def _read_metadata(version: str) -> dict:
    import json
    try:
        return json.loads((version_dir(TRAINED_MODEL, version) / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _bank_id(ctx) -> str:
    # Fail closed: this is bank data, and the bank comes from the caller's row.
    if getattr(ctx, "scope", None) != "BANK" or not getattr(ctx, "bank_id", None):
        raise AppException(403, ErrorCode.FORBIDDEN, "A bank user is required")
    return ctx.bank_id


def _stance_recorded(features: dict | None) -> bool | None:
    if not isinstance(features, dict) or STANCE_FEATURE not in features:
        return None
    return features.get(STANCE_FEATURE) not in _NOT_RECORDED


@dataclass
class _LatestDay:
    on: date | None
    scored: int
    declined: int
    with_stance: int
    #: True when stance coverage was measured over a sample, not the whole day.
    sampled: bool = False
    sample_size: int = 0


# The stance lives inside the prediction's `features` JSON, which no portable
# index reaches, so that share is counted in Python. Bounded: a scoring day on a
# large book is unbounded, and nothing unbounded runs inside a request. The
# counts themselves are exact — only the share is sampled, and it says so.
STANCE_SAMPLE_LIMIT = 5_000


def _latest_scoring_day(db: Session, bank_id: str, version: str | None) -> _LatestDay:
    """This bank's newest scoring day on the serving version: accounts scored,
    accounts declined, and the share carrying a recorded stance.

    One row per account: a re-plan re-scores the whole pool, so the same account
    can hold several rows for one day and only its newest counts."""
    if not version:
        return _LatestDay(None, 0, 0, 0)
    same = (ModelPrediction.bank_id == bank_id, ModelPrediction.model_name == TRAINED_MODEL,
            ModelPrediction.model_version == version)
    last = (db.query(func.max(ModelPrediction.as_of_date)).filter(*same).scalar())
    if last is None:
        return _LatestDay(None, 0, 0, 0)

    on_day = (*same, ModelPrediction.as_of_date == last)
    newest = (db.query(ModelPrediction.entity_id.label("entity_id"),
                       func.max(ModelPrediction.scored_at).label("scored_at"))
              .filter(*on_day).group_by(ModelPrediction.entity_id).subquery())
    rows = (db.query(ModelPrediction.is_modelled, func.count())
            .join(newest, and_(ModelPrediction.entity_id == newest.c.entity_id,
                               ModelPrediction.scored_at == newest.c.scored_at))
            .filter(*on_day).group_by(ModelPrediction.is_modelled).all())
    scored = sum(n for modelled, n in rows if modelled)
    declined = sum(n for modelled, n in rows if not modelled)

    features = (db.query(ModelPrediction.features)
                .join(newest, and_(ModelPrediction.entity_id == newest.c.entity_id,
                                   ModelPrediction.scored_at == newest.c.scored_at))
                .filter(*on_day, ModelPrediction.is_modelled.is_(True))
                .limit(STANCE_SAMPLE_LIMIT).all())
    with_stance = sum(1 for (f,) in features if _stance_recorded(f))
    return _LatestDay(on=last, scored=scored, declined=declined, with_stance=with_stance,
                      sampled=scored > len(features), sample_size=len(features))


def _first_scored(db: Session, bank_id: str, version: str | None) -> date | None:
    if not version:
        return None
    row = (db.query(ModelPrediction.as_of_date)
           .filter(ModelPrediction.bank_id == bank_id, ModelPrediction.model_name == TRAINED_MODEL,
                   ModelPrediction.model_version == version)
           .order_by(ModelPrediction.as_of_date).first())
    return row[0] if row else None


def configured_version() -> str | None:
    """What champion.txt names, whether or not the artifact loads."""
    try:
        return resolve_version(TRAINED_MODEL)
    except (FileNotFoundError, OSError):
        return None


def models_overview(db: Session, ctx) -> dict:
    bank_id = _bank_id(ctx)
    # Two different questions: which version is pointed at, and whether it is
    # actually serving. A deployment whose artifact will not load still has a
    # champion, and saying "unknown" would hide an operational fault.
    configured = configured_version()
    serving = serving_version(TRAINED_MODEL)
    version = serving or configured
    meta = _read_metadata(version) if version else {}
    labels = (meta.get("reason_codes") or {}).get("labels") or {}
    day = _latest_scoring_day(db, bank_id, version)

    try:
        gate = readiness(db, TRAINED_MODEL, version=version)
        # The gate counts the model's whole book; a bank sees its state, not
        # other tenants' volume.
        monitoring_status = "ready" if gate.ready else "not_ready"
    except Exception:  # noqa: BLE001 — a diagnostic must not take the page down
        monitoring_status = "unavailable"
    first = _first_scored(db, bank_id, version)

    from app.models.model_candidate import ModelCandidate
    cand = (db.query(ModelCandidate).filter(ModelCandidate.model_name == TRAINED_MODEL)
            .order_by(ModelCandidate.created_at.desc()).first())

    layers = []
    for layer in _LAYERS:
        v = version if layer["key"] == TRAINED_MODEL else _LAYER_VERSIONS.get(layer["key"])
        # Figures in the copy come from the code that applies them, never restated.
        acts_on = layer["acts_on"].format(priority_uplift=f"{GlobalAllocator.PRIORITY_UPLIFT:.0%}",
                                          eb_low=EB_BOUNDS[0], eb_high=EB_BOUNDS[1])
        layers.append({**layer, "acts_on": acts_on, "version": v})

    return {
        "synthetic_warning": meta.get("SYNTHETIC_WARNING")
        or "No artifact metadata was readable; treat every figure as unverified.",
        "scoring_enabled": bool(settings.ML_SCORING_ENABLED),
        "layers": layers,
        "recovery_risk": {
            "serving_version": serving,
            "configured_version": configured,
            "artifact_loaded": serving is not None,
            "artifact_sha256": meta.get("artifact_sha256"),
            "method": "Additive model (GAM): boosted single-feature shape functions, one declared interaction, "
                      "monotone where a direction is declared, calibrated on validation months.",
            "features": [{"code": f, "label": labels.get(f, f)} for f in meta.get("selected_features") or []],
            "stored_probability_means": "the chance of NO material payment in the next cycle (higher = riskier)",
            "live_equivalent": LIVE_EQUIVALENT.get(version or ""),
            "stance": {
                "feature": STANCE_FEATURE,
                "feature_label": labels.get(STANCE_FEATURE, STANCE_FEATURE),
                "related_features": list(STANCE_FEATURES),
                "capture_since": STANCE_CAPTURE_SINCE.isoformat(),
                "latest_scoring_day": day.on.isoformat() if day.on else None,
                "accounts_scored": day.scored,
                "accounts_with_stance": day.with_stance,
                # Over the sample when the day was too large to read whole.
                "share": round(day.with_stance / day.sample_size, 4) if day.sample_size else None,
                "share_sampled": day.sampled,
                "sample_size": day.sample_size if day.sampled else None,
            },
            "abstention": {"coverage_floor": MIN_FEATURE_COVERAGE, "latest_day_declined": day.declined,
                           "latest_day_scored": day.scored},
            "monitoring": {
                "status": monitoring_status,
                "required_matured": MIN_MATURED_FOR_MONITORING,
                "horizon_days": settings.REPAYMENT_OUTCOME_HORIZON_DAYS,
                "first_outcomes_mature_from": (
                    (first + timedelta(days=settings.REPAYMENT_OUTCOME_HORIZON_DAYS)).isoformat() if first else None),
            },
            "governance": {
                "auto_retrain_enabled": bool(settings.ML_AUTO_RETRAIN_ENABLED),
                "latest_candidate": None if cand is None else {
                    "version": cand.candidate_version,
                    "state": getattr(cand.state, "value", str(cand.state)),
                    "created_at": cand.created_at.isoformat() if getattr(cand, "created_at", None) else None,
                },
            },
            "scoring_versions": meta.get("versions") or {},
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _reason_rows(pred: ModelPrediction, labels: dict) -> list[dict]:
    """Both stored formats, normalised. 2.x rows carry a signed contribution
    centred on the book's average account (log-odds); 1.1.0 rows carry
    scorecard points lost, which only ever push risk up."""
    out = []
    features = pred.features or {}
    for i, r in enumerate(pred.reason_codes or []):
        if not isinstance(r, dict) or not r.get("feature"):
            continue
        feature = str(r["feature"])
        parts = [p.strip() for p in feature.split(" x ")]
        label = (" together with ".join(labels.get(p, p) for p in parts) if len(parts) == 2
                 else labels.get(feature, feature))
        if "contribution" in r:
            contribution = float(r["contribution"])
            out.append({
                "rank": int(r.get("rank") or i + 1), "feature": feature, "label": label,
                "kind": r.get("kind") or "feature", "value": None if r.get("value") is None else str(r["value"]),
                "direction": "increases_risk" if contribution > 0 else "decreases_risk",
                "magnitude": abs(contribution), "unit": "log_odds", "signed": contribution,
            })
        elif "points_lost" in r:
            lost = float(r["points_lost"])
            value = features.get(feature)
            out.append({
                "rank": i + 1, "feature": feature, "label": labels.get(feature, feature), "kind": "feature",
                "value": None if value is None else str(value),
                "direction": "increases_risk", "magnitude": abs(lost), "unit": "points_lost", "signed": lost,
            })
    return out


def loan_explanation(db: Session, ctx, loan_id: str, *, region_limit=None) -> dict:
    bank_id = _bank_id(ctx)
    q = db.query(Loan.id).filter(Loan.id == loan_id, Loan.bank_id == bank_id)
    if apply_region_limit(q, region_limit).first() is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")

    # bank_id as well as loan_id: the ORM already refuses a prediction whose bank
    # disagrees with its loan's (models/tenancy_listener), so this only bites on a
    # row written around the ORM — a raw statement, or a restored dump.
    pred = (db.query(ModelPrediction)
            .filter(ModelPrediction.bank_id == bank_id, ModelPrediction.loan_id == loan_id,
                    ModelPrediction.model_name == TRAINED_MODEL)
            .order_by(ModelPrediction.scored_at.desc()).first())
    serving = serving_version(TRAINED_MODEL)
    if pred is None:
        return {"loan_id": loan_id, "serving_version": serving, "prediction": None}

    meta = _read_metadata(pred.model_version)
    labels = (meta.get("reason_codes") or {}).get("labels") or {}
    p = pred.probability
    contributions = pred.contributions if isinstance(pred.contributions, dict) else None
    return {
        "loan_id": loan_id,
        "serving_version": serving,
        "prediction": {
            "model_name": pred.model_name,
            "model_version": pred.model_version,
            "is_serving_version": pred.model_version == serving,
            "artifact_sha256": pred.artifact_sha256,
            "scored_at": pred.scored_at.isoformat() if pred.scored_at else None,
            "as_of_date": pred.as_of_date.isoformat() if pred.as_of_date else None,
            "is_modelled": bool(pred.is_modelled),
            "fallback_reason": pred.fallback_reason,
            "p_no_payment": p,
            "p_payment": None if p is None else round(1.0 - p, 6),
            "band": pred.band,
            "points": pred.points,
            "feature_coverage": pred.feature_coverage,
            "coverage_floor": MIN_FEATURE_COVERAGE,
            "n_features": len(meta.get("selected_features") or []) or None,
            "stance_recorded": _stance_recorded(pred.features),
            "reasons": _reason_rows(pred, labels),
            "contributions": contributions,
            "scoring_versions": pred.scoring_versions or {},
            "synthetic_warning": meta.get("SYNTHETIC_WARNING")
            or f"UNVERIFIED: no readable metadata for {pred.model_name} {pred.model_version}.",
        },
    }
