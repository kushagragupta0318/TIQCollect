// @vitest-environment jsdom
// usageCostModel.ts imports api/axios (for getUsage), which touches
// localStorage at module load through the auth store — node has none.
import { describe, expect, it } from "vitest";

import { costCellLabel, featureLabel, formatDay, formatTokens, formatUsd } from "./usageCostModel";

describe("featureLabel", () => {
  it("reads a feature as a label, not as shouting", () => {
    expect(featureLabel("CASE_RANKING")).toBe("Case ranking");
    expect(featureLabel("briefing")).toBe("Briefing");
  });
});

describe("formatTokens", () => {
  it("keeps small counts exact", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(842)).toBe("842");
  });

  it("abbreviates thousands and millions", () => {
    expect(formatTokens(4_500)).toBe("4.5k");
    expect(formatTokens(2_340_000)).toBe("2.34M");
  });
});

describe("formatUsd", () => {
  it("shows sub-cent calls at four decimal places", () => {
    expect(formatUsd(0.0025)).toBe("$0.0025");
  });

  it("shows totals of a dollar or more at two", () => {
    expect(formatUsd(142.5)).toBe("$142.50");
  });

  it("shows exactly zero at two decimal places, not four", () => {
    expect(formatUsd(0)).toBe("$0.00");
  });
});

describe("costCellLabel", () => {
  it("shows a plain cost when nothing in the row is unpriced", () => {
    expect(costCellLabel(0.09, 0)).toBe("$0.09");
  });

  it("says 'unpriced' rather than '$0.00' when every call in the row is unpriced", () => {
    expect(costCellLabel(0, 3)).toBe("unpriced (3)");
  });

  it("shows both a real cost and an unpriced count when the row is mixed", () => {
    expect(costCellLabel(0.09, 2)).toBe("$0.09 +2 unpriced");
  });
});

describe("formatDay", () => {
  it("renders a missing or unparseable date as itself, not a dash", () => {
    expect(formatDay("not a date")).toBe("not a date");
  });

  it("renders a real date as a short label", () => {
    expect(formatDay("2026-10-06")).toMatch(/Oct/);
  });
});
