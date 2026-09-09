/**
 * Why a case went to an agent, ranked, in words a manager can act on.
 *
 * Extracted from ManagerOverviewPage on 2026-09-09 so the labelling can be
 * tested without mounting a page. The page imports from here and renders it
 * unchanged; nothing about the ranking or the thresholds moved.
 *
 * THE PERCENTAGE MUST COME FROM THE NUMBER IT EXPLAINS. That is the whole point
 * of this module and the reason it exists as its own file — see the note on
 * `expected_recovery` below.
 */

export type Reason = { key: string; label: string; share: string };

/** Loan type as a manager says it, not as the enum spells it. */
export const loanTypeWords = (b: Record<string, unknown>): string => {
  const lt = typeof b.loan_type === "string" ? b.loan_type : "";
  return lt ? `${lt.replace(/_/g, " ").toLowerCase()} loans` : "this loan type";
};

// TIER_1/2/3 map to 1.0/0.8/0.6 in global_allocator; 0.7 is the unknown-tier
// default, which has no tier to name.
export const tierWords = (b: Record<string, unknown>): string => {
  const w = Number(b.tier_weight ?? 0);
  if (w >= 1.0) return "Tier 1";
  if (w === 0.8) return "Tier 2";
  if (w === 0.6) return "Tier 3";
  return "";
};

/**
 * The recovery probability that ACTUALLY produced `expected_case_inr`.
 *
 * The allocator computes `expected_case_inr = target_amount * effective_prob`,
 * where `effective_prob` is `prob_recovery_ml` when the model drove the
 * decision and `prob_recovery` otherwise (global_allocator.py, `effective_prob`).
 * `ml_used_for_decision` is the flag it records for exactly this purpose.
 *
 * Returns null when neither is present, so the caller shows the rupee figure
 * alone rather than inventing a rate for it.
 */
export function effectiveRecoveryRate(
  b: Record<string, unknown>
): { rate: number; fromModel: boolean } | null {
  const usedModel = b.ml_used_for_decision === true;
  const raw = usedModel ? b.prob_recovery_ml : b.prob_recovery;
  const rate = Number(raw);
  if (!Number.isFinite(rate) || rate <= 0) return null;
  return { rate, fromModel: usedModel };
}

export const FACTOR_LABEL: Record<string, (b: Record<string, unknown>) => string> = {
  /**
   * 2026-09-09 — THIS LABEL USED THE WRONG NUMBER.
   *
   * It read `affinity_score` and said "recovers {affinity}% on {loan type}",
   * on the reasoning that affinity feeds prob_recovery which produces
   * expected_case_inr. That was true until `recovery_risk` was promoted on
   * 2026-09-08. Since then the rupee figure comes from `prob_recovery_ml`, and
   * affinity feeds only the shadow `prob_recovery` the allocator no longer
   * uses.
   *
   * The two are nowhere near each other. Measured across 214 allocated
   * decisions on the live book: mean affinity_score 0.8349 against mean
   * prob_recovery_ml 0.1662 — the panel was overstating the recovery rate
   * behind its own rupee figure by a factor of 5.0, to the manager deciding
   * whether the plan was sensible.
   *
   * The rate now comes from whichever probability the allocator actually
   * multiplied by, and says which one it was.
   */
  expected_recovery: (b) => {
    const rupees = `₹${Math.round(Number(b.expected_case_inr ?? 0)).toLocaleString("en-IN")} expected recovery`;
    const eff = effectiveRecoveryRate(b);
    if (!eff) return rupees;
    const pct = Math.round(eff.rate * 100);
    return eff.fromModel
      ? `${rupees} — model puts recovery at ${pct}% for this borrower`
      : `${rupees} — recovers ${pct}% on ${loanTypeWords(b)}`;
  },
  proximity: (b) => `${b.proximity_km ?? "?"} km from the agent's base`,
  // 0.6 x tier + 0.4 x spec_match. Seniority and a DECLARED specialisation —
  // not experience. Whether they have actually worked this loan type is the
  // agent's measured recovery rate, `affinity_score`, which is shown on the
  // agent panel rather than pretending to explain the rupee figure above.
  skills: (b) => {
    const tier = tierWords(b);
    return Number(b.spec_match ?? 0) >= 1
      ? [tier, `specialises in ${loanTypeWords(b)}`].filter(Boolean).join(" · ")
      : tier ? `${tier} agent` : "Agent tier";
  },
  workload: () => "Had capacity free",
  continuity: () => "Already their case",
  language: () => "Speaks the borrower's language",
};

// A term can be present and still not be a reason. continuity_bonus is 0.10
// weighted at 0.05, so it contributes exactly 0.005 to every row it fires on —
// and it fires on nearly all of them, because the nightly plan re-plans cases
// that are already assigned (planner_service.py:193 pools assigned AND
// unassigned), so most cases are evaluated against their incumbent agent.
//
// A RELATIVE threshold cannot exclude it. Its share is 0.005 / fit, which
// crosses 1% as soon as the fit score falls to 1.0 — so it stayed visible on
// exactly the low-value, far-away cases, which is the worst place for noise.
//
// The durable property is absolute: continuity can never exceed 0.005, while
// every other factor here maxes out at 0.05 or more — a tenfold gap. So the
// floor is on the contribution itself, and it names no factor: anything that
// cannot move a decision by 0.01 is not an explanation for one. The relative
// test stays as a second filter, to drop terms that are real but drowned out.
//
// Nothing is hidden from the record — score_breakdown keeps every term. This
// governs only what is offered to a manager as a reason.
export const MIN_ABSOLUTE_CONTRIBUTION = 0.01;

export function rankedReasons(breakdown: Record<string, unknown>): Reason[] {
  const contributions = breakdown.contributions as Record<string, number> | undefined;

  if (!contributions) {
    return Object.entries(breakdown)
      .filter(([k]) => k !== "contributions")
      .map(([k, v]) => ({ key: k, label: k.replace(/_/g, " "), share: String(v) }));
  }

  const total = Object.values(contributions).reduce((a, b) => a + b, 0);

  return Object.entries(contributions)
    .filter(([, v]) => v >= MIN_ABSOLUTE_CONTRIBUTION)
    .map(([k, v]) => ({ k, v, pct: total > 0 ? Math.round((v / total) * 100) : 0 }))
    .filter((r) => r.pct >= 1)
    .sort((a, b) => b.v - a.v)
    .map((r) => ({
      key: r.k,
      label: FACTOR_LABEL[r.k]?.(breakdown) ?? r.k.replace(/_/g, " "),
      share: `${r.pct}%`,
    }));
}


/** The `ml` block the API attaches to each decision. Optional: a plan produced
 *  before the field existed, or by the LEGACY strategy, simply has none. */
export interface DecisionMl {
  used_for_decision: boolean;
  probability_used: number | null;
  model_name: string | null;
  model_version: string | null;
  feature_coverage: number | null;
  expected_recovery_inr: number | null;
  prediction_id: string | null;
}

/**
 * What to show a manager about the model's part in one decision.
 *
 * Returns null when the model did not drive it, so the caller renders nothing
 * rather than a badge that quietly means "we computed a score and ignored it".
 *
 * The version is passed through from the API, which reads it off the PREDICTION
 * ROW. Nothing here may default it: a badge that says "v1.1.0" because that
 * string is in the source would keep saying so through a rollback, which is the
 * one moment somebody is relying on it.
 */
export function mlBadge(ml: DecisionMl | undefined | null): {
  label: string;
  title: string;
} | null {
  if (!ml || ml.used_for_decision !== true) return null;
  const pct = typeof ml.probability_used === "number"
    ? `${Math.round(ml.probability_used * 100)}%`
    : null;
  const version = ml.model_version;
  const coverage = typeof ml.feature_coverage === "number"
    ? `${Math.round(ml.feature_coverage * 100)}% of inputs available`
    : null;
  return {
    label: "ML-assisted",
    // Plain words. A manager needs to know the recovery estimate came from a
    // model and how confident the inputs were — not what a WOE bin is.
    title: [
      "This allocation used a recovery model.",
      pct ? `Estimated chance of recovery: ${pct}.` : null,
      version ? `Model ${version}.` : null,
      coverage,
    ].filter(Boolean).join(" "),
  };
}
