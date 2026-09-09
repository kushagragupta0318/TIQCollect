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
import { effectiveRecoveryRate, mlBadge, rankedReasons } from "./allocationReasons";

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
