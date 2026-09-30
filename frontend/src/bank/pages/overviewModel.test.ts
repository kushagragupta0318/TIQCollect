import { describe, expect, it } from "vitest";
import { coverage, frameLabel, narrativeCaption, type OverviewKpi } from "./overviewModel";

const kpi = (available: boolean): OverviewKpi => ({
  id: "x", label: "X", value: available ? "₹1.0 Cr" : "—", sub: "", trend: "", trendUp: null, good: null,
  basis: "b", drill: "d", available, reason: available ? null : "analytics view pending",
});

describe("the Overview's pure decisions", () => {
  it("labels the reading date the way the server's frames read, or says there is none", () => {
    expect(frameLabel("2026-09-22")).toBe("Reading of 22 Sep 2026");
    expect(frameLabel("2026-03-01")).toBe("Reading of 01 Mar 2026");
    expect(frameLabel(null)).toBe("No reading yet");
  });

  it("earns the live dot only when every card carries a figure", () => {
    expect(coverage([kpi(true), kpi(true)])).toEqual({ available: 2, total: 2, complete: true });
    expect(coverage([kpi(true), kpi(false)]).complete).toBe(false);
    expect(coverage([]).complete).toBe(false);
  });

  it("never presents rule-based text as AI", () => {
    expect(narrativeCaption("rules")).toContain("not by AI");
    expect(narrativeCaption("rules").toLowerCase()).not.toMatch(/\bai-generated\b|\bai summary\b/);
  });
});
