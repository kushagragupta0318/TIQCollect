// The gallery's sample book claims to be internally consistent — these pin it,
// so a parity screenshot never shows a ladder that disagrees with its funnel.
import { describe, expect, it } from "vitest";
import {
  COVERAGE_PCT_BY_BUCKET,
  DPD_LADDER,
  FUNNEL,
  HEAT_GRID,
  KPI_ROWS,
  KPIS,
  RECOVERY_CURVE,
  RECOVERY_SUMMARY,
  TRANSITION_MATRIX,
  CURE_ROLL,
} from "./gallerySample";

const sum = (xs: number[]) => Math.round(xs.reduce((s, x) => s + x, 0) * 10) / 10;

describe("gallery sample book", () => {
  it("the DPD ladder, the funnel and the heat grid agree on delinquent exposure and accounts", () => {
    const delinquent = FUNNEL[1];
    expect(sum(DPD_LADDER.map((b) => b.exposureCr))).toBe(delinquent.exposureCr);
    expect(sum(DPD_LADDER.map((b) => b.accounts))).toBe(delinquent.accounts);
    expect(sum(HEAT_GRID.map((r) => r.delqExposureCr))).toBe(delinquent.exposureCr);
    expect(sum(DPD_LADDER.map((b) => b.sharePct))).toBe(100);
  });

  it("every transition-matrix row sums to 100, and every cure/roll split too", () => {
    for (const row of TRANSITION_MATRIX) expect(sum(row)).toBe(100);
    for (const f of CURE_ROLL) expect(sum([f.curePct, f.improvePct, f.holdPct, f.rollPct])).toBe(100);
  });

  it("daily collections sum to the achieved figure; the target pace ends at the target", () => {
    expect(RECOVERY_CURVE).toHaveLength(30);
    expect(RECOVERY_CURVE.at(-1)?.cumulative).toBe(RECOVERY_SUMMARY.achievedCr);
    expect(RECOVERY_CURVE.at(-1)?.targetPace).toBe(RECOVERY_SUMMARY.targetCr);
    expect(RECOVERY_CURVE.at(-1)?.date).toBe("22 Sep");
  });

  it("every ladder bucket has a coverage figure, and coverage falls with DPD", () => {
    const coverage = DPD_LADDER.map((b) => COVERAGE_PCT_BY_BUCKET[b.bucket]);
    for (const c of coverage) expect(c).toBeGreaterThan(0);
    expect([...coverage].sort((a, b) => b - a)).toEqual(coverage);
  });

  it("the KPI rows name twelve KPIs that all exist", () => {
    const ids = KPI_ROWS.flatMap((r) => r.kpis);
    expect(ids).toHaveLength(12);
    expect(new Set(ids).size).toBe(12);
    for (const id of ids) expect(KPIS.find((k) => k.id === id)).toBeDefined();
  });
});
