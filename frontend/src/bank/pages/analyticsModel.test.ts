import { describe, expect, it } from "vitest";
import {
  bucketLabel, complianceBreachRows, costMonthRows, dpdLadderHeatRows, exposureFunnelStages,
  migrationTrajectoryRows, moneyCr, monthLabel, pct, recoveryMonthRows, securityCoverStages,
  transitionMatrixData, type CompliancePanels, type CostPanels, type ExposurePanels,
  type MigrationPanels, type RecoveryPanels,
} from "./analyticsModel";

const exposure: ExposurePanels = {
  funnel: { book: 10e7, delinquent: 4e7, placed: 3e7, npa: 1e7 },
  dpd_ladder: [
    { dpd_bucket: "CURRENT", outstanding: 6e7, accounts: 600 },
    { dpd_bucket: "BUCKET_1", outstanding: 2e7, accounts: 200 },
    { dpd_bucket: "NPA", outstanding: 1e7, accounts: 100 },
  ],
  product_bucket_heat: [
    { product: "PERSONAL", dpd_bucket: "CURRENT", outstanding: 5e7 },
    { product: "PERSONAL", dpd_bucket: "NPA", outstanding: 1e7 },
  ],
  security_cover: [
    { security: "SECURED", outstanding: 7e7 },
    { security: "UNSECURED", outstanding: 3e7 },
  ],
};

describe("Exposure's transforms", () => {
  it("builds a four-stage funnel in crores, book first, NPA last", () => {
    const stages = exposureFunnelStages(exposure);
    expect(stages.map((s) => s.stage)).toEqual(["Whole book", "Delinquent", "Placed", "NPA"]);
    expect(stages[0].exposureCr).toBe(10);
    expect(stages[3].exposureCr).toBe(1);
    expect(stages[3].accounts).toBe(100);
  });

  it("maps the DPD ladder's accounts, not the heat grid's (heat carries no accounts column)", () => {
    const stages = exposureFunnelStages(exposure);
    // delinquent accounts = every non-CURRENT bucket in the ladder (200 + 100)
    expect(stages[1].accounts).toBe(300);
  });

  it("the heat grid's delinquency rate comes from CURRENT vs the product's total, never guessed", () => {
    const rows = dpdLadderHeatRows(exposure);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe("PERSONAL");
    expect(rows[0].delqRatePct).toBeCloseTo((1 / 6) * 100, 1);
    expect(rows[0].cells["Current"].exposureCr).toBe(5);
  });

  it("converts security cover to crores without renaming the SECURED/UNSECURED labels", () => {
    expect(securityCoverStages(exposure)).toEqual([
      { security: "SECURED", exposureCr: 7 },
      { security: "UNSECURED", exposureCr: 3 },
    ]);
  });

  it("maps every DPDBucket name to its display label, and anything unknown passes through", () => {
    expect(bucketLabel("CURRENT")).toBe("Current");
    expect(bucketLabel("BUCKET_3")).toBe("61-90");
    expect(bucketLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});

describe("Migration's transition matrix", () => {
  const migration: MigrationPanels = {
    transition_matrix: [
      { from_state: "CURRENT", to_state: "CURRENT", exposure: 0, accounts: 90 },
      { from_state: "CURRENT", to_state: "SMA_0", exposure: 0, accounts: 10 },
      { from_state: "SMA_0", to_state: "CURRENT", exposure: 0, accounts: 5 },
      { from_state: "SMA_0", to_state: "RESOLVED", exposure: 0, accounts: 5 },     // outside the square grid
    ],
    trajectory_12m: [],
  };

  it("every row sums to 100 (a probability row), RESOLVED excluded from the square", () => {
    const { labels, matrix } = transitionMatrixData(migration);
    expect(labels).toEqual(["Current", "0-30", "31-60", "61-90", "90-180", "180+"]);
    const currentRow = matrix[labels.indexOf("Current")];
    expect(currentRow.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 5);
    expect(currentRow[labels.indexOf("Current")]).toBeCloseTo(90, 1);
    expect(currentRow[labels.indexOf("0-30")]).toBeCloseTo(10, 1);
  });

  it("an exit to RESOLVED is dropped from BOTH the row's total and the count, so the footnote matches the grid", () => {
    const { labels, matrix, observed } = transitionMatrixData(migration);
    const smaRow = matrix[labels.indexOf("0-30")];
    // SMA_0 -> CURRENT (5) is the only square-to-square move from SMA_0;
    // SMA_0 -> RESOLVED (5) is a real exit, excluded from this row's total
    // too, so the row reads 100% to Current, not 50%.
    expect(smaRow[labels.indexOf("Current")]).toBeCloseTo(100, 1);
    expect(observed).toBe(105);           // 90 + 10 + 5 square-to-square moves; the exit (5) not counted
  });

  it("a from-state never seen produces an all-zero row, not a crash", () => {
    const { labels, matrix } = transitionMatrixData({ transition_matrix: [], trajectory_12m: [] });
    expect(matrix).toHaveLength(labels.length);
    expect(matrix.every((row) => row.every((v) => v === 0))).toBe(true);
  });
});

describe("formatting", () => {
  it("abstains honestly rather than showing 0%", () => {
    expect(pct(null)).toBe("Not available");
    expect(pct(0.294)).toBe("29.4%");
    expect(pct(0)).toBe("0.0%");
  });

  it("picks crore or lakh by magnitude, like the backend's own money()", () => {
    expect(moneyCr(2.5e7)).toBe("₹2.50 Cr");
    expect(moneyCr(4.5e5)).toBe("₹4.50 L");
    expect(moneyCr(null)).toBe("₹0.00 L");
  });
});

// ── the chart rows (owner: "charts/graphs/aggregations should be there") ────
// The rule every one of these guards: a missing figure is a GAP, not a zero.
// The transform omits the key, so Recharts draws nothing there; a 0 would draw
// a floor and read as "we collected nothing" instead of "we do not know".

describe("month labels", () => {
  it("reads the month off the digits, not through new Date()", () => {
    // A bare ISO date through `new Date()` is UTC midnight, which prints the
    // month BEFORE in any timezone behind UTC -- the bug known issue 3 lists
    // on nine other frontend reads. Jan 1 is the one that exposes it.
    expect(monthLabel("2026-01-01")).toBe("Jan 26");
    expect(monthLabel("2026-09-30")).toBe("Sep 26");
    expect(monthLabel("2026-12-31")).toBe("Dec 26");
  });

  it("hands back anything it cannot parse rather than inventing a month", () => {
    expect(monthLabel("not-a-date")).toBe("not-a-date");
  });
});

describe("Migration's trajectory rows", () => {
  const panels: MigrationPanels = {
    transition_matrix: [],
    trajectory_12m: [
      { month_end: "2026-08-31", cured: 30, held: 50, rolled: 20 },
      { month_end: "2026-09-30", cured: 0, held: 0, rolled: 0 },
    ],
  };

  it("carries the three counts and derives the cure RATE the counts cannot show", () => {
    const [aug] = migrationTrajectoryRows(panels);
    expect(aug.month).toBe("Aug 26");
    expect([aug.cured, aug.held, aug.rolled]).toEqual([30, 50, 20]);
    expect(aug.cure_rate_pct).toBe(30);        // 30 of 100 accounts
  });

  it("omits the cure rate for a month with no accounts instead of charting 0%", () => {
    const [, sep] = migrationTrajectoryRows(panels);
    expect("cure_rate_pct" in sep).toBe(false);
  });
});

describe("Recovery's chart rows", () => {
  const panels: RecoveryPanels = {
    by_month: [
      { month_start: "2026-08-01", actual_inr: 2.5e7, expected_inr: 2e7, recovery_vs_expected: 1.25 },
      { month_start: "2026-09-01", actual_inr: 1e7, expected_inr: null, recovery_vs_expected: null },
    ],
  };

  it("puts both INR series in crores, so one axis can carry them", () => {
    const [aug] = recoveryMonthRows(panels);
    expect(aug.actual_cr).toBe(2.5);
    expect(aug.expected_cr).toBe(2);
    expect(aug.vs_expected_pct).toBe(125);
  });

  it("charts NO expected bar for a month the model abstained on", () => {
    // The model did not expect zero; it said nothing. A zero bar would read
    // as a prediction of no recovery at all.
    const [, sep] = recoveryMonthRows(panels);
    expect(sep.actual_cr).toBe(1);
    expect("expected_cr" in sep).toBe(false);
    expect("vs_expected_pct" in sep).toBe(false);
  });
});

describe("Cost's chart rows", () => {
  const panels: CostPanels = {
    by_month: [
      { month_start: "2026-08-01", commission_inr: 1e7, field_cost_inr: 5e6, collected_inr: 4e8, cost_per_100_inr: 3.75 },
      { month_start: "2026-09-01", commission_inr: 2e7, field_cost_inr: null, collected_inr: 5e8, cost_per_100_inr: null },
    ],
  };

  it("charts commission and field cost in crores with the ratio as the line", () => {
    const [aug] = costMonthRows(panels);
    expect(aug.commission_cr).toBe(1);
    expect(aug.field_cost_cr).toBe(0.5);
    expect(aug.cost_per_100).toBe(3.75);
  });

  it("omits an unrecorded field cost rather than drawing it as free", () => {
    const [, sep] = costMonthRows(panels);
    expect("field_cost_cr" in sep).toBe(false);
    expect("cost_per_100" in sep).toBe(false);
  });
});

describe("Compliance's chart rows", () => {
  it("totals exactly the four charted types, so the line cannot disagree with the bars", () => {
    const panels: CompliancePanels = {
      breaches_over_time: [
        { month_start: "2026-09-01", out_of_hours: 3, geofence: 2, consent_missing: 1, fraud_confirmed: 4 },
      ],
      by_agency: [],
    };
    const [row] = complianceBreachRows(panels);
    expect(row.total).toBe(10);
    expect(row.out_of_hours).toBe(3);
    expect(row.month).toBe("Sep 26");
  });
});
