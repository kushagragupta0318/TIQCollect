// The Analytics tabs' data (plan §5.4, task C04), as GET
// /api/v1/bank/analytics/{tab} returns it (backend app/api/v1/endpoints/bank.py,
// app/services/bank/analytics_catalog.py), and the pure transforms from that
// shape into the Command Center's existing chart props (portfolioVisuals.tsx).
// Kept apart from the page so they are testable without mounting it.
import type { ChartSeries } from "../components/charts";
import { CHART_SERIES } from "../theme/colors";
import type { FunnelStage, HeatGridRow } from "../components/portfolioVisuals";

export interface AnalyticsTabResponse {
  tab: string;
  available: boolean;
  reason: string | null;
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

/** _field_ops's two grains (e6's contract): by_day/by_agent are summed over
 * the window; sla_by_agency is monthly and reuses agency_scorecard.py's own
 * sla_adherence definition — never pivoted onto the daily x-axis. */
export interface FieldOpsDailyRow {
  day: string; visits: number; met_visits: number; met_rate_pct: number | null;
  planned_stops: number; visited_stops: number; beat_adherence_pct: number | null;
  planned_km: number; actual_km: number | null;
}
export interface FieldOpsAgentRow {
  agent_id: string; agent_name: string; visits: number; met_rate_pct: number | null; beat_adherence_pct: number | null;
}
export interface FieldOpsAgencySlaRow {
  agency_id: string; agency_name: string; month_start: string; sla_adherence_pct: number | null;
}
export interface FieldOpsPanels {
  by_day: FieldOpsDailyRow[];
  by_agent: FieldOpsAgentRow[];
  sla_by_agency: FieldOpsAgencySlaRow[];
}

/** _concentration's shapes (e6's contract). by_branch has no NPA split —
 * mv_portfolio_daily carries no branch column, so this grain comes from a
 * live Loan query instead; flagged to 73 as separate follow-up work. */
export interface ConcentrationGeoRow {
  id: string; name: string; exposure: number; npa_exposure: number; accounts: number; npa_accounts: number;
}
export interface ConcentrationProductRow {
  product: string; exposure: number; npa_exposure: number; accounts: number; npa_accounts: number;
}
export interface ConcentrationBranchRow {
  branch_code: string; branch_name: string; exposure: number; loan_count: number;
}
export interface ConcentrationRatios { top_5_cities_share_pct: number; top_5_branches_share_pct: number }
export interface ConcentrationPanels {
  by_city: ConcentrationGeoRow[]; by_state: ConcentrationGeoRow[]; by_region: ConcentrationGeoRow[]; by_zone: ConcentrationGeoRow[];
  by_product: ConcentrationProductRow[]; by_branch: ConcentrationBranchRow[]; concentration_ratios: ConcentrationRatios;
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

/** "2026-07-01" -> "Jul 26". Date-only strings parse as UTC midnight, which
 *  is fine here since only the month/year are read, never the time of day. */
export function shortMonth(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
}

/** "2026-07-14" -> "14 Jul", for a daily x-axis where shortMonth's year-grain
 *  label would repeat across every day in the same month. */
export function shortDay(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.getDate()} ${d.toLocaleDateString("en-GB", { month: "short" })}`;
}

/** Field Ops' daily trend: visit volume against met rate. */
export function fieldOpsVisitsChartRows(p: FieldOpsPanels): Record<string, string | number>[] {
  return p.by_day.map((r) => {
    const row: Record<string, string | number> = { day: shortDay(r.day), visits: r.visits };
    if (r.met_rate_pct != null) row.met_rate_pct = round1(r.met_rate_pct);
    return row;
  });
}

/** Field Ops' beat-adherence trend. A day with no routed beat has no
 *  reading at all (e6: actual_km/beat_adherence_pct are null, never 0), so
 *  it is left off rather than plotted as a zero day. */
export function fieldOpsBeatAdherenceChartRows(p: FieldOpsPanels): Record<string, string | number>[] {
  return p.by_day
    .filter((r) => r.beat_adherence_pct != null)
    .map((r) => ({ day: shortDay(r.day), beat_adherence_pct: round1(r.beat_adherence_pct as number) }));
}

/** Field Ops' per-agent leaderboard: visits against met rate, whole window. */
export function fieldOpsAgentChartRows(p: FieldOpsPanels): Record<string, string | number>[] {
  return p.by_agent.map((a) => {
    const row: Record<string, string | number> = { agent_name: a.agent_name, visits: a.visits };
    if (a.met_rate_pct != null) row.met_rate_pct = round1(a.met_rate_pct);
    return row;
  });
}

/** Field Ops' SLA trend: one line per agency, monthly — the OTHER grain
 *  (by_day is daily), kept on its own chart rather than forced onto the
 *  same x-axis. A month an agency has no reading simply has no point. */
export function fieldOpsSlaTrend(p: FieldOpsPanels): { rows: Record<string, string | number>[]; series: ChartSeries[] } {
  const order: string[] = [];
  const names = new Map<string, string>();
  for (const r of p.sla_by_agency) {
    if (!names.has(r.agency_id)) { names.set(r.agency_id, r.agency_name); order.push(r.agency_id); }
  }
  const byMonth = new Map<string, Record<string, string | number>>();
  for (const r of p.sla_by_agency) {
    if (!byMonth.has(r.month_start)) byMonth.set(r.month_start, { month: shortMonth(r.month_start) });
    if (r.sla_adherence_pct != null) byMonth.get(r.month_start)![r.agency_id] = round1(r.sla_adherence_pct);
  }
  const rows = [...byMonth.keys()].sort().map((k) => byMonth.get(k) as Record<string, string | number>);
  const series = order.map((id, i) => ({ key: id, name: names.get(id) ?? id, color: CHART_SERIES[i % CHART_SERIES.length] }));
  return { rows, series };
}

const GEO_TOP_N = 10;

function geoChartRows(rows: ConcentrationGeoRow[]): Record<string, string | number>[] {
  return rows.slice(0, GEO_TOP_N).map((r) => ({
    name: r.name, exposure_cr: round2(r.exposure / CR), npa_exposure_cr: round2(r.npa_exposure / CR),
  }));
}

/** Concentration's three charted levels (73's brief: branches/cities/products
 *  by exposure). State/region/zone are real data too, so they still get a
 *  table below — just not a third near-identical bar chart each. */
export function concentrationCityChartRows(p: ConcentrationPanels): Record<string, string | number>[] {
  return geoChartRows(p.by_city);
}

export function concentrationProductChartRows(p: ConcentrationPanels): Record<string, string | number>[] {
  return [...p.by_product]
    .sort((a, b) => b.exposure - a.exposure)
    .map((r) => ({ name: r.product, exposure_cr: round2(r.exposure / CR), npa_exposure_cr: round2(r.npa_exposure / CR) }));
}

/** by_branch carries no NPA split (e6: mv_portfolio_daily has no branch
 *  column) — loan count rides the right axis instead, as the honest second
 *  series rather than a fabricated NPA figure. */
export function concentrationBranchChartRows(p: ConcentrationPanels): Record<string, string | number>[] {
  return [...p.by_branch]
    .sort((a, b) => b.exposure - a.exposure)
    .slice(0, GEO_TOP_N)
    .map((r) => ({ name: r.branch_name, exposure_cr: round2(r.exposure / CR), loan_count: r.loan_count }));
}

/** Recovery's lead chart: actual vs the recovery_risk-predicted figure, by
 *  month. `expected_cr` is left unset (never 0) on a month the model
 *  abstained on, same as the table's own "Not available" — a flat line
 *  through those months would say a number exists where none does. */
export function recoveryChartRows(p: RecoveryPanels): Record<string, string | number>[] {
  return p.by_month.map((r) => {
    const row: Record<string, string | number> = { month: shortMonth(r.month_start), actual_cr: round2(r.actual_inr / CR) };
    if (r.expected_inr != null) row.expected_cr = round2(r.expected_inr / CR);
    return row;
  });
}

/** Agencies' leaderboard: ranked by collection efficiency, recovery vs
 *  expected riding alongside on the right axis. An agency with no efficiency
 *  reading yet can't be ranked, so it is left off the chart entirely — the
 *  table beneath still lists it as "Not available", nothing is hidden. */
export function agencyLeaderboardRows(p: AgenciesPanels): Record<string, string | number>[] {
  return p.scorecards
    .filter((a) => a.collection_efficiency != null)
    .slice()
    .sort((a, b) => (b.collection_efficiency ?? 0) - (a.collection_efficiency ?? 0))
    .map((a) => {
      const row: Record<string, string | number> = {
        code: a.code, collection_efficiency_pct: round1((a.collection_efficiency ?? 0) * 100),
      };
      if (a.recovery_vs_expected != null) row.recovery_vs_expected_pct = round1(a.recovery_vs_expected * 100);
      return row;
    });
}

/** Cost to Collect's monthly trend: commission and field cost (both ₹ Cr,
 *  left axis) against cost per ₹100 collected (right axis). Field cost is
 *  the one leg the book doesn't always have; a month without it draws no
 *  bar for that leg rather than a fabricated zero. */
export function costMonthlyChartRows(p: CostPanels): Record<string, string | number>[] {
  return p.by_month.map((r) => {
    const row: Record<string, string | number> = { month: shortMonth(r.month_start), commission_cr: round2(r.commission_inr / CR) };
    if (r.field_cost_inr != null) row.field_cost_cr = round2(r.field_cost_inr / CR);
    if (r.cost_per_100_inr != null) row.cost_per_100 = round2(r.cost_per_100_inr);
    return row;
  });
}

/** Cost to Collect's per-agency bar: cost per ₹100 collected, with the
 *  ₹ Cr actually collected riding alongside for scale. An agency with no
 *  cost_per_100 reading is left off, same reasoning as the leaderboard above. */
export function costByAgencyChartRows(p: CostPanels): Record<string, string | number>[] {
  return p.by_agency
    .filter((a) => a.cost_per_100_inr != null)
    .map((a) => ({
      agency_name: a.agency_name, cost_per_100: round2(a.cost_per_100_inr ?? 0), collected_cr: round2(a.collected_inr / CR),
    }));
}

/** Compliance's breach trend: every category is a real count, never null, so
 *  no abstain case applies here. */
export function complianceChartRows(p: CompliancePanels): Record<string, string | number>[] {
  return p.breaches_over_time.map((r) => ({
    month: shortMonth(r.month_start), out_of_hours: r.out_of_hours, geofence: r.geofence,
    consent_missing: r.consent_missing, fraud_confirmed: r.fraud_confirmed,
  }));
}

export function pct(v: number | null): string {
  return v == null ? "Not available" : `${(v * 100).toFixed(1)}%`;
}

export function moneyCr(v: number | null | undefined): string {
  const x = v ?? 0;
  return Math.abs(x) >= CR ? `₹${(x / CR).toFixed(2)} Cr` : `₹${(x / 1e5).toFixed(2)} L`;
}
