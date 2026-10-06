/**
 * The simulator's display rules (simulatorModel.ts).
 *
 * The figures below are a real run of the engine (mc-1.2.0, 1,500 synthetic
 * loans, 500 paths, 12 months, Adverse), so the rounding assertions are made
 * against the Monte Carlo error the engine actually reports rather than numbers
 * chosen to make a rule look good.
 */
import { describe, expect, it } from "vitest";
import type { Band, SimulationRun } from "@/api/bankStrategy";
import {
  BAND_NOTE, LEVER_DEFAULTS, bandRange, changedLevers, decimalsFor, fractionPct, headlineTiles,
  ifrs9Rows, leverErrors, metricValue, requestFor, runSummary, scenarioChanges, stateShareRows,
  DEFAULT_FORM,
} from "./simulatorModel";

const band = (p50: number, sem: number, spread = 0.2): Band => ({
  p5: p50 * (1 - spread * 1.5), p10: p50 * (1 - spread), p50,
  p90: p50 * (1 + spread), p95: p50 * (1 + spread * 1.5), mean: p50, sem,
});

const run = (o: Partial<SimulationRun> = {}): SimulationRun => ({
  engine_version: "mc-1.2.0", seed: 0, n_paths: 500, horizon_months: 12, numpy_version: "2.4.0",
  subsampled: false, synthetic_inputs: true, calibrated_by_backtest: false,
  synthetic_warning: "SYNTHETIC: …", assumptions: ["Transition matrices are Dirichlet posteriors."],
  scenario: { name: "Adverse", gdp: -1.5, cpi: 1.2, repo_bps: 50, unemployment: 1.0, sector: -2.0 },
  metrics: {
    GNPA_PCT: { p5: 10.6447, p10: 11.3051, p50: 13.8564, p90: 16.607, p95: 17.386, mean: 13.9078, sem: 0.09268 },
    RECOVERED_CASH: { p5: 37230566.9, p10: 40173159.6, p50: 47559266.9, p90: 58793000.1, p95: 64354708.2, mean: 48869150.2, sem: 362162.8 },
    NET_RECOVERY: band(39000000, 300000),
    WRITE_OFFS: { p5: 7154476, p10: 8865583, p50: 14744985, p90: 20893276, p95: 21835675, mean: 14669977, sem: 206000 },
    SETTLEMENT_CASH: { p5: 0, p10: 0, p50: 0, p90: 0, p95: 0, mean: 0, sem: 0 },
    COST: band(8500000, 90000), ECL: band(62000000, 400000),
    WRITE_OFF_ACCOUNTS: band(412, 4), RECOVERED_ACCOUNTS: band(638, 5), NPA_ACCOUNTS: band(1211, 7),
    STATE_SHARE: {
      CURRENT: { p5: 52.87, p10: 53.93, p50: 56.73, p90: 59.74, p95: 60.54, mean: 56.68, sem: 0.1063 },
      SMA_0: { p5: 5.13, p10: 5.53, p50: 6.53, p90: 7.73, p95: 8.0, mean: 6.58, sem: 0.0387 },
      NPA_SUB: band(9.4, 0.05), WRITTEN_OFF: band(4.1, 0.03),
    },
  } as SimulationRun["metrics"],
  recovery_at_risk: 33451279.9,
  ifrs9: {
    stage1: { EAD: band(1043885508, 1655030), ECL: band(41381821, 186694), COVERAGE: band(0.0398132, 0.00015481), PD: band(0.0663554, 0.000258) },
    stage2: { EAD: band(214000000, 900000), ECL: band(31000000, 150000), COVERAGE: band(0.1449, 0.0004), PD: band(0.2415, 0.0007) },
    stage3: { EAD: band(96000000, 700000), ECL: band(54000000, 320000), COVERAGE: band(0.5625, 0.0012), PD: band(0.75, 0.0016) },
  },
  synthetic: true, calibrated: false, data_version: "2026-09-30",
  basis: "1,500 synthetic loans at 2026-09-30, transitions from 24 month-ends",
  text: "SYNTHETIC: … UNCALIBRATED: … Basis: … (engine mc-1.2.0, data 2026-09-30).",
  ...o,
});

describe("precision follows the run's own Monte Carlo error", () => {
  it("keeps a digit only while it is larger than twice the noise", () => {
    expect(decimalsFor(0.09268)).toBe(1);        // GNPA: ±0.09 pp, so 13.9%
    expect(decimalsFor(0.00039, 1, 2)).toBe(2);  // tiny noise, still capped at 2
    expect(decimalsFor(0.6)).toBe(0);            // noisier than a tenth: no decimals
    expect(decimalsFor(0, 1, 2, 1)).toBe(1);     // deterministic: the fallback
    expect(decimalsFor(Number.NaN)).toBe(1);
  });

  it("prints a percent, a count and money each in its own unit", () => {
    const r = run();
    expect(metricValue("PCT", r.metrics.GNPA_PCT.p50, r.metrics.GNPA_PCT.sem)).toBe("13.9%");
    expect(metricValue("COUNT", 1211.4, 7)).toBe("1,211");
    // ±₹3.6 L of noise on ₹4.76 Cr earns both decimals.
    expect(metricValue("INR", r.metrics.RECOVERED_CASH.p50, r.metrics.RECOVERED_CASH.sem)).toBe("₹4.76 Cr");
    // A noisy money figure loses the digit the paths cannot support.
    expect(metricValue("INR", 47559266.9, 9_000_000)).toBe("₹5 Cr");
  });

  it("reads a band as p10 to p90, in the same precision", () => {
    expect(bandRange("PCT", run().metrics.GNPA_PCT)).toBe("11.3% to 16.6%");
    expect(bandRange("INR", run().metrics.RECOVERED_CASH)).toBe("₹4.02 Cr to ₹5.88 Cr");
  });

  it("turns an IFRS-9 fraction into a percentage", () => {
    expect(fractionPct(run().ifrs9.stage1.COVERAGE)).toBe("3.98%");
    expect(fractionPct(run().ifrs9.stage3.PD)).toBe("75.0%");
  });
});

describe("the headline", () => {
  const tiles = headlineTiles(run());

  it("leads with p50 and carries the band under every figure", () => {
    expect(tiles.map((t) => t.label)).toEqual([
      "Gross NPA", "Cash recovered", "Net of collection cost", "Written off", "Recovery at risk",
    ]);
    expect(tiles[0]).toMatchObject({ value: "13.9%", sub: "11.3% to 16.6% at 12 months" });
    for (const t of tiles.slice(0, 4)) expect(t.sub).toMatch(/ to .* at 12 months/);
  });

  it("names the downside rather than hiding it in a percentile", () => {
    const risk = tiles[4];
    expect(risk.value).toBe("₹3.3 Cr");
    expect(risk.sub).toBe("1 simulated path in 20 nets less than this at 12 months");
  });

  it("never calls a simulated figure a forecast", () => {
    const copy = [BAND_NOTE, ...tiles.map((t) => `${t.label} ${t.sub}`), runSummary(run())].join(" ");
    expect(copy).not.toMatch(/forecast|predict|expected|will be|guarantee/i);
    expect(copy).toMatch(/simulated/);
  });
});

describe("the book at the horizon", () => {
  it("orders states as the engine declares them and shows each band", () => {
    const rows = stateShareRows(run());
    expect(rows.map((r) => r.state)).toEqual(["CURRENT", "SMA_0", "NPA_SUB", "WRITTEN_OFF"]);
    expect(rows[0]).toMatchObject({ label: "Current", value: "56.7%", range: "53.9% to 59.7%" });
  });

  it("shows a state the engine adds later rather than dropping it", () => {
    const r = run();
    const metrics = {
      ...r.metrics,
      STATE_SHARE: { ...r.metrics.STATE_SHARE, SOLD: { p5: 0, p10: 0, p50: 1.5, p90: 3, p95: 4, mean: 1.5, sem: 0.02 } },
    } as SimulationRun["metrics"];
    const rows = stateShareRows(run({ metrics }));
    expect(rows.at(-1)).toMatchObject({ state: "SOLD", label: "SOLD" });
  });

  it("lists the three IFRS-9 stages with exposure, ECL, coverage and PD", () => {
    const rows = ifrs9Rows(run());
    expect(rows.map((r) => r.stage)).toEqual(["stage1", "stage2", "stage3"]);
    expect(rows[0]).toMatchObject({ ead: "₹104.4 Cr", ecl: "₹4.14 Cr", coverage: "3.98%", pd: "6.64%" });
    expect(rows[0].note).toBe("ECL ₹3.31 Cr to ₹4.97 Cr");
  });
});

describe("what gets sent, and what is said about it", () => {
  it("sends only the levers that were moved, so an untouched form is the status quo", () => {
    expect(requestFor(DEFAULT_FORM)).toEqual({
      preset: "baseline", levers: {}, horizon_months: 12, n_paths: 500, seed: 0,
    });
    const moved = {
      ...DEFAULT_FORM,
      preset: "adverse",
      levers: { ...LEVER_DEFAULTS, placement_rate: 0.8, writeoff_policy_months: "18" },
    };
    expect(requestFor(moved)).toEqual({
      preset: "adverse", levers: { placement_rate: 0.8, writeoff_policy_months: 18 },
      horizon_months: 12, n_paths: 500, seed: 0,
    });
  });

  it("names the levers that were moved, in the reader's units", () => {
    expect(changedLevers(DEFAULT_FORM)).toEqual([]);
    expect(changedLevers({
      ...DEFAULT_FORM,
      levers: { ...LEVER_DEFAULTS, placement_rate: 0.8, agency_capacity: 1.5, writeoff_policy_months: "18" },
    })).toEqual([
      "Placed with agencies: 80%", "Field capacity: 1.50×", "Write off NPAs at 18 months",
    ]);
  });

  it("refuses a value the engine would refuse, before a run is spent on it", () => {
    expect(leverErrors(LEVER_DEFAULTS)).toEqual([]);
    expect(leverErrors({ ...LEVER_DEFAULTS, placement_rate: 1.4 })).toHaveLength(1);
    expect(leverErrors({ ...LEVER_DEFAULTS, settlement_discount: 1 })).toHaveLength(1);
    expect(leverErrors({ ...LEVER_DEFAULTS, agency_capacity: 0 })).toHaveLength(1);
    expect(leverErrors({ ...LEVER_DEFAULTS, writeoff_policy_months: "0" })).toHaveLength(1);
    expect(leverErrors({ ...LEVER_DEFAULTS, writeoff_policy_months: "18" })).toEqual([]);
  });

  it("describes the run by what it rests on, and says when it was subsampled", () => {
    expect(runSummary(run())).toBe(
      "Adverse scenario · 500 simulated paths · 12 months · engine mc-1.2.0 · book at 2026-09-30");
    expect(runSummary(run({ subsampled: true }))).toContain("stratified subsample");
  });

  it("prints the macro changes the RUN used, not the preset the form asked for", () => {
    expect(scenarioChanges(run())).toEqual([
      "GDP growth -1.5 pp", "CPI inflation +1.2 pp", "Unemployment +1 pp",
      "Repo rate +50 bps", "Sector index -2%",
    ]);
    const baseline = run({ scenario: { name: "Baseline", gdp: 0, cpi: 0, repo_bps: 0, unemployment: 0, sector: 0 } });
    expect(scenarioChanges(baseline)).toEqual(["No macro change from today"]);
  });
});
