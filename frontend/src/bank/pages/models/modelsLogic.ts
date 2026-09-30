// Pure display rules for the model pages (ModelsPage, LoanExplanationPanel).
// No React, no axios: tested in modelsLogic.test.ts.
import type { LayerKind, Prediction, ReasonRow, RecoveryRiskCard } from "@/api/bankModels";

export const KIND_LABELS: Record<LayerKind, string> = {
  TRAINED_MODEL: "Trained model",
  SCORECARD: "Hand-weighted scorecard",
  SHRINKAGE: "Closed-form formula",
  OPTIMISER: "Optimiser",
};

/** "12.3%" from a 0-1 share; null reads as a dash, never as 0%. */
export function pct(share: number | null | undefined, dp = 0): string {
  return share == null ? "—" : `${(share * 100).toFixed(dp)}%`;
}

/** A metric to the precision it was measured at; null is "—". */
export function fixed(v: number | null | undefined, dp: number): string {
  return v == null ? "—" : v.toFixed(dp);
}

export interface ReasonLine {
  key: string;
  /** "Overdue amount: 56,840" */
  what: string;
  /** The effect, in words a reader can check against the number. */
  effect: string;
  direction: ReasonRow["direction"];
  /** 0-1: this reason's size against the largest shown, for a bar. */
  weight: number;
  /** The raw figure, for the analyst view: "+0.32 log-odds" or "31 points". */
  raw: string;
}

/**
 * A stored reason, in plain words. A 2.x contribution is centred on the book's
 * average account, so the honest reading is relative ("pushes risk above the
 * book's typical account"), not absolute. A 1.1.0 scorecard reason is points
 * lost, which only ever raises risk.
 */
export function reasonLines(reasons: ReasonRow[]): ReasonLine[] {
  const max = Math.max(0, ...reasons.map((r) => r.magnitude));
  return [...reasons]
    .sort((a, b) => a.rank - b.rank)
    .map((r) => {
      const title = r.label.charAt(0).toUpperCase() + r.label.slice(1);
      const what = r.value != null && r.value !== "" ? `${title}: ${r.value}` : title;
      const effect = r.unit === "points_lost"
        ? `costs ${Math.round(r.magnitude)} scorecard points`
        : r.direction === "increases_risk"
          ? "pushes the risk above the book's typical account"
          : "pulls the risk below the book's typical account";
      const raw = r.unit === "points_lost"
        ? `${Math.round(r.magnitude)} points lost`
        : `${r.signed >= 0 ? "+" : "−"}${Math.abs(r.signed).toFixed(2)} log-odds`;
      return { key: `${r.rank}-${r.feature}`, what, effect, direction: r.direction, weight: max > 0 ? r.magnitude / max : 0, raw };
    });
}

/** "9 of 15 inputs had evidence" from a 0-1 coverage and the model's input count. */
export function coverageText(coverage: number | null, nFeatures: number | null): string {
  if (coverage == null) return "Input coverage not recorded";
  if (!nFeatures) return `${pct(coverage)} of inputs had evidence`;
  return `${Math.round(coverage * nFeatures)} of ${nFeatures} inputs had evidence`;
}

export type ExplanationState =
  | { kind: "unscored" }
  | { kind: "declined"; reason: string; coverage: string; floor: string }
  | { kind: "scored"; pPayment: string; pNoPayment: string; band: string | null };

/** What the panel's headline says. A decline is a decision, shown as one. */
export function explanationState(p: Prediction | null): ExplanationState {
  if (!p) return { kind: "unscored" };
  if (!p.is_modelled || p.p_no_payment == null) {
    return {
      kind: "declined",
      reason: p.fallback_reason ?? "The model declined to score this account.",
      coverage: coverageText(p.feature_coverage, p.n_features),
      floor: pct(p.coverage_floor),
    };
  }
  return { kind: "scored", pPayment: pct(p.p_payment), pNoPayment: pct(p.p_no_payment), band: p.band };
}

/** The intercept + contributions = logit check an analyst can do by eye. */
export function reconciliation(contributions: Record<string, number> | null): { intercept: number; sum: number; logit: number; gap: number } | null {
  if (!contributions || contributions.logit == null || contributions.intercept == null) return null;
  const sum = Object.entries(contributions)
    .filter(([k]) => k !== "logit" && k !== "intercept")
    .reduce((s, [, v]) => s + v, 0);
  const total = contributions.intercept + sum;
  return { intercept: contributions.intercept, sum, logit: contributions.logit, gap: Math.abs(total - contributions.logit) };
}

/** The stance line on the model card: how much of the book the model's strongest behavioural input reaches. */
export function stanceCoverageText(s: RecoveryRiskCard["stance"]): string {
  if (!s.latest_scoring_day || s.accounts_scored === 0) {
    return "No scored accounts yet, so there is no coverage to report.";
  }
  if (s.share_sampled) {
    return `${pct(s.share, 1)} of a ${(s.sample_size ?? 0).toLocaleString("en-IN")}-account sample, from the ` +
      `${s.accounts_scored.toLocaleString("en-IN")} scored on ${s.latest_scoring_day}, carried a recorded borrower stance.`;
  }
  return `${s.accounts_with_stance.toLocaleString("en-IN")} of ${s.accounts_scored.toLocaleString("en-IN")} accounts ` +
    `(${pct(s.share, 1)}) scored on ${s.latest_scoring_day} carried a recorded borrower stance.`;
}

export function monitoringText(m: RecoveryRiskCard["monitoring"]): string {
  if (m.status === "unavailable") return "The monitoring state could not be read.";
  if (m.status === "ready") return `Monitoring is live: at least ${m.required_matured} matured outcomes on the serving version.`;
  const from = m.first_outcomes_mature_from ? ` The first outcomes on your book mature from ${m.first_outcomes_mature_from}.` : "";
  return `Not yet monitored: live performance is measured once ${m.required_matured} outcomes have matured ` +
    `(${m.horizon_days} days after scoring).${from} Until then no live Gini or KS exists, and none is shown.`;
}
