// The bank's model pages: GET /bank/models and GET /bank/loans/{id}/explanation
// (backend/app/api/v1/endpoints/bank_models.py, schemas/bank_models.py).
//
// POLARITY: a prediction's `p_no_payment` is what the model stores — the chance of
// NO material payment next cycle (higher = riskier). `p_payment` is 1 − that.
// Both come from the server, labelled; nothing here derives one from the other.
import api from "./axios";

export type LayerKind = "TRAINED_MODEL" | "SCORECARD" | "SHRINKAGE" | "OPTIMISER";

export interface ScoringLayer {
  key: string;
  name: string;
  kind: LayerKind;
  is_modelled: boolean;
  version: string | null;
  decides: string;
  method: string;
  acts_on: string;
  evidence: string;
}

export interface RecoveryRiskCard {
  /** null when the artifact could not be loaded on this deployment. */
  serving_version: string | null;
  /** What champion.txt names, whether or not it loads. */
  configured_version: string | null;
  artifact_loaded: boolean;
  artifact_sha256: string | null;
  method: string;
  features: { code: string; label: string }[];
  stored_probability_means: string;
  artifact_metrics: { gini: number | null; ks: number | null; auc: number | null; brier: number | null; n: number | null };
  live_equivalent: { gini: number; ks: number; measured_on: string; basis: string } | null;
  stance: {
    feature: string;
    feature_label: string;
    /** Every model input that depends on the recorded stance. */
    related_features: string[];
    capture_since: string;
    latest_scoring_day: string | null;
    accounts_scored: number;
    accounts_with_stance: number;
    /** Over the sample when the scoring day was too large to read whole. */
    share: number | null;
    share_sampled: boolean;
    sample_size: number | null;
  };
  abstention: { coverage_floor: number; latest_day_declined: number; latest_day_scored: number };
  bands: { band: string | null; oot_n: number | null; oot_bad_rate: number | null }[];
  monitoring: {
    status: "ready" | "not_ready" | "unavailable";
    required_matured: number;
    horizon_days: number;
    first_outcomes_mature_from: string | null;
  };
  governance: {
    auto_retrain_enabled: boolean;
    latest_candidate: { version: string | null; state: string; created_at: string | null } | null;
  };
  scoring_versions: Record<string, string>;
}

export interface ModelsOverview {
  synthetic_warning: string;
  scoring_enabled: boolean;
  layers: ScoringLayer[];
  recovery_risk: RecoveryRiskCard;
  checked_at: string;
}

export interface ReasonRow {
  rank: number;
  feature: string;
  label: string;
  kind: string;
  value: string | null;
  direction: "increases_risk" | "decreases_risk";
  magnitude: number;
  unit: "log_odds" | "points_lost";
  signed: number;
}

export interface Prediction {
  model_name: string;
  model_version: string;
  is_serving_version: boolean;
  artifact_sha256: string | null;
  scored_at: string | null;
  as_of_date: string | null;
  is_modelled: boolean;
  fallback_reason: string | null;
  p_no_payment: number | null;
  p_payment: number | null;
  band: string | null;
  points: number | null;
  feature_coverage: number | null;
  coverage_floor: number;
  n_features: number | null;
  stance_recorded: boolean | null;
  reasons: ReasonRow[];
  contributions: Record<string, number> | null;
  scoring_versions: Record<string, string>;
  synthetic_warning: string;
}

export interface LoanExplanation {
  loan_id: string;
  serving_version: string | null;
  prediction: Prediction | null;
}

export async function getModelsOverview(): Promise<ModelsOverview> {
  const { data } = await api.get<ModelsOverview>("/bank/models");
  return data;
}

export async function getLoanExplanation(loanId: string): Promise<LoanExplanation> {
  const { data } = await api.get<LoanExplanation>(`/bank/loans/${loanId}/explanation`);
  return data;
}
