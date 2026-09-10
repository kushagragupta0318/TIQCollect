/**
 * The explanation must describe the number it is attached to.
 *
 * WHAT WENT WRONG. The Explainable Decisions panel rendered
 *   "Rs X expected recovery - recovers {affinity_score}% on {loan type}"
 * while X was produced by `prob_recovery_ml`. Affinity feeds only the shadow
 * `prob_recovery` the allocator stopped using when recovery_risk was promoted
 * on 2026-09-08. Measured across 214 allocated decisions on the live book:
 * mean affinity_score 0.8349 against mean prob_recovery_ml 0.1662 — a 5.0x
 * overstatement, shown to the manager deciding whether the plan was sensible.
 *
 * The binding property these tests hold is arithmetic, not wording:
 *   expected_case_inr = target_amount x (the rate the label states)
 * so a future edit that swaps the source back fails here rather than shipping.
 */
import { describe, expect, it } from "vitest";
import {
  agentAdjustment, effectiveRecoveryRate, mlBadge, rankedReasons,
} from "./allocationReasons";

/** A real row, copied from allocation_decisions on the live demo book. */
const ML_ROW: Record<string, unknown> = {
  affinity_score: 0.941,
  prob_recovery: 0.85,          // shadow: the old formula, NOT used
  prob_recovery_ml: 0.1357,     // what the allocator multiplied by
  ml_borrower_p_recover: 0.1357,
  ml_used_for_decision: true,
  value_transform: "log_rescaled",
  expected_case_inr: 6524.64,   // = 48070 x 0.1357
  loan_type: "PERSONAL",
  tier_weight: 1.0,
  proximity_km: 4.2,
  contributions: {
    expected_recovery: 0.4792, proximity: 0.0759, skills: 0.076,
    workload: 0.05, continuity: 0.005, language: 0.05,
  },
};
const TARGET_AMOUNT = 48070;

function reasonFor(row: Record<string, unknown>, key: string) {
  return rankedReasons(row).find((r) => r.key === key);
}
function percentIn(label: string): number {
  const m = label.match(/(\d+)%/);
  if (!m) throw new Error(`no percentage in: ${label}`);
  return Number(m[1]);
}

describe("the expected-recovery explanation", () => {
  it("states the rate that actually produced the rupee figure", () => {
    const label = reasonFor(ML_ROW, "expected_recovery")!.label;
    const shown = percentIn(label) / 100;
    const implied =
      Number(ML_ROW.expected_case_inr) / TARGET_AMOUNT; // what the allocator used
    expect(shown).toBeCloseTo(implied, 2);
  });

  it("does NOT state affinity_score when the model drove the decision", () => {
    const label = reasonFor(ML_ROW, "expected_recovery")!.label;
    // 94% would be affinity_score. That is the 5x overstatement.
    expect(percentIn(label)).not.toBe(94);
    expect(percentIn(label)).toBe(14);
    expect(label).not.toMatch(/recovers 94%/);
  });

  it("says the figure came from the model, so a manager can weigh it", () => {
    const label = reasonFor(ML_ROW, "expected_recovery")!.label;
    expect(label).toMatch(/model/i);
    expect(label).toContain("6,525");
  });

  it("falls back to the agent's own rate when the model did NOT drive it", () => {
    const legacy = {
      ...ML_ROW,
      ml_used_for_decision: false,
      prob_recovery: 0.85,
      expected_case_inr: 40859.5, // = 48070 x 0.85
    };
    const label = reasonFor(legacy, "expected_recovery")!.label;
    const shown = percentIn(label) / 100;
    expect(shown).toBeCloseTo(Number(legacy.expected_case_inr) / TARGET_AMOUNT, 2);
    expect(label).not.toMatch(/model/i);
  });

  it("shows the rupees alone rather than inventing a rate", () => {
    const bare = { ...ML_ROW, prob_recovery_ml: null, prob_recovery: null };
    const label = reasonFor(bare, "expected_recovery")!.label;
    expect(label).toBe("₹6,525 expected recovery");
  });
});

/**
 * A REAL adjusted row, copied from allocation_decisions on the run of
 * 2026-09-10 — the largest borrower-vs-used gap on the book.
 *
 * A Tier 3 agent: 0.2556 x eb 1.00 x (0.8 + 0.2 x 0.6) = 0.2351. The borrower's
 * own figure rounds to 26% and the figure the allocator multiplied by rounds to
 * 24%, so the old label showed 24% and called it "for this borrower".
 */
const ADJUSTED_ROW: Record<string, unknown> = {
  affinity_score: 0.861,
  prob_recovery: 0.85,
  prob_recovery_ml: 0.2351,
  ml_borrower_p_recover: 0.2556,
  eb_multiplier: 1.0,
  tier_weight: 0.6,
  ml_used_for_decision: true,
  value_transform: "log_rescaled",
  expected_case_inr: 3560.05,     // = 15140 x 0.235142
  loan_type: "AUTO",
  proximity_km: 1.0,
  contributions: {
    expected_recovery: 0.2243, proximity: 0.3338, skills: 0.038,
    workload: 0.025, continuity: 0.05, language: 0.05,
  },
};
const ADJUSTED_COLLECTABLE = 15140;

/**
 * The rate on screen is agent-adjusted, and the sentence must not say otherwise.
 *
 * WHAT WENT WRONG. "for this borrower" was an unconditional literal, while
 * prob_recovery_ml is clamp(borrower x eb_multiplier x tier_uplift). Measured on
 * 2026-09-10 over 214 allocated decisions: 155 carried an adjustment and 104
 * displayed a DIFFERENT whole percent from the borrower's own figure.
 *
 * The property held here is that the QUALIFIER IS DERIVED. These tests move one
 * field and nothing else, so a future edit that pins the phrase back to a
 * constant fails here rather than shipping — which is what the earlier
 * affinity_score literal did.
 *
 * CONFIRMED BY MUTATION, 2026-09-10 — each variant applied to the source, the
 * suite run, the source restored. A test that cannot fail proves nothing, and
 * these were run rather than reasoned about:
 *
 *   M1  the conditional removed, unconditional "for this borrower"
 *       (literally the pre-fix line)          -> 3 failed / 20 passed
 *   M2  the opposite unconditional literal,
 *       "for this borrower with this agent"   -> 3 failed / 20 passed
 *   M3  agentAdjustment pinned to "none"      -> 3 failed / 20 passed
 *   M4  typeof guard removed, Number() back   -> 1 failed / 22 passed
 *
 * M4 is the one that matters most: it is not hypothetical. The first draft of
 * agentAdjustment DID use Number(), `Number(null)` is 0 rather than NaN, and a
 * missing borrower probability was therefore classified as "adjusted" —
 * asserting an agent effect from a value nobody recorded. That is why the
 * unknown case is tested at all.
 */
describe("borrower-vs-agent wording is derived, not asserted", () => {
  it("does NOT claim borrower-only when the agent adjustment moved the rate", () => {
    const label = reasonFor(ADJUSTED_ROW, "expected_recovery")!.label;
    expect(label).toContain("for this borrower with this agent");
    // The failing shape: the sentence ending at the borrower and stopping.
    expect(label).not.toMatch(/for this borrower$/);
  });

  it("allows borrower-only wording when there is no agent adjustment", () => {
    const label = reasonFor(ML_ROW, "expected_recovery")!.label;
    expect(label).toMatch(/for this borrower$/);
    expect(label).not.toContain("with this agent");
  });

  it("reads the data rather than the row's shape — one field flips it", () => {
    // Identical to the unadjusted row but for the borrower-side probability.
    // If the qualifier were hardcoded, both would render the same phrase.
    const nudged = { ...ML_ROW, ml_borrower_p_recover: 0.1400 };
    expect(reasonFor(ML_ROW, "expected_recovery")!.label)
      .not.toContain("with this agent");
    expect(reasonFor(nudged, "expected_recovery")!.label)
      .toContain("with this agent");
  });

  it("states neither when agent-independence cannot be established", () => {
    // A decision recorded before ml_borrower_p_recover existed. Guessing either
    // way would be inventing evidence.
    const legacy = { ...ML_ROW, ml_borrower_p_recover: null };
    const label = reasonFor(legacy, "expected_recovery")!.label;
    expect(agentAdjustment(legacy)).toBe("unknown");
    expect(label).not.toContain("for this borrower");
    expect(label).not.toContain("with this agent");
    expect(label).toMatch(/model puts recovery at 14%$/);
  });

  it("leaves the expected-recovery arithmetic untouched on the adjusted row", () => {
    const label = reasonFor(ADJUSTED_ROW, "expected_recovery")!.label;
    const shown = percentIn(label) / 100;
    const implied = Number(ADJUSTED_ROW.expected_case_inr) / ADJUSTED_COLLECTABLE;
    expect(shown).toBeCloseTo(implied, 2);
    // The rate stated is the one the allocator MULTIPLIED BY, not the borrower's
    // own — changing the wording must not change which number is shown.
    expect(percentIn(label)).toBe(24);
    expect(percentIn(label)).not.toBe(26);
    expect(label).toContain("3,560");
  });

  it("never touches the non-model path", () => {
    const legacy = {
      ...ADJUSTED_ROW, ml_used_for_decision: false, expected_case_inr: 12869,
    };
    const label = reasonFor(legacy, "expected_recovery")!.label;
    expect(label).toContain("recovers 85% on auto loans");
    expect(label).not.toContain("borrower");
  });
});

describe("agentAdjustment", () => {
  it("classifies each of the three states", () => {
    expect(agentAdjustment(ADJUSTED_ROW)).toBe("adjusted");
    expect(agentAdjustment(ML_ROW)).toBe("none");
    expect(agentAdjustment({})).toBe("unknown");
  });

  it("does not read an adjustment into floating-point noise", () => {
    const noisy = { prob_recovery_ml: 0.1 + 0.2, ml_borrower_p_recover: 0.3 };
    expect(agentAdjustment(noisy)).toBe("none");
  });
});

describe("effectiveRecoveryRate", () => {
  it("reads prob_recovery_ml when ml_used_for_decision is true", () => {
    expect(effectiveRecoveryRate(ML_ROW)).toEqual({ rate: 0.1357, fromModel: true });
  });

  it("reads prob_recovery when it is false", () => {
    expect(effectiveRecoveryRate({ ...ML_ROW, ml_used_for_decision: false }))
      .toEqual({ rate: 0.85, fromModel: false });
  });

  it("never silently substitutes affinity_score", () => {
    const noProbs = { affinity_score: 0.941, ml_used_for_decision: true };
    expect(effectiveRecoveryRate(noProbs)).toBeNull();
  });
});

describe("the rest of the panel is unchanged", () => {
  it("still ranks by contribution and drops sub-threshold terms", () => {
    const reasons = rankedReasons(ML_ROW);
    expect(reasons[0].key).toBe("expected_recovery");
    // continuity contributes 0.005, below MIN_ABSOLUTE_CONTRIBUTION.
    expect(reasons.map((r) => r.key)).not.toContain("continuity");
    expect(reasons.map((r) => r.key)).toEqual(
      ["expected_recovery", "skills", "proximity", "workload", "language"]
    );
  });
});

describe("the ML badge", () => {
  const ML = {
    used_for_decision: true, probability_used: 0.1357,
    model_name: "recovery_risk", model_version: "1.1.0",
    feature_coverage: 1.0, expected_recovery_inr: 6524.64,
    prediction_id: "pred-1",
  };

  it("appears only when the model actually drove the decision", () => {
    expect(mlBadge(ML)?.label).toBe("ML-assisted");
    // Scored but shadowed: no badge. A badge here would mean "we computed a
    // score and ignored it", which is the opposite of what it says.
    expect(mlBadge({ ...ML, used_for_decision: false })).toBeNull();
    expect(mlBadge(undefined)).toBeNull();
    expect(mlBadge(null)).toBeNull();
  });

  it("states the SAME probability the rupee figure was built from", () => {
    const pct = Number(mlBadge(ML)!.title.match(/recovery: (\d+)%/)![1]);
    const fromBreakdown = Math.round(
      effectiveRecoveryRate({
        ml_used_for_decision: true, prob_recovery_ml: ML.probability_used,
      })!.rate * 100
    );
    expect(pct).toBe(fromBreakdown);
  });

  it("reports the version it was GIVEN, never a constant", () => {
    expect(mlBadge(ML)!.title).toContain("Model 1.1.0");
    // A rollback serves a different version; the badge must follow it.
    expect(mlBadge({ ...ML, model_version: "1.0.0" })!.title)
      .toContain("Model 1.0.0");
    expect(mlBadge({ ...ML, model_version: "1.0.0" })!.title)
      .not.toContain("1.1.0");
    // And says nothing at all rather than inventing one.
    expect(mlBadge({ ...ML, model_version: null })!.title).not.toMatch(/Model /);
  });

  it("keeps the wording free of implementation detail", () => {
    const title = mlBadge(ML)!.title;
    for (const jargon of ["WOE", "logistic", "calibrat", "sha256", "joblib",
                          "prob_recovery", "affinity"]) {
      expect(title.toLowerCase()).not.toContain(jargon.toLowerCase());
    }
  });
});

describe("backend contract", () => {
  /** The exact `ml` block shape the API builds, so a rename breaks a test
   *  rather than silently emptying the badge. */
  it("reads the field names the API actually sends", () => {
    const fromApi = {
      used_for_decision: true,
      probability_used: 0.048,
      borrower_p_recover: 0.05,
      shadow_prob_recovery: 0.7644,
      value_transform: "log_rescaled",
      expected_recovery_inr: 2451.98,
      prediction_id: "be3ef597",
      model_name: "recovery_risk",
      model_version: "1.1.0",
      feature_coverage: 1.0,
    };
    const badge = mlBadge(fromApi)!;
    expect(badge.label).toBe("ML-assisted");
    expect(badge.title).toContain("5%");          // 0.048 -> 5%
    expect(badge.title).toContain("Model 1.1.0");
    expect(badge.title).toContain("100% of inputs available");
  });

  it("never falls back to the shadow probability", () => {
    const shadowOnly = {
      used_for_decision: false, probability_used: null,
      shadow_prob_recovery: 0.7644, model_name: "recovery_risk",
      model_version: "1.1.0", feature_coverage: 1.0,
      expected_recovery_inr: 100, prediction_id: "x",
    };
    // No badge at all — and certainly not one claiming 76%.
    expect(mlBadge(shadowOnly)).toBeNull();
  });
});
