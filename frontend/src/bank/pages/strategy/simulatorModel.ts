// Pure display rules for the Monte Carlo simulator (E05). No React, no axios.
//
// Three rules this file exists to hold:
//
//  1. A figure is a BAND, never a point. The headline is p50 and the range it
//     is read with is p10-p90; p5/p95 belong in detail, and p5 of net recovery
//     has its own name (recovery at risk).
//  2. `sem` decides how many digits are printed. Monte Carlo noise of half a
//     crore printed to the rupee reads as precision the run does not have.
//  3. The word is "simulated", never "forecast" or "expected". The engine is
//     UNCALIBRATED on this book (ADR 0014); scenario arithmetic is what it is.
import type { Band, Ifrs9Stage, SimulationRun } from "@/api/bankStrategy";
import type { PercentileRow } from "../../components/charts";
import { pulseCr, pulseMoney } from "../../theme/format";

/** monte_carlo.METRIC_UNITS, mirrored for display only (the server is the
 *  authority for what each metric means). */
export type Unit = "PCT" | "INR" | "COUNT";
export const METRIC_UNIT: Record<string, Unit> = {
  GNPA_PCT: "PCT", STATE_SHARE: "PCT",
  RECOVERED_CASH: "INR", SETTLEMENT_CASH: "INR", ECL: "INR", WRITE_OFFS: "INR",
  COST: "INR", NET_RECOVERY: "INR",
  WRITE_OFF_ACCOUNTS: "COUNT", RECOVERED_ACCOUNTS: "COUNT", NPA_ACCOUNTS: "COUNT",
};

/**
 * How many decimals the run has earned, from its own Monte Carlo error.
 *
 * `sem` is the standard error of the mean in the metric's own unit; `scale`
 * converts it to the unit being PRINTED (crores for money). A digit is kept
 * only while one step of it is larger than twice the noise, so the last digit
 * shown is one more paths would not move. A deterministic metric (sem 0) gets
 * the fallback.
 */
export function decimalsFor(sem: number, scale = 1, max = 2, fallback = 1): number {
  const noise = Math.abs(sem) * scale;
  if (!Number.isFinite(noise) || noise <= 0) return fallback;
  const dp = Math.ceil(-Math.log10(2 * noise));
  return Math.min(max, Math.max(0, dp));
}

const CR = 1e7;

/** One value of a metric, printed in the metric's unit at the run's precision. */
export function metricValue(unit: Unit, value: number, sem: number): string {
  if (unit === "PCT") return `${value.toFixed(decimalsFor(sem))}%`;
  if (unit === "COUNT") return Math.round(value).toLocaleString("en-IN");
  return pulseCr(value, decimalsFor(sem, 1 / CR));
}

/** The range a figure is read with: p10 to p90, in the metric's unit. */
export function bandRange(unit: Unit, b: Band): string {
  return `${metricValue(unit, b.p10, b.sem)} to ${metricValue(unit, b.p90, b.sem)}`;
}

/** The one sentence that says what the range means. Every band on the page
 *  points at this, so there is one explanation of p10-p90 and not five. */
export const BAND_NOTE = "8 in 10 simulated paths land in this range (p10 to p90).";

/** A fraction (IFRS-9 coverage, PD) as a percentage at the run's precision. */
export function fractionPct(b: Band, field: keyof Band = "p50"): string {
  return `${(b[field] * 100).toFixed(decimalsFor(b.sem, 100))}%`;
}

// ── The headline ─────────────────────────────────────────────────────────────

export interface Tile {
  key: string;
  label: string;
  value: string;
  sub: string;
}

/**
 * The figures a bank reader looks at first, each with its own band beneath it.
 * No net figure is invented here: COST and NET_RECOVERY are the engine's own.
 */
export function headlineTiles(run: SimulationRun): Tile[] {
  const m = run.metrics;
  const horizon = `at ${run.horizon_months} months`;
  const tile = (key: string, label: string, b: Band, unit: Unit): Tile => ({
    key,
    label,
    value: metricValue(unit, b.p50, b.sem),
    sub: `${bandRange(unit, b)} ${horizon}`,
  });
  return [
    tile("GNPA_PCT", "Gross NPA", m.GNPA_PCT, "PCT"),
    tile("RECOVERED_CASH", "Cash recovered", m.RECOVERED_CASH, "INR"),
    tile("NET_RECOVERY", "Net of collection cost", m.NET_RECOVERY, "INR"),
    tile("WRITE_OFFS", "Written off", m.WRITE_OFFS, "INR"),
    {
      key: "RECOVERY_AT_RISK",
      label: "Recovery at risk",
      value: pulseMoney(run.recovery_at_risk),
      sub: `1 simulated path in 20 nets less than this ${horizon}`,
    },
  ];
}

// ── The book at the horizon ──────────────────────────────────────────────────

/** models/loan.PORTFOLIO_STATES, in its order. A state the engine adds later is
 *  still shown (appended), never silently dropped. */
export const STATE_ORDER = [
  "CURRENT", "SMA_0", "SMA_1", "SMA_2", "NPA_SUB", "NPA_DOUBTFUL", "WRITTEN_OFF", "RESOLVED",
] as const;

const STATE_LABEL: Record<string, string> = {
  CURRENT: "Current", SMA_0: "SMA-0 (1–30 days)", SMA_1: "SMA-1 (31–60 days)",
  SMA_2: "SMA-2 (61–90 days)", NPA_SUB: "NPA — substandard", NPA_DOUBTFUL: "NPA — doubtful",
  WRITTEN_OFF: "Written off", RESOLVED: "Closed or recovered",
};

export interface StateShareRow {
  state: string;
  label: string;
  pct: number;
  value: string;
  range: string;
}

export function stateShareRows(run: SimulationRun): StateShareRow[] {
  const share = run.metrics.STATE_SHARE ?? {};
  const known = STATE_ORDER.filter((s) => s in share);
  const extra = Object.keys(share).filter((s) => !(STATE_ORDER as readonly string[]).includes(s));
  return [...known, ...extra].map((state) => {
    const b = share[state];
    return {
      state,
      label: STATE_LABEL[state] ?? state,
      pct: b.p50,
      value: metricValue("PCT", b.p50, b.sem),
      range: bandRange("PCT", b),
    };
  });
}

/** The same rows as `stateShareRows`, shaped for PercentileRangeChart (every
 *  percentile as a plain number, not pre-formatted text). */
export function stateShareChartRows(run: SimulationRun): PercentileRow[] {
  const share = run.metrics.STATE_SHARE ?? {};
  const known = STATE_ORDER.filter((s) => s in share);
  const extra = Object.keys(share).filter((s) => !(STATE_ORDER as readonly string[]).includes(s));
  return [...known, ...extra].map((state) => {
    const b = share[state];
    return { name: STATE_LABEL[state] ?? state, p5: b.p5, p10: b.p10, p50: b.p50, p90: b.p90, p95: b.p95 };
  });
}

/** Gross NPA at the horizon, as the one row PercentileRangeChart needs. */
export function gnpaChartRows(run: SimulationRun): PercentileRow[] {
  const b = run.metrics.GNPA_PCT;
  return [{ name: "Gross NPA", p5: b.p5, p10: b.p10, p50: b.p50, p90: b.p90, p95: b.p95 }];
}

/** The headline cash metrics, in crores, for one comparative chart — the
 *  Tiles already show each on its own; this is the one place they sit next
 *  to each other so their relative size actually reads. */
export function cashChartRows(run: SimulationRun): PercentileRow[] {
  const m = run.metrics;
  const row = (name: string, b: Band): PercentileRow =>
    ({ name, p5: b.p5 / CR, p10: b.p10 / CR, p50: b.p50 / CR, p90: b.p90 / CR, p95: b.p95 / CR });
  return [
    row("Cash recovered", m.RECOVERED_CASH),
    row("Net of collection cost", m.NET_RECOVERY),
    row("Written off", m.WRITE_OFFS),
    row("Settlement cash", m.SETTLEMENT_CASH),
  ];
}

// ── IFRS-9 staging ───────────────────────────────────────────────────────────

export interface StageRow {
  stage: string;
  label: string;
  ead: string;
  ecl: string;
  coverage: string;
  pd: string;
  note: string;
}

const STAGE_LABEL: Record<string, string> = {
  stage1: "Stage 1 — performing (12-month ECL)",
  stage2: "Stage 2 — significant increase in credit risk",
  stage3: "Stage 3 — credit-impaired",
};

/** Written-off and closed balances are DERECOGNISED by the engine: they carry
 *  no exposure and no ECL, and the loss on them is the write-off line. Said on
 *  the panel so a reader does not look for a stage that holds them. */
export const STAGING_NOTE =
  "Written-off and closed balances are derecognised — no exposure, no ECL. The loss on them is the written-off figure.";

export function ifrs9Rows(run: SimulationRun): StageRow[] {
  return (["stage1", "stage2", "stage3"] as const)
    .filter((k) => run.ifrs9?.[k])
    .map((k) => {
      const s: Ifrs9Stage = run.ifrs9[k];
      return {
        stage: k,
        label: STAGE_LABEL[k] ?? k,
        ead: metricValue("INR", s.EAD.p50, s.EAD.sem),
        ecl: metricValue("INR", s.ECL.p50, s.ECL.sem),
        coverage: fractionPct(s.COVERAGE),
        pd: fractionPct(s.PD),
        note: `ECL ${bandRange("INR", s.ECL)}`,
      };
    });
}

export interface Ifrs9ChartRow extends Record<string, string | number> {
  stage: string;
  ead: number;
  ecl: number;
  coverage: number;
}

/** EAD/ECL (crores) and Coverage (a percentage, not the raw fraction) per
 *  stage, for GroupedBarLineChart. p50 only — the table beside this chart
 *  still carries each one's full band for a reader who wants it. */
export function ifrs9ChartRows(run: SimulationRun): Ifrs9ChartRow[] {
  return (["stage1", "stage2", "stage3"] as const)
    .filter((k) => run.ifrs9?.[k])
    .map((k) => {
      const s: Ifrs9Stage = run.ifrs9[k];
      return {
        stage: STAGE_LABEL[k]?.split(" — ")[0] ?? k,
        ead: s.EAD.p50 / CR,
        ecl: s.ECL.p50 / CR,
        coverage: s.COVERAGE.p50 * 100,
      };
    });
}

// ── The controls ─────────────────────────────────────────────────────────────

/** monte_carlo.PRESETS, by key. The macro numbers are NOT restated here — the
 *  response carries the scenario it ran, and the page prints that. */
export const PRESETS = [
  { value: "baseline", label: "Baseline", note: "No macro change from today" },
  { value: "adverse", label: "Adverse", note: "A slowdown" },
  { value: "severely_adverse", label: "Severely Adverse", note: "A deep recession" },
  { value: "sector_shock", label: "Sector Shock", note: "One sector falls hard" },
] as const;

export const HORIZONS = [6, 12, 24, 36] as const;
/** service.N_PATHS_CAP is 1000; the server refuses more. */
export const PATH_COUNTS = [200, 500, 1000] as const;

export interface LeverForm {
  placement_rate: number;        // share of delinquent accounts placed, 0-1
  agency_capacity: number;       // multiple of today's visit volume
  settlement_discount: number;   // share of balance waived, 0-1
  legal_threshold_days: number;  // DPD at which legal action starts
  writeoff_policy_months: string;  // "" = keep the observed hazard
}

/** The status quo: monte_carlo.Levers' own defaults, so an untouched form
 *  reproduces the observed book. */
export const LEVER_DEFAULTS: LeverForm = {
  placement_rate: 0.6,
  agency_capacity: 1,
  settlement_discount: 0,
  legal_threshold_days: 90,
  writeoff_policy_months: "",
};

export interface LeverField {
  key: "placement_rate" | "agency_capacity" | "settlement_discount" | "legal_threshold_days";
  label: string;
  help: string;
  min: number;
  max: number;
  step: number;
  /** Shown beside the slider. */
  format: (v: number) => string;
}

/** Bounds MIRROR Levers.validate() (monte_carlo.py). The server stays the
 *  authority: an out-of-range value is a 422 there, not a silent clamp here. */
export const LEVER_FIELDS: LeverField[] = [
  {
    key: "placement_rate", label: "Placed with agencies", min: 0, max: 1, step: 0.05,
    help: "Share of delinquent accounts handed to a field agency.",
    format: (v) => `${Math.round(v * 100)}%`,
  },
  {
    key: "agency_capacity", label: "Field capacity", min: 0.25, max: 3, step: 0.25,
    help: "Visit volume as a multiple of today's. 1.00× is the book as observed.",
    format: (v) => `${v.toFixed(2)}×`,
  },
  {
    key: "settlement_discount", label: "Settlement discount", min: 0, max: 0.6, step: 0.05,
    help: "Share of the balance waived in a settlement offer. 0% means no programme.",
    format: (v) => `${Math.round(v * 100)}%`,
  },
  {
    key: "legal_threshold_days", label: "Legal action from", min: 30, max: 360, step: 30,
    help: "Days past due at which legal action starts.",
    format: (v) => `${v} days`,
  },
];

/** Client-side echo of Levers.validate(), so an obvious mistake is named before
 *  a run is spent on it. Never a substitute for the server's check. */
export function leverErrors(f: LeverForm): string[] {
  const out: string[] = [];
  if (!(f.placement_rate >= 0 && f.placement_rate <= 1)) {
    out.push("Placed with agencies must be between 0% and 100%.");
  }
  if (!(f.agency_capacity > 0)) out.push("Field capacity must be above zero.");
  if (!(f.settlement_discount >= 0 && f.settlement_discount < 1)) {
    out.push("Settlement discount must be under 100%.");
  }
  if (!(f.legal_threshold_days > 0)) out.push("Legal action must start after at least one day.");
  const wo = f.writeoff_policy_months.trim();
  if (wo !== "" && !(Number.isInteger(Number(wo)) && Number(wo) >= 1)) {
    out.push("Write-off age must be a whole number of months, 1 or more.");
  }
  return out;
}

export interface SimulatorForm {
  preset: string;
  horizon_months: number;
  n_paths: number;
  seed: number;
  levers: LeverForm;
}

export const DEFAULT_FORM: SimulatorForm = {
  preset: "baseline", horizon_months: 12, n_paths: 500, seed: 0, levers: LEVER_DEFAULTS,
};

/** Only levers the reader actually moved are sent: an omitted lever keeps the
 *  engine's default, which is the status quo the matrices were observed under. */
export function requestFor(f: SimulatorForm) {
  const levers: Record<string, number> = {};
  for (const field of LEVER_FIELDS) {
    if (f.levers[field.key] !== LEVER_DEFAULTS[field.key]) levers[field.key] = f.levers[field.key];
  }
  const wo = f.levers.writeoff_policy_months.trim();
  if (wo !== "") levers.writeoff_policy_months = Number(wo);
  return {
    preset: f.preset,
    levers,
    horizon_months: f.horizon_months,
    n_paths: f.n_paths,
    seed: f.seed,
  };
}

/** How the run is described in one line: what it ran on, not what it promises. */
export function runSummary(run: SimulationRun): string {
  const parts = [
    `${run.scenario.name} scenario`,
    `${run.n_paths.toLocaleString("en-IN")} simulated paths`,
    `${run.horizon_months} months`,
    `engine ${run.engine_version}`,
    `book at ${run.data_version}`,
  ];
  if (run.subsampled) parts.push("run on a stratified subsample of the book");
  return parts.join(" · ");
}

/** Which levers were moved away from the status quo, for the run's own record. */
export function changedLevers(f: SimulatorForm): string[] {
  const out: string[] = [];
  for (const field of LEVER_FIELDS) {
    if (f.levers[field.key] !== LEVER_DEFAULTS[field.key]) {
      out.push(`${field.label}: ${field.format(f.levers[field.key])}`);
    }
  }
  const wo = f.levers.writeoff_policy_months.trim();
  if (wo !== "") out.push(`Write off NPAs at ${wo} months`);
  return out;
}

/** The macro scenario the run actually used, as changes from today. Printed
 *  from the response, never from the local preset list. */
export function scenarioChanges(run: SimulationRun): string[] {
  const s = run.scenario;
  const out: string[] = [];
  const pp = (v: number, label: string) => {
    if (v) out.push(`${label} ${v > 0 ? "+" : ""}${v} pp`);
  };
  pp(s.gdp, "GDP growth");
  pp(s.cpi, "CPI inflation");
  pp(s.unemployment, "Unemployment");
  if (s.repo_bps) out.push(`Repo rate ${s.repo_bps > 0 ? "+" : ""}${s.repo_bps} bps`);
  if (s.sector) out.push(`Sector index ${s.sector > 0 ? "+" : ""}${s.sector}%`);
  return out.length ? out : ["No macro change from today"];
}
