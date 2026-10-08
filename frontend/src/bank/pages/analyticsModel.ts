// The Analytics tabs' data (plan §5.4, task C04), as GET
// /api/v1/bank/analytics/{tab} returns it (backend app/api/v1/endpoints/bank.py,
// app/services/bank/analytics_catalog.py), and the pure transforms from that
// shape into the Command Center's existing chart props (portfolioVisuals.tsx).
// Kept apart from the page so they are testable without mounting it.
import type { FunnelStage, HeatGridRow } from "../components/portfolioVisuals";

export interface AnalyticsTabResponse {
  tab: string;
  available: boolean;
  reason: string | null;
  /** ISO date of the portfolio snapshot a portfolio-derived tab reads
   *  (exposure/migration/concentration); null on the scorecard tabs. */
  as_of?: string | null;
  panels: Record<string, unknown>;
}

/** The four built tabs' own panel shapes, as the backend's _exposure /
 * _migration / _agencies / _compliance functions build them. */
export interface ExposurePanels {
  funnel: { book: number; delinquent: number; placed: number; npa: number };
  dpd_ladder: { dpd_bucket: string; outstanding: number; accounts: number }[];
  product_bucket_heat: { product: string; dpd_bucket: string; outstanding: number }[];
  security_cover: { security: string; outstanding: number }[];
}

export interface MigrationPanels {
  transition_matrix: { from_state: string; to_state: string; exposure: number; accounts: number }[];
  trajectory_12m: { month_end: string; rolled: number; held: number; cured: number }[];
}

export interface AgencyScorecard {
  agency_id: string;
  code: string;
  name: string;
  status: string;
  n_rows: number;
  collection_efficiency: number | null;
  resolution_rate: number | null;
  recovery_vs_expected: number | null;
  ptp_conversion: number | null;
  contact_rate: number | null;
  sla_adherence: number | null;
  productivity_per_agent_per_day: number | null;
  cost_per_100_inr: number | null;
  evidence_integrity_per_100_visits: number | null;
  workforce_active_ratio: number | null;
  workforce_attrition_ratio: number | null;
  workforce_leave_rate: number | null;
  compliance_score: number | null;
}

export interface AgenciesPanels {
  scorecards: AgencyScorecard[];
}

export interface RecoveryPanels {
  by_month: { month_start: string; actual_inr: number; expected_inr: number | null;
             recovery_vs_expected: number | null }[];
}

interface CostRow {
  commission_inr: number; field_cost_inr: number | null; collected_inr: number; cost_per_100_inr: number | null;
}
export interface CostPanels {
  by_month: (CostRow & { month_start: string })[];
  by_agency: (CostRow & { agency_id: string; agency_name: string })[];
}

export interface CompliancePanels {
  breaches_over_time: { month_start: string; out_of_hours: number; geofence: number; consent_missing: number; fraud_confirmed: number }[];
  by_agency: { agency_id: string; agency_name: string; out_of_hours: number; geofence: number; visits: number;
              fraud_confirmed: number }[];
}

/** Field effort and reach off mv_field_activity_daily (analytics_catalog's
 *  _field_ops). Every rate is 0..1 or null; null means the denominator was
 *  zero — no calls made, no routed day planned — and is NOT a 0% result. */
export interface FieldOpsMonthRow {
  month_start: string;
  visits: number;
  met_visits: number;
  calls: number;
  calls_answered: number;
  ptps_set: number;
  payments_verified_count: number;
  agent_days: number;
  planned_stops: number;
  visited_stops: number;
  planned_km: number | null;
  met_rate: number | null;
  contact_rate: number | null;
  visits_per_agent_day: number | null;
  stop_coverage: number | null;
}

export interface FieldOpsAgencyRow {
  agency_id: string;
  agency_name: string;
  visits: number;
  met_visits: number;
  calls: number;
  calls_answered: number;
  ptps_set: number;
  agent_days: number;
  agents: number;
  planned_stops: number;
  visited_stops: number;
  met_rate: number | null;
  contact_rate: number | null;
  visits_per_agent_day: number | null;
  stop_coverage: number | null;
}

export interface FieldOpsPanels {
  by_month: FieldOpsMonthRow[];
  by_agency: FieldOpsAgencyRow[];
}

/** One concentration slice. The dimension's own name arrives under its own key
 *  (zone | state | city | product), which is why `share` and the money columns
 *  are typed here and the label is read by key. */
export interface ConcentrationRow {
  zone?: string;
  state?: string;
  city?: string;
  product?: string;
  outstanding: number | null;
  placed: number | null;
  npa: number | null;
  accounts: number | null;
  share: number | null;
}

export interface ConcentrationPanels {
  by_zone: ConcentrationRow[];
  by_state: ConcentrationRow[];
  by_city: ConcentrationRow[];
  by_product: ConcentrationRow[];
}

/** mv_portfolio_daily's dpd_bucket (CURRENT | BUCKET_1..3 | NPA) to the
 * display label portfolioVisuals.tsx / DPD_COLORS actually key on —
 * these are DIFFERENT vocabularies (DPD_COLORS also carries the
 * transitions view's SMA_0/SMA_1 etc., never produced by Exposure). */
const BUCKET_LABEL: Record<string, string> = {
  CURRENT: "Current", BUCKET_1: "0-30", BUCKET_2: "31-60", BUCKET_3: "61-90", NPA: "NPA",
};
export const DPD_BUCKET_ORDER = ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"] as const;

export function bucketLabel(bucket: string): string {
  return BUCKET_LABEL[bucket] ?? bucket;
}

const CR = 1e7;

export function exposureFunnelStages(p: ExposurePanels): FunnelStage[] {
  const f = p.funnel;
  const accountsByBucket = new Map(p.dpd_ladder.map((r) => [r.dpd_bucket, r.accounts]));
  const delinquentAccounts = DPD_BUCKET_ORDER.filter((b) => b !== "CURRENT")
    .reduce((s, b) => s + (accountsByBucket.get(b) ?? 0), 0);
  const npaAccounts = accountsByBucket.get("NPA") ?? 0;
  return [
    { stage: "Whole book", exposureCr: f.book / CR, accounts: p.dpd_ladder.reduce((s, r) => s + r.accounts, 0) },
    { stage: "Delinquent", exposureCr: f.delinquent / CR, accounts: delinquentAccounts },
    { stage: "Placed", exposureCr: f.placed / CR, accounts: 0 },
    { stage: "NPA", exposureCr: f.npa / CR, accounts: npaAccounts },
  ];
}

export function dpdLadderHeatRows(p: ExposurePanels): HeatGridRow[] {
  const byProduct = new Map<string, Map<string, { exposureCr: number; accounts: number }>>();
  for (const cell of p.product_bucket_heat) {
    if (!byProduct.has(cell.product)) byProduct.set(cell.product, new Map());
    byProduct.get(cell.product)!.set(bucketLabel(cell.dpd_bucket), { exposureCr: round2(cell.outstanding / CR), accounts: 0 });
  }
  return [...byProduct.entries()].map(([product, cells]) => {
    const current = cells.get("Current")?.exposureCr ?? 0;
    const total = [...cells.values()].reduce((s, c) => s + c.exposureCr, 0);
    return {
      label: product, delqRatePct: total > 0 ? round1(((total - current) / total) * 100) : 0,
      delqExposureCr: round2(total - current), cells: Object.fromEntries(cells),
    };
  });
}

export function securityCoverStages(p: ExposurePanels): { security: string; exposureCr: number }[] {
  return p.security_cover.map((r) => ({ security: r.security, exposureCr: round2(r.outstanding / CR) }));
}

/** The transition matrix's row/column labels: only the states Exposure's own
 * DPD ladder already shows, in the SAME order — RESOLVED, WRITTEN_OFF and
 * NO_READING are real `to_state`s but not DPD states, so they sit outside
 * the square grid as a separate accounting rather than forced into it. */
const SQUARE_STATES = ["CURRENT", "SMA_0", "SMA_1", "SMA_2", "NPA_SUB", "NPA_DOUBTFUL"] as const;
const SQUARE_LABEL: Record<string, string> = {
  CURRENT: "Current", SMA_0: "0-30", SMA_1: "31-60", SMA_2: "61-90", NPA_SUB: "90-180", NPA_DOUBTFUL: "180+",
};

export function transitionMatrixData(p: MigrationPanels): { labels: string[]; matrix: number[][]; observed: number } {
  // Rows must sum to 100 (the chart's own footnote promises it): a loan that
  // left the square (to RESOLVED, WRITTEN_OFF or NO_READING) is a real exit,
  // not a DPD-state move, so it is excluded from BOTH the row's total and
  // `observed` — counting it in observed while excluding it from the
  // percentages would make the footnote's number disagree with the grid.
  const labels = SQUARE_STATES.map((s) => SQUARE_LABEL[s]);
  const isSquare = (s: string) => SQUARE_STATES.includes(s as (typeof SQUARE_STATES)[number]);
  const byFrom = new Map<string, Map<string, number>>();
  let observed = 0;
  for (const r of p.transition_matrix) {
    if (!isSquare(r.from_state) || !isSquare(r.to_state)) continue;
    if (!byFrom.has(r.from_state)) byFrom.set(r.from_state, new Map());
    byFrom.get(r.from_state)!.set(r.to_state, (byFrom.get(r.from_state)!.get(r.to_state) ?? 0) + r.accounts);
    observed += r.accounts;
  }
  const matrix = SQUARE_STATES.map((from) => {
    const row = byFrom.get(from);
    const total = row ? [...row.values()].reduce((s, v) => s + v, 0) : 0;
    return SQUARE_STATES.map((to) => (total > 0 ? round1(((row?.get(to) ?? 0) / total) * 100) : 0));
  });
  return { labels, matrix, observed };
}

function round1(v: number): number { return Math.round(v * 10) / 10; }
function round2(v: number): number { return Math.round(v * 100) / 100; }

export function pct(v: number | null): string {
  return v == null ? "Not available" : `${(v * 100).toFixed(1)}%`;
}

export function moneyCr(v: number | null | undefined): string {
  const x = v ?? 0;
  return Math.abs(x) >= CR ? `₹${(x / CR).toFixed(2)} Cr` : `₹${(x / 1e5).toFixed(2)} L`;
}

/* ── Chart rows (owner, 2026-10-07: "only data no actual analytics,
      charts/graphs/aggregations should be there") ─────────────────────────────

   Four tabs rendered their monthly series as text tables. These transforms
   turn each into the props the shared chart shapes take, and they live here
   rather than in the page for the reason the rest of this file does: the
   honesty rules below are worth testing without mounting a chart jsdom
   cannot measure anyway.

   HOW A NULL IS CHARTED: it is not. A missing figure has its KEY OMITTED
   from the row, so Recharts draws a gap; writing 0 would draw a floor and
   read as "we collected nothing" instead of "we do not know". The compact
   table under each chart keeps saying "Not available" in words. This is also
   why the rows are built by `put` rather than object literals: charts.tsx's
   own Row type is Record<string, string | number> and does not admit null,
   and a shared chart shape is not widened for one caller's missing data. */

export type ChartRow = Record<string, string | number>;

function put(row: ChartRow, key: string, v: number | null | undefined): void {
  if (v != null && Number.isFinite(v)) row[key] = v;
}

/** "2026-09-30" -> "Sep 26". Parsed as digits, never through `new Date(iso)`:
 *  that reads a bare date as UTC midnight and prints the month before in any
 *  timezone behind it, which is known issue 3 on nine other frontend reads. */
export function monthLabel(iso: string): string {
  const [y, m] = iso.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const name = names[Number(m) - 1];
  return name ? `${name} ${y.slice(2)}` : iso;
}

function toCr(inr: number): number {
  return round2(inr / CR);
}

/** Accounts cured / held / rolled per month, plus the cure RATE as a line —
 *  the one aggregation the three counts do not show on their own, because a
 *  month with twice the accounts has twice the cures at the same rate.
 *  Omitted (gap, not 0%) for a month with no accounts in any state. */
export function migrationTrajectoryRows(p: MigrationPanels): ChartRow[] {
  return p.trajectory_12m.map((r) => {
    const total = r.cured + r.held + r.rolled;
    const row: ChartRow = { month: monthLabel(r.month_end), cured: r.cured, held: r.held, rolled: r.rolled };
    put(row, "cure_rate_pct", total > 0 ? round1((r.cured / total) * 100) : null);
    return row;
  });
}

/** Actual against expected in crores (both INR, directly comparable, so both
 *  are bars on one axis) and recovery-vs-expected as a percentage line on the
 *  right axis. `expected_inr` is the recovery_risk-predicted figure, not a
 *  bank target — the panel's hint says so, and a month without a prediction
 *  charts no expected bar at all. */
export function recoveryMonthRows(p: RecoveryPanels): ChartRow[] {
  return p.by_month.map((r) => {
    const row: ChartRow = { month: monthLabel(r.month_start), actual_cr: toCr(r.actual_inr) };
    put(row, "expected_cr", r.expected_inr == null ? null : toCr(r.expected_inr));
    put(row, "vs_expected_pct", r.recovery_vs_expected == null ? null : round1(r.recovery_vs_expected * 100));
    return row;
  });
}

/** Commission and field cost as bars in crores, cost per ₹100 collected as the
 *  line. field_cost_inr is null wherever the book has no field-cost rows, so
 *  that bar is absent for those months rather than sitting at zero — which
 *  would read as "free", the opposite of "not recorded". */
export function costMonthRows(p: CostPanels): ChartRow[] {
  return p.by_month.map((r) => {
    const row: ChartRow = { month: monthLabel(r.month_start), commission_cr: toCr(r.commission_inr) };
    put(row, "field_cost_cr", r.field_cost_inr == null ? null : toCr(r.field_cost_inr));
    put(row, "cost_per_100", r.cost_per_100_inr);
    return row;
  });
}

/** The same economics per agency instead of per month — one bar group per
 *  agency with cost per ₹100 as the line. Same null-is-a-gap rule as above. */
export function costAgencyRows(p: CostPanels): ChartRow[] {
  return p.by_agency.map((r) => {
    const row: ChartRow = { agency: r.agency_name, commission_cr: toCr(r.commission_inr) };
    put(row, "field_cost_cr", r.field_cost_inr == null ? null : toCr(r.field_cost_inr));
    put(row, "cost_per_100", r.cost_per_100_inr);
    return row;
  });
}

/** The four breach types as grouped bars, with their total as the line. The
 *  total is the sum of exactly those four and nothing else, so the line and
 *  the bars can never disagree. */
export function complianceBreachRows(p: CompliancePanels): ChartRow[] {
  return p.breaches_over_time.map((r) => ({
    month: monthLabel(r.month_start),
    out_of_hours: r.out_of_hours,
    geofence: r.geofence,
    consent_missing: r.consent_missing,
    fraud_confirmed: r.fraud_confirmed,
    total: r.out_of_hours + r.geofence + r.consent_missing + r.fraud_confirmed,
  }));
}

/** Visits and met visits as bars with the met RATE as the line. Both counts
 *  are on one axis because met_visits is a subset of visits and the gap
 *  between the bars IS the miss. A month with no visits has no rate — gap,
 *  not 0%. */
export function fieldOpsMonthRows(p: FieldOpsPanels): ChartRow[] {
  return p.by_month.map((r) => {
    const row: ChartRow = { month: monthLabel(r.month_start), visits: r.visits, met_visits: r.met_visits };
    put(row, "met_rate_pct", r.met_rate == null ? null : round1(r.met_rate * 100));
    return row;
  });
}

/** Planned against visited stops, with coverage as the line. A zero here is
 *  REAL and charted: zero planned stops means no routed day in that month,
 *  which is a measurement, not a gap. The COVERAGE is the unknown in that
 *  case (0/0), so only the line breaks. */
export function fieldOpsCoverageRows(p: FieldOpsPanels): ChartRow[] {
  return p.by_month.map((r) => {
    const row: ChartRow = {
      month: monthLabel(r.month_start), planned_stops: r.planned_stops, visited_stops: r.visited_stops,
    };
    put(row, "stop_coverage_pct", r.stop_coverage == null ? null : round1(r.stop_coverage * 100));
    return row;
  });
}

/** How many slices a concentration chart draws. A city breakdown can run to
 *  dozens of rows, and forty bars on a categorical axis is a texture, not a
 *  reading; the table under the chart keeps every row. */
export const CONCENTRATION_CHART_LIMIT = 10;

/** One concentration dimension as chart rows: outstanding, placed and NPA in
 *  crores with the share of book as the line, biggest first (the backend
 *  already orders by outstanding), truncated to CONCENTRATION_CHART_LIMIT.
 *  `dim` is the key the dimension's name arrives under. */
export function concentrationRows(rows: ConcentrationRow[], dim: "zone" | "state" | "city" | "product"): ChartRow[] {
  return rows.slice(0, CONCENTRATION_CHART_LIMIT).map((r) => {
    const row: ChartRow = { name: String(r[dim] ?? "Unassigned") };
    put(row, "outstanding_cr", r.outstanding == null ? null : toCr(r.outstanding));
    put(row, "placed_cr", r.placed == null ? null : toCr(r.placed));
    put(row, "npa_cr", r.npa == null ? null : toCr(r.npa));
    put(row, "share_pct", r.share == null ? null : round1(r.share * 100));
    return row;
  });
}
