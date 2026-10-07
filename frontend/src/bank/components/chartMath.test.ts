// percentileDomain (PercentileRangeChart's axis math). The bug this guards
// against: the owner saw the "Gross NPA at the horizon" chart render as a
// blank band — no bar visible at all, just the axis. The domain had been
// computed from p95 alone, which assumed p95 is always a row's largest
// value. A real simulate response doesn't guarantee that (percentiles
// SHOULD be monotonic p5<=p10<=p50<=p90<=p95, but a chart that trusts that
// and goes blank the one time it isn't is a worse failure than a chart
// that's merely cautious about it).
import { describe, expect, it } from "vitest";
import { percentileDomain, type PercentileRow } from "./chartMath";

const row = (overrides: Partial<PercentileRow>): PercentileRow =>
  ({ name: "x", p5: 10, p10: 11, p50: 14, p90: 16, p95: 17, ...overrides });

describe("percentileDomain", () => {
  it("covers the true max even when p95 isn't the largest field", () => {
    // The exact shape that produced the blank chart: p95 below p50.
    const [, max] = percentileDomain([row({ p5: 10.6, p10: 11.3, p50: 13.9, p90: 16.6, p95: 11.14 })], true);
    expect(max).toBeGreaterThanOrEqual(13.9);
  });

  it("covers the true min even when p5 isn't the smallest field (tight-fit domain)", () => {
    const [min] = percentileDomain([row({ p5: 50, p10: 40, p50: 45, p90: 48, p95: 49 })], false);
    expect(min).toBeLessThanOrEqual(40);
  });

  it("is 0-anchored only when domainFrom0 is true", () => {
    const [minTrue] = percentileDomain([row({})], true);
    const [minFalse] = percentileDomain([row({})], false);
    expect(minTrue).toBe(0);
    expect(minFalse).toBeGreaterThan(0);
  });

  it("spans every row, not just the first, across multiple metrics", () => {
    const [min, max] = percentileDomain(
      [row({ p5: 1, p95: 5 }), row({ p5: 100, p95: 500 })],
      false,
    );
    expect(min).toBeLessThanOrEqual(1);
    expect(max).toBeGreaterThanOrEqual(500);
  });

  it("never produces an inverted domain on a degenerate (all-equal) band", () => {
    const [min, max] = percentileDomain([row({ p5: 0, p10: 0, p50: 0, p90: 0, p95: 0 })], false);
    expect(max).toBeGreaterThanOrEqual(min);
  });
});
