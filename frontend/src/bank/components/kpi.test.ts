import { describe, expect, it } from "vitest";
import { trendDisplay } from "./kpi";

describe("trendDisplay — arrow is direction, colour is whether that is good", () => {
  it("rising and bad reads up in red (rising NPA)", () => {
    expect(trendDisplay({ trend: "+0.2 pp vs last month", trendUp: true, good: false })).toEqual({
      direction: "up",
      tone: "destructive",
      className: "text-destructive",
    });
  });

  it("falling and good reads down in green (unworked exposure down)", () => {
    expect(trendDisplay({ trend: "-6.2% MoM", trendUp: false, good: true })).toEqual({
      direction: "down",
      tone: "success",
      className: "text-success",
    });
  });

  it("good == null is flat whatever the delta (a target does not improve)", () => {
    expect(trendDisplay({ trend: "+4.0% MoM", trendUp: true, good: null }).direction).toBe("flat");
  });

  it("a zero delta in pp or % is flat and muted", () => {
    for (const trend of ["+0.0 pp vs last month", "0 pp", "-0.0% MoM", "+0%"]) {
      expect(trendDisplay({ trend, trendUp: true, good: true })).toEqual({
        direction: "flat",
        tone: "muted",
        className: "text-muted-foreground",
      });
    }
  });

  it("a small but non-zero delta is not flat", () => {
    expect(trendDisplay({ trend: "+0.04 pp", trendUp: true, good: true }).direction).toBe("up");
    expect(trendDisplay({ trend: "+0.1 pp", trendUp: true, good: true }).direction).toBe("up");
  });
});
