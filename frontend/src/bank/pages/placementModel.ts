// Agencies › Placement (plan §6.3, task D08): the pure half of the page —
// the API shapes, the filter → query string, the selection rules and the
// wording of a gate refusal. Kept apart from the component so it is tested.

/** Mirrors backend models/loan.LoanType (the values the API accepts). */
export const LOAN_TYPES = [
  "HOME", "AUTO", "PERSONAL", "BUSINESS", "GOLD", "CREDIT_CARD", "EDUCATION", "MICROFINANCE",
] as const;
export type LoanTypeCode = (typeof LOAN_TYPES)[number];

/** Mirrors backend models/loan.DPDBucket; labels are the ranges its comments state. */
export const DPD_BUCKETS = [
  { code: "CURRENT", label: "Current" },
  { code: "BUCKET_1", label: "1-30" },
  { code: "BUCKET_2", label: "31-60" },
  { code: "BUCKET_3", label: "61-90" },
  { code: "NPA", label: "90+" },
] as const;
export type DpdBucketCode = (typeof DPD_BUCKETS)[number]["code"];

/** The API's batch cap (manual_placement_service.MAX_BATCH). */
export const MAX_BATCH = 500;

export interface PlaceableLoan {
  loan_id: string;
  loan_account_number: string;
  customer_name: string;
  loan_type: LoanTypeCode;
  dpd: number;
  dpd_bucket: DpdBucketCode;
  total_outstanding: number;
  overdue_amount: number;
  branch_code: string;
  region_id: string | null;
  region_name: string | null;
  region_path: string | null;
  placement_id: string | null;
  placed_with_agency_id: string | null;
  placed_with_agency_name: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** GET /bank/placements: a page plus the model artifact's SYNTHETIC_WARNING, when any row shows a model score. */
export interface PlacementPage extends Page<PlacementRow> {
  synthetic_warning: string | null;
}

export interface CoverageRegion {
  region_id: string;
  name: string;
  path: string;
}

export interface AgencyRoom {
  agency_id: string;
  code: string;
  name: string;
  status: string;
  placeable: boolean;
  contract_no: string | null;
  contract_end: string | null;
  max_placed_cases: number | null;
  active_placements: number;
  /** null = the contract has no cap. */
  headroom: number | null;
  coverage: CoverageRegion[];
}

export type GateName = "loan" | "placement" | "agency" | "contract" | "authorisation" | "coverage" | "capacity";

export interface GateVerdict {
  passed: boolean | null;
  reason: string | null;
  detail: string;
}

export type Outcome = "PLACED" | "KEPT" | "BLOCKED";

export interface LoanVerdict {
  loan_id: string;
  loan_account_number: string;
  outcome: Outcome;
  reason: string;
  gates: Partial<Record<GateName, GateVerdict>>;
  placement_id: string | null;
  case_id: string | null;
  case_number: string | null;
}

export interface BatchResult {
  run_id: string | null;
  agency_id: string;
  on: string;
  headroom_before: number | null;
  counts: Record<Outcome, number>;
  verdicts: LoanVerdict[];
}

export interface PlacementRow {
  placement_id: string;
  loan_id: string;
  loan_account_number: string;
  agency_id: string;
  agency_name: string;
  source: string;
  status: string;
  placed_on: string;
  ended_on: string | null;
  end_reason: string | null;
  dpd_at_placement: number;
  dpd_bucket_at_placement: DpdBucketCode;
  exposure_at_placement: number;
  expected_recovery_prob: number | null;
  sla_first_visit_due: string | null;
  placement_run_id: string | null;
}

// ── filters ────────────────────────────────────────────────────────────────

export interface LoanFilters {
  region_id: string;
  loan_type: LoanTypeCode | "";
  dpd_bucket: DpdBucketCode | "";
  dpd_min: string;
  dpd_max: string;
  placed: "no" | "yes" | "any";
  search: string;
}

export const EMPTY_FILTERS: LoanFilters = {
  region_id: "", loan_type: "", dpd_bucket: "", dpd_min: "", dpd_max: "", placed: "no", search: "",
};

/** Non-negative whole number or undefined; anything else is dropped, never sent. */
function wholeOrUndefined(s: string): string | undefined {
  const t = s.trim();
  return /^\d{1,5}$/.test(t) ? String(Number(t)) : undefined;
}

export function loanQuery(f: LoanFilters, page: number, pageSize: number): URLSearchParams {
  const p = new URLSearchParams();
  if (f.region_id) p.set("region_id", f.region_id);
  if (f.loan_type) p.set("loan_type", f.loan_type);
  if (f.dpd_bucket) p.set("dpd_bucket", f.dpd_bucket);
  const lo = wholeOrUndefined(f.dpd_min);
  const hi = wholeOrUndefined(f.dpd_max);
  if (lo !== undefined) p.set("dpd_min", lo);
  if (hi !== undefined) p.set("dpd_max", hi);
  p.set("placed", f.placed);
  const q = f.search.trim().slice(0, 30);
  if (q) p.set("search", q);
  p.set("page", String(Math.max(1, Math.floor(page))));
  p.set("page_size", String(pageSize));
  return p;
}

/** Every region any placeable agency covers, once, in path order: the region filter's options. */
export function regionOptions(agencies: AgencyRoom[]): CoverageRegion[] {
  const seen = new Map<string, CoverageRegion>();
  for (const a of agencies) for (const r of a.coverage) if (!seen.has(r.region_id)) seen.set(r.region_id, r);
  return [...seen.values()].sort((a, b) => a.path.localeCompare(b.path));
}

// ── selection ──────────────────────────────────────────────────────────────

/** Only an unplaced loan can be selected for placement. */
export const selectable = (l: PlaceableLoan): boolean => l.placement_id === null;

export function toggle(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else if (next.size < MAX_BATCH) next.add(id);
  return next;
}

/** Select (or, when all already are, clear) the page's selectable loans, never past the cap. */
export function togglePage(selected: ReadonlySet<string>, page: PlaceableLoan[]): Set<string> {
  const ids = page.filter(selectable).map((l) => l.loan_id);
  const next = new Set(selected);
  if (ids.length > 0 && ids.every((id) => next.has(id))) {
    for (const id of ids) next.delete(id);
    return next;
  }
  for (const id of ids) {
    if (next.size >= MAX_BATCH) break;
    next.add(id);
  }
  return next;
}

export type PageSelection = "none" | "some" | "all";

export function pageSelection(selected: ReadonlySet<string>, page: PlaceableLoan[]): PageSelection {
  const ids = page.filter(selectable).map((l) => l.loan_id);
  const n = ids.filter((id) => selected.has(id)).length;
  if (n === 0) return "none";
  return n === ids.length ? "all" : "some";
}

// ── agencies ───────────────────────────────────────────────────────────────

export function headroomLabel(a: AgencyRoom): string {
  if (!a.placeable) return a.status !== "ACTIVE" ? `Agency ${a.status.toLowerCase()}` : "No contract in force";
  if (a.headroom === null) return "No cap";
  return `${a.headroom.toLocaleString("en-IN")} of ${(a.max_placed_cases ?? 0).toLocaleString("en-IN")} free`;
}

// ── verdicts ───────────────────────────────────────────────────────────────

/** Plain wording for placement_service's refusal codes. */
export const REASON_TEXT: Record<string, string> = {
  LOAN_NOT_OPEN: "Loan is closed, settled or written off",
  NO_AGENCY: "Agency not found for this bank",
  AGENCY_NOT_ACTIVE: "Agency is not active",
  NO_CONTRACT_IN_FORCE: "No contract in force",
  NOT_AUTHORISED: "Product and DPD bucket not authorised by the contract",
  NOT_COVERED: "Outside the agency's contracted territory",
  CONTRACT_FULL: "Agency is at its placement cap",
  PLACED_ELSEWHERE: "Already placed with another agency",
};

/** The refusal code a BLOCKED verdict carries ("CODE: detail"), or null. */
export function refusalCode(v: LoanVerdict): string | null {
  if (v.outcome !== "BLOCKED") return null;
  const code = v.reason.split(":", 1)[0].trim();
  return code in REASON_TEXT ? code : null;
}

export function verdictText(v: LoanVerdict): string {
  if (v.outcome === "PLACED") return "Placed";
  if (v.outcome === "KEPT") return "Already with this agency";
  const code = refusalCode(v);
  return code ? REASON_TEXT[code] : v.reason;
}

/** Blocked loans grouped by reason, most frequent first: the preview's summary. */
export function blockedByReason(verdicts: LoanVerdict[]): { code: string; text: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const v of verdicts) {
    if (v.outcome !== "BLOCKED") continue;
    const code = refusalCode(v) ?? "OTHER";
    counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([code, count]) => ({ code, text: REASON_TEXT[code] ?? "Other reason", count }))
    .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code));
}

/** A recall reason as the API will accept it (1-500 characters after trimming). */
export function recallReasonError(reason: string): string | null {
  const t = reason.trim();
  if (!t) return "A reason is required.";
  if (t.length > 500) return "Keep the reason to 500 characters.";
  return null;
}

// ── the engine (D09, ADR 0010) ─────────────────────────────────────────────

/** The API's exploration cap (config PLACEMENT_EXPLORATION_RATE, ADR 0010). */
export const MAX_EXPLORATION = 0.2;

export type RunStatus = "PLANNED" | "APPLIED" | "SIMULATED" | "ROLLED_BACK" | "FAILED";
export type DecisionOutcome = "PLACED" | "DEFERRED" | "BLOCKED" | "RECALLED";

export interface EngineRun {
  run_id: string;
  plan_date: string;
  status: RunStatus;
  simulate: boolean;
  strategy: string;
  exploration_rate: number;
  seed: number | null;
  created_by: string | null;
  applied_by: string | null;
  applied_at: string | null;
  totals: { evaluated: number; placed: number; kept: number; blocked: number; deferred: number; recalled: number };
  expected_recovery_total: number | null;
  summary: {
    synthetic_warning?: string | null;
    limitations?: string;
    effect_note?: string;
    elapsed_s?: number;
    explored?: number;
    apply?: { placed: number; recalled: number; skipped_total: number; skipped: { loan_id: string; step: string; why: string }[] };
  };
}

export interface EngineDecision {
  loan_id: string;
  loan_account_number: string;
  outcome: DecisionOutcome;
  reason: string;
  score: number | null;
  chosen_agency_id: string | null;
  chosen_agency_name: string | null;
  previous_agency_id: string | null;
  previous_agency_name: string | null;
  score_breakdown: { is_modelled?: boolean; exploration?: boolean; multiplier?: number | null; commission_pct?: number | null };
  gate_results: { refused?: Record<string, string>; eligible?: string[] };
}

/**
 * Why this person cannot apply this run, or null when they can. Four-eyes
 * (ADR 0010): the planner never applies their own run. Staleness is the
 * server's to judge (it knows the IST day), so it is not guessed here.
 */
export function applyBlocker(run: EngineRun, userId: string | undefined): string | null {
  if (run.simulate || run.status === "SIMULATED") return "A simulation cannot be applied.";
  if (run.status !== "PLANNED") return `This run is ${run.status.toLowerCase()}.`;
  if (!userId || run.created_by === userId) return "Another bank admin must apply a run you planned.";
  return null;
}

/** A 0-20 percent text box as the API's rate, or null when it is not a valid number in range. */
export function explorationRate(percent: string): number | null {
  const t = percent.trim();
  if (t === "") return 0;
  if (!/^\d{1,2}(\.\d{1,2})?$/.test(t)) return null;
  const r = Number(t) / 100;
  return r >= 0 && r <= MAX_EXPLORATION ? r : null;
}
