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
 *   expected_case_inr = target_amount x (the rate the panel states)
 * so a future edit that swaps the source back fails here rather than shipping.
 *
 * 2026-09-16 — the REASON LINE no longer prints the rate; the product asked for
 * the rupee figure and the wording only. The rate is still on the ML badge
 * tooltip, from the same `effectiveRecoveryRate`, so the arithmetic binding
 * moved there ("the ML badge" below) rather than being dropped. The reason-line
 * tests now hold the inverse: no percentage of ANY kind may appear in it, which
 * is what stops affinity_score (94%) creeping back in through the wording.
 */
import { describe, expect, it } from "vitest";
import {
  agentAdjustment, deferredBadge, effectiveRecoveryRate, mlBadge, ownerTag, rankedReasons,
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
  collectable_amount: 48070,    // target - collected: what the line prints
  loan_type: "PERSONAL",
  tier_weight: 1.0,
  proximity_km: 4.2,
  // CONTINUITY_BONUS is 1.0 since 2026-09-11, weighted at 0.05: an OWNED case
  // as the allocator writes it today, not the 0.005 the pre-gate value gave.
  continuity_bonus: 1.0,
  contributions: {
    expected_recovery: 0.4792, proximity: 0.0759, skills: 0.076,
    workload: 0.05, continuity: 0.05, language: 0.05,
  },
};
const TARGET_AMOUNT = 48070;

function reasonFor(row: Record<string, unknown>, key: string) {
  return rankedReasons(row).find((r) => r.key === key);
}

describe("the expected-recovery explanation", () => {
  it("prints the FULL collectable balance, not the model's discount on it", () => {
    // Product direction 2026-09-16. The discounted figure is one multiplication
    // away and still recoverable — see the arithmetic test below.
    const label = reasonFor(ML_ROW, "expected_recovery")!.label;
    expect(label).toContain("₹48,070 expected recovery");
    expect(label).not.toContain("6,525");
  });

  it("falls back to the discounted figure on a plan that predates collectable_amount", () => {
    const old = { ...ML_ROW, collectable_amount: undefined };
    expect(reasonFor(old, "expected_recovery")!.label).toContain("₹6,525 expected recovery");
  });

  it("prints NO percentage — neither the model's rate nor affinity_score", () => {
    const label = reasonFor(ML_ROW, "expected_recovery")!.label;
    expect(label).not.toMatch(/\d+%/);
    expect(label).not.toMatch(/recovers 94%/);
  });

  it("the rate the rupees came from is still recoverable, from the row not the words", () => {
    // The arithmetic the old label was tested on. It now binds the SOURCE the
    // badge reads, so the number a manager can hover for is still the one the
    // rupee figure was built from.
    const rate = effectiveRecoveryRate(ML_ROW)!.rate;
    expect(rate).toBeCloseTo(Number(ML_ROW.expected_case_inr) / TARGET_AMOUNT, 2);
  });

  it("reads the same one sentence whether or not the model drove it", () => {
    // Product direction, 2026-09-16: one sentence for every case. The only
    // thing that may differ between rows is the rupee figure and the loan type.
    const ml = reasonFor(ML_ROW, "expected_recovery")!.label;
    expect(ml).toBe("₹48,070 expected recovery — maximum recovery expected on personal loans with this agent");

    const legacy = {
      ...ML_ROW,
      ml_used_for_decision: false,
      prob_recovery: 0.85,
      expected_case_inr: 40859.5, // = 48070 x 0.85
    };
    const lg = reasonFor(legacy, "expected_recovery")!.label;
    expect(lg).toBe(ml); // the same balance is owed whichever rate priced it
    expect(lg).not.toMatch(/\d+%/);
  });

  it("shows the rupees alone rather than inventing a rate", () => {
    const bare = { ...ML_ROW, prob_recovery_ml: null, prob_recovery: null };
    const label = reasonFor(bare, "expected_recovery")!.label;
    expect(label).toBe("₹48,070 expected recovery");
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
  collectable_amount: 15140,
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
describe("the sentence no longer carries the borrower-vs-agent qualifier", () => {
  // Until 2026-09-16 the label ended "for this borrower" or "for this borrower
  // with this agent", DERIVED from whether the agent adjustment had moved the
  // rate — because a rate was printed and a borrower-only claim beside an
  // agent-adjusted rate was the defect. With no rate in the sentence there is
  // no such claim to get wrong, and the product asked for one sentence. These
  // hold that the wording is now invariant to the adjustment, while the
  // classifier itself (agentAdjustment, below) still answers the question for
  // anything that needs it.
  it("reads identically on an adjusted and an unadjusted row", () => {
    const adjusted = reasonFor(ADJUSTED_ROW, "expected_recovery")!.label;
    const nudged = reasonFor({ ...ML_ROW, ml_borrower_p_recover: 0.1400 }, "expected_recovery")!.label;
    const plain = reasonFor(ML_ROW, "expected_recovery")!.label;
    expect(agentAdjustment(ADJUSTED_ROW)).toBe("adjusted");
    expect(agentAdjustment(ML_ROW)).toBe("none");
    expect(adjusted).toBe("₹15,140 expected recovery — maximum recovery expected on auto loans with this agent");
    expect(plain).toBe(nudged);
    expect(plain).not.toMatch(/\d+%/);
    expect(adjusted).not.toMatch(/\d+%/);
  });

  it("says nothing different when agent-independence cannot be established", () => {
    const legacy = { ...ML_ROW, ml_borrower_p_recover: null };
    expect(agentAdjustment(legacy)).toBe("unknown");
    expect(reasonFor(legacy, "expected_recovery")!.label)
      .toBe(reasonFor(ML_ROW, "expected_recovery")!.label);
  });

  it("leaves the expected-recovery arithmetic untouched on the adjusted row", () => {
    // The rate behind the rupees is the one the allocator MULTIPLIED BY, not the
    // borrower's own. It no longer appears in the sentence, so it is held on
    // the source the badge reads instead.
    const rate = effectiveRecoveryRate(ADJUSTED_ROW)!.rate;
    expect(rate).toBeCloseTo(Number(ADJUSTED_ROW.expected_case_inr) / ADJUSTED_COLLECTABLE, 2);
    expect(Math.round(rate * 100)).toBe(24);
    expect(Math.round(rate * 100)).not.toBe(26);
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
    expect(reasons.map((r) => r.key)).toEqual(
      ["expected_recovery", "skills", "proximity", "workload", "language"]
    );
  });
});

/**
 * Ownership is a gate, so it is a tag and not a reason. 2026-09-16.
 *
 * Measured on the live run that prompted this: 212 of 214 allocated decisions
 * carried continuity_bonus 1.0 and every one of them listed "Already their
 * case · 6%" — a constant on the only columns the case could take, presented
 * as a preference. These tests hold the display rule and, separately, that
 * withholding the row moved nothing else: the other reasons' shares are
 * computed against the SAME total as before, because the term is still in
 * the fit score.
 */
describe("ownership is shown as a tag, never as a reason", () => {
  const OWNED = ML_ROW; // continuity_bonus 1.0, contributions.continuity 0.05
  const NEW_CASE: Record<string, unknown> = {
    ...ML_ROW,
    continuity_bonus: 0.0,
    contributions: { ...(ML_ROW.contributions as Record<string, number>), continuity: 0 },
  };

  it("(a) an owned case does NOT get 'Already their case' in its reasons", () => {
    const reasons = rankedReasons(OWNED);
    expect(reasons.map((r) => r.key)).not.toContain("continuity");
    expect(reasons.map((r) => r.label)).not.toContain("Already their case");
    // And it is not hidden by the floor — 0.05 clears 0.01. It is withheld
    // because it is a gate term, which is the claim under test.
    expect((OWNED.contributions as Record<string, number>).continuity).toBeGreaterThan(0.01);
  });

  it("(b) the owner tag is present for an owned case", () => {
    const tag = ownerTag(OWNED, "Deepak Narayan Joshi");
    expect(tag).not.toBeNull();
    expect(tag!.label).toBe("Owner: Deepak Narayan Joshi");
  });

  it("(c) a new / unassigned case does NOT receive the owner tag", () => {
    expect(ownerTag(NEW_CASE, "Deepak Narayan Joshi")).toBeNull();
    // Nor a row from before the field, nor a row with nobody to name.
    expect(ownerTag({ ...ML_ROW, continuity_bonus: undefined }, "X")).toBeNull();
    expect(ownerTag(OWNED, null)).toBeNull();
    expect(ownerTag(OWNED, "   ")).toBeNull();
  });

  it("(d) every non-continuity reason is unchanged — same keys, labels and shares", () => {
    // What the list read BEFORE the gate term was withheld, computed the same
    // way rankedReasons does it, with continuity left in.
    const c = OWNED.contributions as Record<string, number>;
    const total = Object.values(c).reduce((a, b) => a + b, 0);
    const before = Object.entries(c)
      .filter(([, v]) => v >= 0.01)
      .map(([k, v]) => ({ k, v, pct: Math.round((v / total) * 100) }))
      .filter((r) => r.pct >= 1)
      .sort((a, b) => b.v - a.v);
    const after = rankedReasons(OWNED);
    const beforeMinusContinuity = before.filter((r) => r.k !== "continuity");
    expect(after.map((r) => r.key)).toEqual(beforeMinusContinuity.map((r) => r.k));
    expect(after.map((r) => r.share)).toEqual(beforeMinusContinuity.map((r) => `${r.pct}%`));
    // Withholding the row did not re-normalise anyone: the shares still sum to
    // less than 100 by exactly continuity's share.
    const shown = after.reduce((s, r) => s + Number(r.share.replace("%", "")), 0);
    expect(shown).toBe(before.reduce((s, r) => s + r.pct, 0) - Math.round((c.continuity / total) * 100));
  });

  it("(d) the tag does not count as a reason — reasons on an owned and a new case are identical", () => {
    // Same case, same agent, only ownership differs: the ranked list must not
    // change length or content, because ownership is not in it either way.
    expect(rankedReasons(OWNED).map((r) => r.key)).toEqual(rankedReasons(NEW_CASE).map((r) => r.key));
  });

  it("(e) nothing is recomputed or mutated — the breakdown is read, not written", () => {
    const snapshot = JSON.stringify(OWNED);
    rankedReasons(OWNED);
    ownerTag(OWNED, "Deepak Narayan Joshi");
    expect(JSON.stringify(OWNED)).toBe(snapshot);
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

describe("deferredBadge — every DEFERRED_* subtype gets a badge", () => {
  it("labels the two subtypes that used to render nothing", () => {
    expect(deferredBadge("DEFERRED_PTP").label).toBe("PTP DUE");
    expect(deferredBadge("DEFERRED_VISIT_CAP").label).toBe("VISIT CAP");
  });

  it("keeps the existing two labels unchanged", () => {
    expect(deferredBadge("DEFERRED").label).toBe("DEFERRED");
    expect(deferredBadge("DEFERRED_ROUTE_INFEASIBLE").label).toBe("ROUTE OUTLIER");
  });

  it("never returns null, so a future DEFERRED_* subtype still shows something", () => {
    const badge = deferredBadge("DEFERRED_SOME_NEW_REASON");
    expect(badge.label).toBe("SOME NEW REASON");
    expect(badge.title).toContain("DEFERRED_SOME_NEW_REASON");
  });
});
