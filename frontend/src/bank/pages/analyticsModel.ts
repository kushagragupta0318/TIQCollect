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

export interface CompliancePanels {
  breaches_over_time: { month_start: string; out_of_hours: number; geofence: number; consent_missing: number; fraud_confirmed: number }[];
  by_agency: { agency_id: string; agency_name: string; out_of_hours: number; geofence: number; visits: number;
              fraud_confirmed: number }[];
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
