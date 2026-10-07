// The MLOps console's types, fetchers and the one presentation rule that
// matters. Beside the page rather than in it, as overviewModel /
// analyticsModel / auditModel are.
//
// THE RULE: the headline performance figure is the LIVE-EQUIVALENT one, never
// the artifact's own. ADR 0008 / ML-1 — the artifact was measured with a
// borrower-stance feature the product never records, so its OOT figures
// describe a model that cannot exist in production. Quoting them as "the
// model's performance" is the single most misleading thing this page could
// do, and it is exactly the number somebody would act on.
//
// Both numbers come from the API (services/bank/model_showcase.LIVE_EQUIVALENT
// holds the live-equivalent measurement per version); NOTHING here restates a
// figure. If the API stops sending live_equivalent, this page says it does not
// have one rather than quietly falling back to the flattering number.
import api from "@/api/axios";

export interface Metrics {
  gini?: number | null;
  ks?: number | null;
  auc?: number | null;
}

export interface LiveEquivalent {
  gini?: number | null;
  ks?: number | null;
  measured_on?: string | null;
  basis?: string | null;
}

export interface ModelsOverview {
  synthetic_warning: string;
  scoring_enabled: boolean;
  checked_at: string;
  recovery_risk: {
    serving_version: string | null;
    configured_version: string | null;
    artifact_loaded: boolean;
    artifact_sha256: string | null;
    method: string;
    features: { code: string; label: string }[];
    artifact_metrics: Metrics;
    live_equivalent: LiveEquivalent | null;
    monitoring: {
      status?: string;
      matured?: number | null;
      required_matured?: number | null;
      horizon_days?: number | null;
      first_outcomes_mature_from?: string | null;
    };
    governance: {
      auto_retrain_enabled: boolean;
      latest_candidate: { version?: string | null; state: string; created_at?: string | null } | null;
    };
  };
}

export interface CandidateRow {
  candidate_id: string;
  model_name: string;
  candidate_version: string | null;
  incumbent_version: string | null;
  state: string;
  trigger_reasons: string[];
  cohort_rows?: number | null;
  created_at?: string | null;
}

//: A monitor reason, reduced to a fixed label.
//:
//: ml/pipeline/monitor.py builds these strings WITH THE ARTIFACT'S OWN
//: FIGURES in them -- "Gini has fallen 12% below its development value
//: (0.421 against 0.512)" prints the development Gini this page is forbidden
//: to show (owner, 2026-10-07). Rendering reasons raw put the number back on
//: the page through a side door, and the test missed it because the fixture
//: said ["drift"].
//:
//: So a reason is CLASSIFIED, never echoed. An unrecognised reason falls
//: through to a generic label rather than its own text: fail closed, because
//: the next reason somebody adds to monitor.py will also carry numbers and
//: nobody will remember this rule.
const REASON_LABELS: [RegExp, string][] = [
  [/^POOLED across model versions/i, "Pooled across model versions"],
  [/^Gini has fallen/i, "Discrimination (Gini) below its development value"],
  [/^KS has fallen/i, "Separation (KS) below its development value"],
  [/^Brier score has risen/i, "Calibration (Brier) worse than development"],
  [/^calibration is off/i, "Predicted rate drifting from observed"],
  [/^rank order is broken/i, "Rank order broken in the top deciles"],
];

export function reasonLabel(reason: string): string {
  for (const [re, label] of REASON_LABELS) if (re.test(reason.trim())) return label;
  return "Monitoring threshold breached";
}

/** The states a candidate moves through. PENDING_APPROVAL is where the
 *  two-person rule bites: approving is not promoting, and the promoter must be
 *  a different person (ml.approve / ml.promote are both second_person caps). */
export const ACTIONABLE_STATES = ["PENDING_APPROVAL", "APPROVED"] as const;

export function canApprove(state: string): boolean {
  return state === "PENDING_APPROVAL";
}

export function canPromote(state: string): boolean {
  return state === "APPROVED";
}

/** A Gini of 0.4796 reads better as 0.480 than as 0.47960000000000003. */
export function metric(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

export function stateTone(state: string): "ok" | "warn" | "danger" | "muted" {
  if (state === "PROMOTED" || state === "APPROVED") return "ok";
  if (state === "PENDING_APPROVAL") return "warn";
  if (state === "REJECTED" || state === "FAILED") return "danger";
  return "muted";
}

export async function getModelsOverview(): Promise<ModelsOverview> {
  const { data } = await api.get<ModelsOverview>("/bank/models");
  return data;
}

export async function getCandidates(limit = 25): Promise<{ candidates: CandidateRow[]; count: number }> {
  // The candidate list and the approve/reject/promote actions live on the
  // /manager/ml/* routes, which are gated by CAPABILITY (ml.read, ml.approve,
  // ml.promote) and not by a manager role — F12 re-gated them, so BANK_TECHOPS
  // reaches them. They are NOT re-implemented here: the two-person promotion
  // rule is a load-bearing control and a second copy of it is a second way to
  // get it wrong. The path is a historical wart (RESTRUCTURE-PLAN 2.4 splits
  // manager.py), not a scoping statement.
  const { data } = await api.get<{ candidates: CandidateRow[]; count: number }>(
    "/manager/ml/candidates", { params: { limit } },
  );
  return data;
}

export async function approveCandidate(id: string, note?: string): Promise<void> {
  await api.post(`/manager/ml/candidates/${id}/approve`, null, { params: note ? { note } : {} });
}

export async function rejectCandidate(id: string, note?: string): Promise<void> {
  await api.post(`/manager/ml/candidates/${id}/reject`, null, { params: note ? { note } : {} });
}

export async function promoteCandidate(id: string): Promise<void> {
  await api.post(`/manager/ml/candidates/${id}/promote`);
}
