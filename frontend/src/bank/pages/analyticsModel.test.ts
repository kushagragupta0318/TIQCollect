import { describe, expect, it } from "vitest";
import {
  agencyLeaderboardRows, bucketLabel, complianceChartRows, concentrationBranchChartRows,
  concentrationCityChartRows, concentrationProductChartRows, costByAgencyChartRows, costMonthlyChartRows,
  dpdLadderHeatRows, exposureFunnelStages, fieldOpsAgentChartRows, fieldOpsBeatAdherenceChartRows,
  fieldOpsSlaTrend, fieldOpsVisitsChartRows, moneyCr, pct, recoveryChartRows, securityCoverStages, shortDay,
  shortMonth, transitionMatrixData, type AgenciesPanels, type CompliancePanels, type ConcentrationPanels,
  type CostPanels, type ExposurePanels, type FieldOpsPanels, type MigrationPanels, type RecoveryPanels,
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

  it("shortMonth collapses to month+year; shortDay keeps the day (different x-axis grains)", () => {
    expect(shortMonth("2026-07-01")).toBe("Jul 26");
    expect(shortDay("2026-07-14")).toBe("14 Jul");
    expect(shortMonth("not-a-date")).toBe("not-a-date");
  });
});

describe("Recovery's chart rows", () => {
  const recovery: RecoveryPanels = {
    by_month: [
      { month_start: "2026-08-01", actual_inr: 5e7, expected_inr: 6e7, recovery_vs_expected: -0.167 },
      { month_start: "2026-09-01", actual_inr: 4e7, expected_inr: null, recovery_vs_expected: null },
    ],
  };

  it("scales actual and expected to crores, by month", () => {
    const rows = recoveryChartRows(recovery);
    expect(rows[0]).toEqual({ month: "Aug 26", actual_cr: 5, expected_cr: 6 });
  });

  it("leaves expected_cr unset on a month the model abstained, rather than a fabricated 0", () => {
    const rows = recoveryChartRows(recovery);
    expect(rows[1]).toEqual({ month: "Sept 26", actual_cr: 4 });
    expect("expected_cr" in rows[1]).toBe(false);
  });
});

describe("Agencies' leaderboard", () => {
  const base = {
    agency_id: "a", code: "AGY", name: "Agency", status: "ACTIVE", n_rows: 10, resolution_rate: null,
    ptp_conversion: null, contact_rate: null, sla_adherence: null, productivity_per_agent_per_day: null,
    cost_per_100_inr: null, evidence_integrity_per_100_visits: null, workforce_active_ratio: null,
    workforce_attrition_ratio: null, workforce_leave_rate: null, compliance_score: null,
  };
  const panels: AgenciesPanels = {
    scorecards: [
      { ...base, agency_id: "a1", code: "ALPHA", collection_efficiency: 0.60, recovery_vs_expected: 0.90 },
      { ...base, agency_id: "a2", code: "BETA", collection_efficiency: 0.85, recovery_vs_expected: null },
      { ...base, agency_id: "a3", code: "GAMMA", collection_efficiency: null, recovery_vs_expected: 0.5 },
    ],
  };

  it("ranks by collection efficiency descending, as percentages", () => {
    const rows = agencyLeaderboardRows(panels);
    expect(rows.map((r) => r.code)).toEqual(["BETA", "ALPHA"]);
    expect(rows[0].collection_efficiency_pct).toBe(85);
  });

  it("drops an agency with no efficiency reading — it can't be ranked, not scored as last", () => {
    const rows = agencyLeaderboardRows(panels);
    expect(rows.find((r) => r.code === "GAMMA")).toBeUndefined();
  });

  it("leaves recovery_vs_expected_pct unset where that agency has no reading", () => {
    const rows = agencyLeaderboardRows(panels);
    const beta = rows.find((r) => r.code === "BETA")!;
    expect("recovery_vs_expected_pct" in beta).toBe(false);
  });
});

describe("Cost to Collect's chart rows", () => {
  const panels: CostPanels = {
    by_month: [
      { month_start: "2026-08-01", commission_inr: 2e7, field_cost_inr: 1e7, collected_inr: 10e7, cost_per_100_inr: 3 },
      { month_start: "2026-09-01", commission_inr: 2.2e7, field_cost_inr: null, collected_inr: 11e7, cost_per_100_inr: null },
    ],
    by_agency: [
      { agency_id: "a1", agency_name: "Alpha", commission_inr: 2e7, field_cost_inr: 1e7, collected_inr: 10e7, cost_per_100_inr: 3 },
      { agency_id: "a2", agency_name: "Beta", commission_inr: 1e7, field_cost_inr: null, collected_inr: 5e7, cost_per_100_inr: null },
    ],
  };

  it("field cost missing for a month draws no bar for that leg, not a 0", () => {
    const rows = costMonthlyChartRows(panels);
    expect(rows[1]).toEqual({ month: "Sept 26", commission_cr: 2.2 });
  });

  it("by-agency drops an agency with no cost_per_100 reading", () => {
    const rows = costByAgencyChartRows(panels);
    expect(rows).toEqual([{ agency_name: "Alpha", cost_per_100: 3, collected_cr: 10 }]);
  });
});

describe("Compliance's breach trend", () => {
  it("every category is a real count — no abstain case, nothing filtered", () => {
    const panels: CompliancePanels = {
      breaches_over_time: [
        { month_start: "2026-08-01", out_of_hours: 3, geofence: 1, consent_missing: 0, fraud_confirmed: 0 },
      ],
      by_agency: [],
    };
    expect(complianceChartRows(panels)).toEqual([
      { month: "Aug 26", out_of_hours: 3, geofence: 1, consent_missing: 0, fraud_confirmed: 0 },
    ]);
  });
});

describe("Field Ops' two grains", () => {
  const panels: FieldOpsPanels = {
    by_day: [
      { day: "2026-10-05", visits: 40, met_visits: 30, met_rate_pct: 75, planned_stops: 10, visited_stops: 9,
        beat_adherence_pct: 90, planned_km: 120, actual_km: 110 },
      { day: "2026-10-06", visits: 20, met_visits: 10, met_rate_pct: 50, planned_stops: 0, visited_stops: 0,
        beat_adherence_pct: null, planned_km: 0, actual_km: null },
    ],
    by_agent: [
      { agent_id: "ag1", agent_name: "Ravi Kumar", visits: 60, met_rate_pct: 70, beat_adherence_pct: 88 },
    ],
    sla_by_agency: [
      { agency_id: "a1", agency_name: "Alpha", month_start: "2026-08-01", sla_adherence_pct: 92 },
      { agency_id: "a2", agency_name: "Beta", month_start: "2026-08-01", sla_adherence_pct: null },
      { agency_id: "a1", agency_name: "Alpha", month_start: "2026-09-01", sla_adherence_pct: 88 },
    ],
  };

  it("visits chart carries met rate alongside, by day", () => {
    expect(fieldOpsVisitsChartRows(panels)[0]).toEqual({ day: "5 Oct", visits: 40, met_rate_pct: 75 });
  });

  it("beat adherence leaves off a day with no routed beat, rather than plotting a 0", () => {
    const rows = fieldOpsBeatAdherenceChartRows(panels);
    expect(rows).toEqual([{ day: "5 Oct", beat_adherence_pct: 90 }]);
  });

  it("by-agent carries visits and met rate, summed over the window", () => {
    expect(fieldOpsAgentChartRows(panels)).toEqual([{ agent_name: "Ravi Kumar", visits: 60, met_rate_pct: 70 }]);
  });

  it("SLA trend pivots to one series per agency, monthly — Beta's no-reading month leaves a gap, not a 0", () => {
    const { rows, series } = fieldOpsSlaTrend(panels);
    expect(series.map((s) => s.name)).toEqual(["Alpha", "Beta"]);
    expect(rows).toEqual([
      { month: "Aug 26", a1: 92 },
      { month: "Sept 26", a1: 88 },
    ]);
  });
});

describe("Concentration's charted levels", () => {
  const panels: ConcentrationPanels = {
    by_city: [
      { id: "c1", name: "Pune", exposure: 5e7, npa_exposure: 1e7, accounts: 500, npa_accounts: 50 },
      { id: "c2", name: "Nashik", exposure: 2e7, npa_exposure: 0.5e7, accounts: 200, npa_accounts: 10 },
    ],
    by_state: [], by_region: [], by_zone: [],
    by_product: [
      { product: "PERSONAL", exposure: 3e7, npa_exposure: 1e7, accounts: 300, npa_accounts: 30 },
      { product: "HOME", exposure: 7e7, npa_exposure: 0.5e7, accounts: 100, npa_accounts: 5 },
    ],
    by_branch: [
      { branch_code: "PUN01", branch_name: "Pune Camp", exposure: 2e7, loan_count: 150 },
      { branch_code: "NSK01", branch_name: "Nashik Road", exposure: 4e7, loan_count: 300 },
    ],
    concentration_ratios: { top_5_cities_share_pct: 62.5, top_5_branches_share_pct: 48.1 },
  };

  it("cities arrive already sorted — charted as-is, scaled to crores", () => {
    expect(concentrationCityChartRows(panels)).toEqual([
      { name: "Pune", exposure_cr: 5, npa_exposure_cr: 1 },
      { name: "Nashik", exposure_cr: 2, npa_exposure_cr: 0.5 },
    ]);
  });

  it("products are sorted by exposure descending (not guaranteed sorted on the wire)", () => {
    expect(concentrationProductChartRows(panels).map((r) => r.name)).toEqual(["HOME", "PERSONAL"]);
  });

  it("branches sort by exposure descending and carry loan count, never a fabricated NPA split", () => {
    const rows = concentrationBranchChartRows(panels);
    expect(rows.map((r) => r.name)).toEqual(["Nashik Road", "Pune Camp"]);
    expect(rows[0]).toEqual({ name: "Nashik Road", exposure_cr: 4, loan_count: 300 });
    expect("npa_exposure_cr" in rows[0]).toBe(false);
  });
});
