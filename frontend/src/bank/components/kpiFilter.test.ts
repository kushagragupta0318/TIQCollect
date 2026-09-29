import { describe, expect, it } from "vitest";
import { activeCount, filterFromParams, filterToParams, type KpiFilter } from "./kpiFilter";

const p = (s: string) => new URLSearchParams(s);

describe("the global filter in the URL", () => {
  it("round-trips every field", () => {
    const f: KpiFilter = { period: "custom", start: "2026-08-01", end: "2026-08-31", geo: "g1", agency: "a1",
      product: "HOME", bucket: "NPA", security: "SECURED" };
    expect(filterFromParams(filterToParams(f))).toEqual(f);
  });

  it("defaults to month to date and keeps the default out of the URL", () => {
    expect(filterFromParams(p(""))).toEqual({ period: "mtd" });
    expect(filterToParams({ period: "mtd" }).toString()).toBe("");
  });

  it("drops what is malformed rather than guessing", () => {
    expect(filterFromParams(p("period=weekly&security=MAYBE"))).toEqual({ period: "mtd" });
    expect(filterFromParams(p("period=custom&start=2026-09-02&end=2026-09-01"))).toEqual({ period: "mtd" });
    expect(filterFromParams(p("period=custom&start=2026-09-01"))).toEqual({ period: "mtd" });
    expect(filterFromParams(p("period=qtd&start=2026-09-01&end=2026-09-30"))).toEqual({ period: "qtd" });
  });

  it("never sends dates for a period that is not custom", () => {
    expect(filterToParams({ period: "fytd", start: "2026-04-01", end: "2026-09-22" }).toString()).toBe("period=fytd");
  });

  it("counts the narrowing filters, not the period", () => {
    expect(activeCount({ period: "l30" })).toBe(0);
    expect(activeCount({ period: "mtd", agency: "a", product: "GOLD" })).toBe(2);
  });
});
