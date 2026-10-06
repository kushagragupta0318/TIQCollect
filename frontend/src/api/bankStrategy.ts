// The bank's Monte Carlo simulator: POST /bank/strategy/simulate
// (backend/app/api/v1/endpoints/bank_strategy.py, app/strategy/*).
//
// Every figure arrives as a BAND over simulated paths, never a single number,
// and the honesty stamp arrives WITH it (ADR 0014). The types below keep the
// two inseparable: a SimulationRun carries `text`/`basis`/`synthetic`/
// `calibrated`, so a surface cannot render a figure and omit what it rests on.
import api, { LONG_RUNNING_MS } from "./axios";

/** One metric at the horizon, over the run's paths. `sem` is the Monte Carlo
 *  standard error of the mean — it says how many digits are real. */
export interface Band {
  p5: number;
  p10: number;
  p50: number;
  p90: number;
  p95: number;
  mean: number;
  sem: number;
}

/** Macro CHANGES from today, not levels (monte_carlo.MacroScenario). */
export interface MacroScenario {
  name: string;
  gdp: number;
  cpi: number;
  repo_bps: number;
  unemployment: number;
  sector: number;
}

/** What the bank can set. Omitted fields keep the status quo the transition
 *  matrices were observed under (monte_carlo.Levers defaults), so an empty
 *  object reproduces the observed book. `commission_pct` is per state and is
 *  not offered here yet; the engine keeps its default. */
export interface Levers {
  placement_rate?: number;
  agency_capacity?: number;
  settlement_discount?: number;
  legal_threshold_days?: number;
  writeoff_policy_months?: number | null;
}

export interface SimulateRequest {
  preset: string;
  scenario?: Partial<Omit<MacroScenario, "name">> | null;
  levers: Levers;
  horizon_months: number;
  n_paths: number;
  seed: number;
}

/** Percent metrics (GNPA_PCT, STATE_SHARE) are percentages; INR metrics are
 *  rupees; the account metrics are counts. monte_carlo.METRIC_UNITS is the
 *  authority — simulatorModel.METRIC_UNIT mirrors it for display. */
export type MetricKey =
  | "GNPA_PCT" | "RECOVERED_CASH" | "SETTLEMENT_CASH" | "ECL" | "WRITE_OFFS"
  | "COST" | "NET_RECOVERY" | "WRITE_OFF_ACCOUNTS" | "RECOVERED_ACCOUNTS" | "NPA_ACCOUNTS";

export interface Ifrs9Stage {
  EAD: Band;
  ECL: Band;
  /** ECL / EAD, a FRACTION (0.0398), not a percentage. */
  COVERAGE: Band;
  /** Coverage implied by the stage's LGD, also a fraction. */
  PD: Band;
}

export interface SimulationRun {
  // Reproducibility.
  engine_version: string;
  seed: number;
  n_paths: number;
  horizon_months: number;
  numpy_version: string | null;
  subsampled: boolean;
  // The caveats, which travel with every number above.
  synthetic_inputs: boolean;
  calibrated_by_backtest: boolean;
  synthetic_warning: string | null;
  assumptions: string[];
  scenario: MacroScenario;
  metrics: Record<MetricKey, Band> & { STATE_SHARE: Record<string, Band> };
  /** p5 of cumulative net recovery: the downside 1 path in 20 falls below. */
  recovery_at_risk: number;
  ifrs9: Record<"stage1" | "stage2" | "stage3", Ifrs9Stage>;
  // The honesty stamp (strategy/honesty.py). `text` is the one caption; no
  // surface writes its own.
  synthetic: boolean;
  calibrated: boolean;
  data_version: string;
  basis: string;
  text: string;
}

export async function runSimulation(body: SimulateRequest): Promise<SimulationRun> {
  // A Monte Carlo run (up to 1000 paths x 60 months) exceeds the default 15s
  // axios timeout; the backend keeps running and returns 200, but the browser
  // gives up and the page shows the generic error. Same override the allocation
  // endpoint uses.
  return (await api.post<SimulationRun>("/bank/strategy/simulate", body, { timeout: LONG_RUNNING_MS })).data;
}
