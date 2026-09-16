/**
 * Today's beat cases by DPD: DPD order, validated colours, nothing invented.
 */
import { describe, expect, it } from "vitest";
import { DPD_COLOUR, todayDpdSlices } from "./todayDpd";

// manager1's beats for 2026-09-16, as GET /manager/dashboard returned them.
const LIVE = [
  { bucket: "NPA",      case_count: 50, target_amount: 1691000.0, collectable_amount: 1353964.92 },
  { bucket: "BUCKET_2", case_count: 92, target_amount: 2825950.0, collectable_amount: 2556533.06 },
  { bucket: "BUCKET_3", case_count: 72, target_amount: 2208400.0, collectable_amount: 1904232.49 },
];

describe("todayDpdSlices", () => {
  it("orders by DPD, not by the order the API returned", () => {
    expect(todayDpdSlices(LIVE).map((s) => s.bucket)).toEqual(["BUCKET_2", "BUCKET_3", "NPA"]);
  });

  it("counts sum to the day's beat cases — 214 on the live run", () => {
    expect(todayDpdSlices(LIVE).reduce((t, s) => t + s.case_count, 0)).toBe(214);
  });

  it("carries collectable and target through untouched", () => {
    const b2 = todayDpdSlices(LIVE).find((s) => s.bucket === "BUCKET_2")!;
    expect(b2.collectable_amount).toBe(2556533.06);
    expect(b2.target_amount).toBe(2825950.0);
  });

  it("labels buckets as a manager reads them, with a short form for the ring's centre", () => {
    const s = todayDpdSlices(LIVE);
    expect(s.map((x) => x.label)).toEqual(["31–60 DPD", "61–90 DPD", "NPA (90+)"]);
    expect(s.map((x) => x.shortLabel)).toEqual(["31–60 dpd", "61–90 dpd", "npa 90+"]);
  });

  it("drops zero-count buckets and survives an absent payload", () => {
    expect(todayDpdSlices([...LIVE, { bucket: "CURRENT", case_count: 0, target_amount: 0, collectable_amount: 0 }])).toHaveLength(3);
    expect(todayDpdSlices([])).toEqual([]);
    expect(todayDpdSlices(null)).toEqual([]);
    expect(todayDpdSlices(undefined)).toEqual([]);
  });

  it("uses the validated palette, in DPD order", () => {
    // Every adjacent pair passed the dataviz checker on light and dark, 2026-09-16.
    expect(Object.values(DPD_COLOUR)).toEqual(["#059669", "#0284C7", "#D97706", "#B91C1C", "#6D28D9"]);
    expect(todayDpdSlices(LIVE).map((s) => s.colour)).toEqual(["#D97706", "#B91C1C", "#6D28D9"]);
  });

  it("falls back sanely on a bucket it has never heard of", () => {
    const [x] = todayDpdSlices([{ bucket: "BUCKET_9", case_count: 1, target_amount: 0, collectable_amount: 0 }]);
    expect(x.label).toBe("BUCKET 9");
    expect(x.colour).toBe("#64748B");
  });
});
