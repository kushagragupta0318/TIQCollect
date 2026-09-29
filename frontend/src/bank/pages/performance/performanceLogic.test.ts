import { describe, expect, it } from "vitest";
import {
  formatCostPer100, formatFraudPer100, formatIndex, formatPerAgentPerDay, formatRatioPercent,
  indexEvidenceCaption, percentBarWidth, rankLeaderboard,
} from "./performanceLogic";
import type { PerformanceIndex } from "@/api/bank";

function idx(overrides: Partial<PerformanceIndex>): PerformanceIndex {
  return {
    agency_id: "a1", region_id: null, month_start: "2026-09-01", months: 1, n: 6, months_unread: 0,
    raw_rate: 0.5, peer_rate: 0.5, shrunk_rate: 0.5, index: 72, multiplier: 1, version: "agency-effect-1.0.0",
    ...overrides,
  };
}

describe("formatRatioPercent — null vs zero, and no clamping", () => {
  it("null renders as an em dash, never 0%", () => {
    expect(formatRatioPercent(null)).toBe("—");
  });

  it("a real zero renders as 0.0%, distinct from null", () => {
    expect(formatRatioPercent(0)).toBe("0.0%");
  });

  it("a normal 0-1 fraction renders as a percentage", () => {
    expect(formatRatioPercent(0.834)).toBe("83.4%");
  });

  it("does NOT clamp a fraction over 1 — recovery_vs_expected can legitimately exceed 100%", () => {
    expect(formatRatioPercent(1.42)).toBe("142.0%");
  });

  it("respects a custom digit count", () => {
    expect(formatRatioPercent(0.5, 0)).toBe("50%");
  });
});

describe("formatIndex — the headline number", () => {
  it("null is 'not enough data', never 0", () => {
    expect(formatIndex(null)).toBe("—");
  });

  it("rounds to a whole number", () => {
    expect(formatIndex(71.6)).toBe("72");
  });

  it("a real zero index is distinct from null", () => {
    expect(formatIndex(0)).toBe("0");
  });
});

describe("formatCostPer100 — null means unconfigured, not free", () => {
  it("null renders as 'Not configured'", () => {
    expect(formatCostPer100(null)).toBe("Not configured");
  });

  it("a real value renders in rupees per ₹100", () => {
    expect(formatCostPer100(8.5)).toBe("₹8.50 per ₹100");
  });
});

describe("formatPerAgentPerDay", () => {
  it("null renders as an em dash", () => {
    expect(formatPerAgentPerDay(null)).toBe("—");
  });

  it("formats a plain count, not a percentage", () => {
    expect(formatPerAgentPerDay(2.4)).toBe("2.4 visits/agent/day");
  });
});

describe("formatFraudPer100 — lower is better, 0 is a real good value", () => {
  it("null (no visits recorded) renders as an em dash", () => {
    expect(formatFraudPer100(null)).toBe("—");
  });

  it("a real zero (no confirmed fraud) renders as 0.00, not '—'", () => {
    expect(formatFraudPer100(0)).toBe("0.00 per 100 visits");
  });
});

describe("percentBarWidth — the bar visually caps at 100, the number does not", () => {
  it("null is an empty bar", () => {
    expect(percentBarWidth(null)).toBe(0);
  });

  it("a normal fraction scales to a percentage width", () => {
    expect(percentBarWidth(0.6)).toBe(60);
  });

  it("caps a fraction over 1 at 100 for the bar, while the text stays uncapped", () => {
    expect(percentBarWidth(1.42)).toBe(100);
    expect(formatRatioPercent(1.42)).toBe("142.0%");
  });
});

describe("indexEvidenceCaption", () => {
  it("no matured evidence", () => {
    expect(indexEvidenceCaption(0)).toBe("No matured evidence yet");
  });

  it("singular for exactly one", () => {
    expect(indexEvidenceCaption(1)).toBe("Based on 1 placement-month");
  });

  it("plural otherwise", () => {
    expect(indexEvidenceCaption(6)).toBe("Based on 6 placement-months");
  });
});

describe("rankLeaderboard — unscored agencies get no rank, not a tied one", () => {
  it("empty leaderboard", () => {
    expect(rankLeaderboard([])).toEqual([]);
  });

  it("ranks scored rows in the order given (server already sorted)", () => {
    const rows = [idx({ agency_id: "a1", index: 90 }), idx({ agency_id: "a2", index: 60 })];
    const ranked = rankLeaderboard(rows);
    expect(ranked.map((r) => [r.agency_id, r.rank])).toEqual([["a1", 1], ["a2", 2]]);
  });

  it("an unscored row (index: null) gets rank: null, not counted into the sequence", () => {
    const rows = [
      idx({ agency_id: "a1", index: 90 }),
      idx({ agency_id: "a2", index: null, n: 0 }),
      idx({ agency_id: "a3", index: 60 }),
    ];
    const ranked = rankLeaderboard(rows);
    expect(ranked.map((r) => [r.agency_id, r.rank])).toEqual([["a1", 1], ["a2", null], ["a3", 2]]);
  });
});
