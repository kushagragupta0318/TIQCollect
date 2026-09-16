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

/**
 * Whether the rate the allocator used is a property of the BORROWER ALONE.
 *
 * 2026-09-10 — THE LABEL CLAIMED IT ALWAYS WAS, AND IT IS NOT.
 * `prob_recovery_ml` is not the model's output. `global_allocator._prob_recovery_ml`
 * computes clamp(borrower_p_recover x eb_multiplier x (0.8 + 0.2 x tier_weight),
 * 0.02, 0.85), so the number on screen carries the agent's empirical-Bayes
 * multiplier and their tier while the sentence said "for this borrower".
 *
 * Measured on the run of 2026-09-10, 214 allocated decisions:
 *   prob_recovery_ml != ml_borrower_p_recover     155 of 214
 *   the DISPLAYED whole percent differs           104 of 214
 *   max gap 2.05pp, mean 0.55pp
 * The worst row is a Tier 3 agent: borrower 0.2556 x 1.00 x 0.92 = 0.2351, shown
 * as 24% where the borrower's own figure is 26%. Small today only because
 * eb_multiplier sits at 0.98-1.06 while 297 of 315 (agent, segment) cells are
 * below the five-observation threshold. It is bounded [0.75, 1.25], and the
 * epsilon-greedy slice exists to push those cells over that threshold — so the
 * gap widens by design rather than by accident.
 *
 * THE CONDITION IS DERIVED, NEVER ASSERTED, and that is the entire point. A
 * hardcoded qualifier describing today's formula is exactly what went stale last
 * time — see the note on `expected_recovery` below. Comparing the two numbers
 * asks the question the sentence actually makes ("is this rate borrower-only?")
 * instead of restating the arithmetic that answers it, so it stays correct if
 * the adjustment changes shape, is removed, or starts biting harder. It also
 * needs no knowledge of WHICH factor moved it: EB, tier or the clamp all falsify
 * the same claim.
 *
 * "unknown" is its own answer and is not folded into either. A decision recorded
 * before `ml_borrower_p_recover` existed cannot establish agent-independence,
 * and guessing either way would be inventing evidence — so the caller states
 * neither, the same way it shows rupees alone rather than inventing a rate.
 */
export type AgentAdjustment = "adjusted" | "none" | "unknown";

// Both values are rounded to 4dp by the allocator before they are persisted, so
// exact equality would serve. This guards float representation only, and sits far
// below the smallest adjustment the formula can produce — eb_multiplier 0.98 on a
// rate of 0.15 moves it by 0.003.
export const RATE_EPSILON = 1e-9;

export function agentAdjustment(b: Record<string, unknown>): AgentAdjustment {
  // typeof, NOT Number(). The API sends JSON, so an absent probability arrives
  // as null — and `Number(null)` is 0, which is finite. The first draft of this
  // function used Number() and classified a MISSING borrower probability as
  // "adjusted", asserting an agent effect from a value nobody recorded. Caught
  // by test_states_neither_when_agent_independence_cannot_be_established, which
  // is the reason that case is tested rather than assumed benign.
  const used = b.prob_recovery_ml;
  const borrower = b.ml_borrower_p_recover;
  if (typeof used !== "number" || typeof borrower !== "number") return "unknown";
  if (!Number.isFinite(used) || !Number.isFinite(borrower)) return "unknown";
  return Math.abs(used - borrower) > RATE_EPSILON ? "adjusted" : "none";
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
    // 2026-09-16 — THE PERCENTAGE IS NO LONGER SHOWN HERE, AND THE RUPEE FIGURE
    // IS THE FULL COLLECTABLE BALANCE, BOTH ON PRODUCT DIRECTION.
    //
    // Until today this read "₹7,038 expected recovery — model puts recovery at
    // 12% for this borrower": expected_case_inr (collectable x the rate the
    // allocator multiplied by) beside the rate, with the tests binding the two
    // so the rate could never again drift from the figure (the 5x affinity
    // overstatement above). The product asked for the rate to go, for ONE
    // sentence on every case, and for the figure to be the full balance still
    // owed rather than the model's discount on it — a manager reading "₹7,038"
    // and "12%" beside every row took it as the plan being weak, when it is
    // simply what a calibrated probability on a 60+ DPD book looks like.
    //
    // `collectable_amount` is target − collected, sent per decision by the API
    // and merged into the breakdown by the page. It is the base the allocator
    // multiplies, so the model's own figure is still one multiplication away:
    // expected_case_inr = collectable_amount x effectiveRecoveryRate(b).rate,
    // and the rate is still on the ML badge tooltip (mlBadge below). Where a
    // plan predates the field, expected_case_inr is shown so the line never
    // reads ₹0.
    //
    // "with this agent" is unconditional and that is correct rather than the
    // 2026-09-10 defect returning: the defect was a borrower-ONLY claim beside
    // an agent-adjusted rate. No rate, no such claim. `agentAdjustment` stays
    // exported for the badge and the tests. "Maximum recovery expected"
    // describes the assignment — the allocator picked this pairing as the best
    // expected yield available for the case — not a ceiling on what the
    // borrower will pay.
    const full = Number(b.collectable_amount);
    const amount = Number.isFinite(full) && full > 0 ? full : Number(b.expected_case_inr ?? 0);
    const rupees = `₹${Math.round(amount).toLocaleString("en-IN")} expected recovery`;
    if (!effectiveRecoveryRate(b)) return rupees;
    return `${rupees} — maximum recovery expected on ${loanTypeWords(b)} with this agent`;
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

// A term can be present and still not be a reason. The floor is on the
// contribution itself and names no factor: anything that cannot move a
// decision by 0.01 is not an explanation for one. The relative test stays as a
// second filter, to drop terms that are real but drowned out.
//
// *(This block used to say "continuity_bonus is 0.10 weighted at 0.05, so it
// contributes exactly 0.005 … continuity can never exceed 0.005, while every
// other factor here maxes out at 0.05 or more — a tenfold gap", and relied on
// this floor to keep "Already their case" off the panel. That stopped being
// true on 2026-09-11: `GlobalAllocator.CONTINUITY_BONUS` is now 1.0, so the
// term contributes 0.05 — the same as workload and language — and clears the
// floor on every row it fires on. Measured on the run of 2026-09-16: 212 of
// 214 allocated decisions carried it, at "6%" each. Corrected rather than
// deleted, because the floor is still right for what it does; it was the
// claim that it handled continuity that went stale.)*
//
// Nothing is hidden from the record — score_breakdown keeps every term. This
// governs only what is offered to a manager as a reason.
export const MIN_ABSOLUTE_CONTRIBUTION = 0.01;

// 2026-09-16 — OWNERSHIP IS A GATE, SO IT IS NOT A REASON.
//
// Since 2026-09-11 a case that already has an agent is only ever offered back
// to that agent (global_allocator.py, "STICKY CASE OWNERSHIP", beside the other
// hard gates). `continuity_bonus` fires exactly when `case.agent_id == agent.id`
// — the allocated agent is the owner — so on every row where it is non-zero it
// was a constant across the only columns the case could take, and it could not
// have changed the choice. Listing it as "Already their case · 6%" beside
// "1.5 km from the agent's base · 10%" presents a constraint as a preference.
//
// The term is still in `score_breakdown.contributions` and still in the fit
// score, so the OTHER reasons' shares are unchanged — the total they divide by
// is untouched. Only the row is withheld from the list; the fact it recorded
// is shown by `ownerTag` below, as a tag, where a manager reads it as what it
// is. Nothing about the allocator moved for this: it is a display rule.
export const GATE_TERMS: ReadonlySet<string> = new Set(["continuity"]);

/**
 * "Owner: <agent>" for a case that already had an agent when it was planned.
 *
 * Read from the breakdown the allocator wrote — `continuity_bonus > 0` is the
 * allocator's own record that the case was owned by the agent it went to — not
 * from any client-side guess about the case. A new (unassigned) case has
 * `continuity_bonus` 0 and gets no tag. Returns null rather than "Owner: "
 * when the name is missing, so the tag never asserts ownership it cannot name.
 */
export function ownerTag(
  breakdown: Record<string, unknown> | null | undefined,
  agentName: string | null | undefined,
): { label: string; title: string } | null {
  const bonus = Number(breakdown?.continuity_bonus);
  if (!Number.isFinite(bonus) || bonus <= 0) return null;
  const name = typeof agentName === "string" ? agentName.trim() : "";
  if (!name) return null;
  return {
    label: `Owner: ${name}`,
    title: "This case was already assigned to this agent. Ownership is kept by rule, not scored — it is not one of the reasons listed below.",
  };
}

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
    // After the shares, so withholding a gate term cannot move anyone else's
    // percentage — the fit score it divides by still includes it.
    .filter((r) => !GATE_TERMS.has(r.k))
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
