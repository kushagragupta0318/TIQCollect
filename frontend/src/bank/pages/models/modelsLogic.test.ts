/** The model pages' display rules (modelsLogic.ts): plain words, polarity, abstention. */
import { describe, expect, it } from "vitest";
import type { Prediction, ReasonRow, RecoveryRiskCard } from "@/api/bankModels";
import {
  coverageText, explanationState, monitoringText, pct, reasonLines, reconciliation, stanceCoverageText,
} from "./modelsLogic";

const reason = (o: Partial<ReasonRow>): ReasonRow => ({
  rank: 1, feature: "overdue_amount", label: "overdue amount", kind: "feature", value: "56,840",
  direction: "increases_risk", magnitude: 0.3238, unit: "log_odds", signed: 0.3238, ...o,
});

const prediction = (o: Partial<Prediction> = {}): Prediction => ({
  model_name: "recovery_risk", model_version: "2.2.0", is_serving_version: true, artifact_sha256: "b668",
  scored_at: "2026-09-21T10:00:00+00:00", as_of_date: "2026-09-21", is_modelled: true, fallback_reason: null,
  p_no_payment: 0.7, p_payment: 0.3, band: "C", points: 520, feature_coverage: 1, coverage_floor: 0.6, n_features: 15,
  stance_recorded: false, reasons: [], contributions: null, scoring_versions: {}, synthetic_warning: "synthetic", ...o,
});

describe("reasonLines", () => {
  it("reads a centred contribution against the book's typical account, never as an absolute cause", () => {
    const [up, down] = reasonLines([
      reason({}),
      reason({ rank: 2, feature: "arrears_ratio", label: "instalments in arrears", value: "2",
               direction: "decreases_risk", magnitude: 0.2321, signed: -0.2321 }),
    ]);
    expect(up).toMatchObject({ what: "Overdue amount: 56,840", effect: "pushes the risk above the book's typical account",
                               raw: "+0.32 log-odds", weight: 1 });
    expect(down).toMatchObject({ what: "Instalments in arrears: 2", effect: "pulls the risk below the book's typical account",
                                 raw: "−0.23 log-odds" });
    expect(down.weight).toBeCloseTo(0.2321 / 0.3238);
  });

  it("orders by rank and reads scorecard reasons as points lost", () => {
    const lines = reasonLines([
      reason({ rank: 2, feature: "cibil_score", label: "cibil_score", unit: "points_lost", magnitude: 27, signed: 27, value: "520" }),
      reason({ rank: 1, feature: "dpd", label: "dpd", unit: "points_lost", magnitude: 31, signed: 31, value: "88" }),
    ]);
    expect(lines.map((l) => l.what)).toEqual(["Dpd: 88", "Cibil_score: 520"]);
    expect(lines[0]).toMatchObject({ effect: "costs 31 scorecard points", raw: "31 points lost" });
  });

  it("drops the value clause when the model logged none", () => {
    expect(reasonLines([reason({ value: null })])[0].what).toBe("Overdue amount");
  });
});

describe("explanationState", () => {
  it("scored: both probabilities, labelled by the caller, from the server's own numbers", () => {
    expect(explanationState(prediction())).toEqual({ kind: "scored", pPayment: "30%", pNoPayment: "70%", band: "C" });
  });

  it("declined: says why, with coverage against the floor, and shows no probability", () => {
    const s = explanationState(prediction({ is_modelled: false, p_no_payment: null, p_payment: null, band: null,
                                            feature_coverage: 0.6, fallback_reason: "only 60% …" }));
    expect(s).toEqual({ kind: "declined", reason: "only 60% …", coverage: "9 of 15 inputs had evidence", floor: "60%" });
  });

  it("unscored when there is no prediction at all", () => {
    expect(explanationState(null)).toEqual({ kind: "unscored" });
  });
});

describe("small readers", () => {
  it("never shows a missing share as zero", () => {
    expect(pct(null)).toBe("—");
    expect(pct(0)).toBe("0%");
    expect(coverageText(null, 15)).toBe("Input coverage not recorded");
    expect(coverageText(0.8, null)).toBe("80% of inputs had evidence");
  });

  it("reconciles intercept + contributions to the logit", () => {
    const r = reconciliation({ intercept: 0.8967, overdue_amount: 0.3238, arrears_ratio: -0.2321, logit: 0.9884 });
    expect(r!.gap).toBeLessThan(1e-9);
    expect(reconciliation({ intercept: 1 })).toBeNull();
    expect(reconciliation(null)).toBeNull();
  });

  const stance = (o: Partial<RecoveryRiskCard["stance"]>): RecoveryRiskCard["stance"] => ({
    feature: "latest_disposition", feature_label: "latest disposition", capture_since: "2026-09-28",
    related_features: ["latest_disposition", "disposition_recency_class"], latest_scoring_day: "2026-09-21", accounts_scored: 1245, accounts_with_stance: 0, share: 0, ...o,
  });

  it("states stance coverage as counts and a share, and has an empty state", () => {
    expect(stanceCoverageText(stance({}))).toBe("0 of 1,245 accounts (0.0%) scored on 2026-09-21 carried a recorded borrower stance.");
    expect(stanceCoverageText(stance({ latest_scoring_day: null, accounts_scored: 0, share: null })))
      .toBe("No scored accounts yet, so there is no coverage to report.");
  });

  it("says plainly that no live figure exists before monitoring is ready", () => {
    const t = monitoringText({ status: "not_ready", required_matured: 500, horizon_days: 30, first_outcomes_mature_from: "2026-10-17" });
    expect(t).toContain("500 outcomes");
    expect(t).toContain("2026-10-17");
    expect(t).toContain("no live Gini or KS exists");
  });
});
