"""Response shapes for the bank's model pages (endpoints/bank_models.py,
services/bank/model_showcase.py)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ScoringLayer(BaseModel):
    key: str
    name: str
    kind: Literal["TRAINED_MODEL", "SCORECARD", "SHRINKAGE", "OPTIMISER"]
    is_modelled: bool
    version: Optional[str] = None
    decides: str
    method: str
    acts_on: str
    evidence: str


class FeatureLabel(BaseModel):
    code: str
    label: str


class ArtifactMetrics(BaseModel):
    gini: Optional[float] = None
    ks: Optional[float] = None
    auc: Optional[float] = None
    brier: Optional[float] = None
    n: Optional[int] = None


class LiveEquivalent(BaseModel):
    gini: float
    ks: float
    measured_on: str
    basis: str


class StanceCoverage(BaseModel):
    feature: str
    feature_label: str
    related_features: list[str]
    capture_since: str
    latest_scoring_day: Optional[str] = None
    accounts_scored: int
    accounts_with_stance: int
    share: Optional[float] = None


class Abstention(BaseModel):
    coverage_floor: float
    latest_day_declined: int
    latest_day_scored: int


class BandRow(BaseModel):
    band: Optional[str] = None
    oot_n: Optional[int] = None
    oot_bad_rate: Optional[float] = None


class Monitoring(BaseModel):
    status: Literal["ready", "not_ready", "unavailable"]
    required_matured: int
    horizon_days: int
    first_outcomes_mature_from: Optional[str] = None


class Candidate(BaseModel):
    version: Optional[str] = None
    state: str
    created_at: Optional[str] = None


class Governance(BaseModel):
    auto_retrain_enabled: bool
    latest_candidate: Optional[Candidate] = None


class RecoveryRiskCard(BaseModel):
    serving_version: Optional[str] = None
    artifact_sha256: Optional[str] = None
    method: str
    features: list[FeatureLabel]
    stored_probability_means: str
    artifact_metrics: ArtifactMetrics
    live_equivalent: Optional[LiveEquivalent] = None
    stance: StanceCoverage
    abstention: Abstention
    bands: list[BandRow]
    monitoring: Monitoring
    governance: Governance
    scoring_versions: dict[str, str]


class ModelsOverview(BaseModel):
    synthetic_warning: str
    scoring_enabled: bool
    layers: list[ScoringLayer]
    recovery_risk: RecoveryRiskCard
    checked_at: str


class ReasonRow(BaseModel):
    rank: int
    feature: str
    label: str
    kind: str
    value: Optional[str] = None
    direction: Literal["increases_risk", "decreases_risk"]
    magnitude: float
    unit: Literal["log_odds", "points_lost"]
    signed: float


class Prediction(BaseModel):
    model_name: str
    model_version: str
    is_serving_version: bool
    artifact_sha256: Optional[str] = None
    scored_at: Optional[str] = None
    as_of_date: Optional[str] = None
    is_modelled: bool
    fallback_reason: Optional[str] = None
    p_no_payment: Optional[float] = None
    p_payment: Optional[float] = None
    band: Optional[str] = None
    points: Optional[int] = None
    feature_coverage: Optional[float] = None
    coverage_floor: float
    n_features: Optional[int] = None
    stance_recorded: Optional[bool] = None
    reasons: list[ReasonRow]
    contributions: Optional[dict[str, float]] = None
    scoring_versions: dict[str, str]
    synthetic_warning: str


class LoanExplanation(BaseModel):
    loan_id: str
    serving_version: Optional[str] = None
    prediction: Optional[Prediction] = None
