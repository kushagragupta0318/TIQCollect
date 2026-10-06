import { describe, expect, it } from "vitest";
import {
  bucketLabel, dpdLadderHeatRows, exposureFunnelStages, moneyCr, pct, securityCoverStages,
  transitionMatrixData, type ExposurePanels, type MigrationPanels,
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
