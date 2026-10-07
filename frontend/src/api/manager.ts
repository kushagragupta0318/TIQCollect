// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-15 — Added acknowledgeAgentSos() (backs the dashboard's SOS
//   "Respond" buttons — see manager.py POST .../sos/acknowledge and
//   changelog.md).
// 2026-07-15 (later) — VisitRecord: added consent_given; renamed ai_report ->
//   ai_visit_note to match what the backend actually sends (get_case_detail
//   returns `ai_visit_note`, never `ai_report` — the mismatched name meant
//   ManagerCasesPage.tsx's "AI Audit Report" section could never render,
//   silently, since visit.ai_report was always undefined).
// ─────────────────────────────────────────────────────────────────────────
import api, { LONG_RUNNING_MS } from "./axios";
import type { DashboardSummary, Agent, CaseReassignment } from "@/types";
import type { ActivityWindow, FieldActivity } from "@/pages/manager/fieldActivity";
import type { PaymentModes } from "@/pages/manager/paymentModes";
import type { LeaveRequest, LeaveType } from "@/api/agent";

export async function getDashboard(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>("/manager/dashboard");
  return data;
}

/** The overview's Field Activity funnel for one window, anchored on the
 *  dashboard's effective date. Today means today's visits only. */
export async function getFieldActivity(window: ActivityWindow = "today"): Promise<FieldActivity> {
  const { data } = await api.get<FieldActivity>("/manager/dashboard/field-activity", { params: { window } });
  return data;
}

export async function getAgents(): Promise<Agent[]> {
  const { data } = await api.get<Agent[]>("/manager/agents");
  return data;
}

export async function getCases(params?: {
  status?: string;
  priority?: string;
  agent_id?: string;
  date_from?: string;
  date_to?: string;
  /** Field-activity funnel stage — planned | visited | met | paid_or_promised |
   *  not_met | met_no_money — resolved server-side through the same service
   *  the overview funnel uses, for `activity_window` (today | 7d | 30d).
   *  Window-scoped by construction: a case visited yesterday is not "visited"
   *  today. `visit_outcome` (comma list) narrows a reason stage. */
  activity?: string;
  activity_window?: string;
  visit_outcome?: string;
  /** Cases with an ACTIVE promise committed inside [from, to] (yyyy-mm-dd). */
  ptp_due_from?: string;
  ptp_due_to?: string;
  /** HIGH | MEDIUM | LOW — filters on the loan's latest computed recovery label.
   *  Server-side, so it narrows the whole book rather than the current page. */
  recovery?: string;
  /** "priority_desc" | "priority_asc" — switches the list to the actionable
   *  visit-priority view. Anything else keeps the legacy allocation_date order.
   *  Server-side: the list is paginated there, so a client-side sort would only
   *  reorder the 50 rows already fetched. */
  sort?: string;
  /** HIGH | MEDIUM | LOW on the VISIT PRIORITY band — how much a case is worth
   *  working next. Distinct from `recovery`, which bands how much of the loan
   *  comes back. */
  priority_band?: string;
  /** CURRENT | BUCKET_1 | BUCKET_2 | BUCKET_3 | NPA — the loan's DPD bucket.
   *  Server-side, same reason as `recovery`: the overview's DPD donut links
   *  here, and narrowing client-side only filtered the one page already
   *  fetched, so the donut and the list disagreed (demo QA sweep, 2026-10-01). */
  dpd_bucket?: string;
  limit?: number;
  offset?: number;
}) {
  const { data } = await api.get("/manager/cases", { params });
  return data as { total: number; cases: unknown[] };
}

/** Oldest/newest allocation_date across the manager's cases, for seeding the
 *  cases page's date filter. Either field is null when there are no cases. */
export async function getCasesDateRange() {
  const { data } = await api.get<{ min: string | null; max: string | null }>("/manager/cases/date-range");
  return data;
}

/** Month-to-date compliance figures for this manager's own team.
 *
 * Every field is measured from the 1st of the current month, NOT from today —
 * the Compliance page labels them accordingly. `compliance_rate` and
 * `geo_verification_rate` are fractions in [0, 1], not percentages.
 *
 * `out_of_hours_visits` and `compliance_rate` count STORED visits flagged
 * outside hours. Through the real API that is always zero — an out-of-hours
 * visit is refused with 403 and never stored — so they describe seeded data,
 * not compliance. The page does not render them. `blocked_contact_attempts`
 * is the measurement: one CONTACT_HOUR_VIOLATION_ATTEMPT audit row per
 * refused visit, same month, same team.
 */
export interface ComplianceMetrics {
  month: string;                  // "YYYY-MM"
  total_visits: number;
  out_of_hours_visits: number;
  /** Visits refused under the 8 AM – 7 PM IST rule this month. */
  blocked_contact_attempts: number;
  geo_violations: number;
  sos_active_count: number;
  compliance_rate: number;        // 0–1
  geo_verification_rate: number;  // 0–1
  /** Declared audit action types vs. those EVER RECORDED IN THIS DATABASE.
   *  Observed data, system-wide — NOT implementation coverage. A wired action
   *  that has never fired reads as never recorded. `semantics` says so on the
   *  wire so no consumer can misread it. */
  audit_actions: { declared: number; ever_recorded: number; never_recorded: string[]; semantics: string };
}

export async function getCompliance(): Promise<ComplianceMetrics> {
  const { data } = await api.get<ComplianceMetrics>("/manager/compliance");
  return data;
}

// ── Agency profile (P2 G04) — AGENCY_ADMIN only; the backend 403s anyone else ──

export interface AgencyContractSummary {
  contract_no: string;
  status: string;
  /** False when this is the "nothing ACTIVE exists" fallback to the most
   *  recent contract regardless of status — a lapsed/terminated/draft
   *  contract shown because there is nothing better, not because it is in
   *  force. Label it when false; never show its terms as current. */
  is_current: boolean;
  start_date: string;
  end_date: string;
  max_agents: number | null;
  max_placed_cases: number | null;
  max_visits_per_month: number | null;
  sla_first_visit_days: number;
  recall_no_activity_days: number | null;
  recall_on_sla_breach: boolean;
  recall_at_contract_end: boolean;
  performance_bonus_pct: number | null;
  performance_target_pct: number | null;
  security_deposit: number | null;
}

export interface AgencyCommissionTerm {
  loan_type: string;
  dpd_bucket: string;
  commission_pct: number;
  fixed_fee_per_resolution: number | null;
}

export interface AgencyProfile {
  agency: {
    legal_name: string;
    trade_name: string | null;
    rbi_registration_no: string | null;
    status: string;
    hq_city: string | null;
    contact_name: string | null;
    contact_email: string | null;
    contact_phone: string | null;
  };
  contract: AgencyContractSummary | null;
  commission_terms: AgencyCommissionTerm[];
}

export async function getAgencyProfile(): Promise<AgencyProfile> {
  const { data } = await api.get<AgencyProfile>("/manager/agency-profile");
  return data;
}

export async function getAnalytics() {
  const { data } = await api.get("/manager/analytics");
  return data as AnalyticsData;
}

export async function getCaseDetail(caseId: string) {
  const { data } = await api.get(`/manager/cases/${caseId}`);
  return data as ManagerCaseDetail;
}

/** Move a case to another of this manager's agents, with a reason that is
 *  mandatory and audited. Since 2026-09-11 an owned case is sticky in the
 *  nightly plan, so this is the one sanctioned way it changes hands. The
 *  server re-checks every hard gate for the incoming agent and answers 409
 *  with a plain sentence when one refuses; 422 when the reason is blank.
 *  Takes effect at the next nightly plan — today's beat is untouched. */
export async function reassignCase(caseId: string, body: { new_agent_id: string; reason: string }) {
  const { data } = await api.post<{
    case_id: string; case_number: string;
    from_agent_id: string | null; from_agent_name: string | null;
    to_agent_id: string; to_agent_name: string | null;
    reason: string; reassigned_at: string; takes_effect: string;
  }>(`/manager/cases/${caseId}/reassign`, body);
  return data;
}

export async function getAgentsPerformance(months = 6) {
  const { data } = await api.get("/manager/agents/performance", { params: { months } });
  return data as AgentsPerformanceData;
}

export interface VisitRecord {
  id: string;
  visit_number: number;
  outcome: string;
  customer_met: boolean;
  person_met: string | null;
  default_reason: string | null;
  not_met_reason: string | null;
  notes: string | null;
  consent_given: boolean | null;
  geo_verified: boolean;
  within_contact_hours: boolean;
  distance_from_customer_metres: number;
  check_in_time: string;
  check_out_time: string | null;
  property_type: string | null;
  occupancy_status: string | null;
  vehicle_present: boolean | null;
  business_running: boolean | null;
  agent_name: string | null;
  ai_visit_note: string | null;
  agent_recording_transcript: string | null;
  borrower_recording_transcript: string | null;
}

export interface PaymentRecord {
  id: string;
  receipt_number: string;
  amount: number;
  mode: string;
  status: string;
  /** Null when the borrower paid REMOTELY — settling a promise days after the
   *  visit, with nobody at their door. Without surfacing this, the payment
   *  count cannot be reconciled against the visit count and a manager
   *  reasonably concludes a visit is missing. */
  visit_id: string | null;
  upi_reference: string | null;
  cheque_number: string | null;
  bank_reference: string | null;
  payment_date: string;
}

export interface PTPRecord {
  id: string;
  committed_amount: number;
  committed_date: string;
  follow_up_date: string | null;
  status: string;
  customer_reason: string | null;
  agent_notes: string | null;
}

// ── Visit priority (2026-08-27) ───────────────────────────────────────────
// Why one case sits above another in the visit queue. A hand-weighted
// scorecard over facts the schema already holds — `is_modelled` is always
// false, and is sent so no screen can present it as a learned model.
export interface VisitPriorityComponent {
  code: "RECOVERABLE_VALUE" | "URGENCY" | "EFFORT";
  points: number;
  summary: string;
  evidence?: Record<string, unknown>;
  /** True when the term had no data — "not measured", not "measured and low". */
  abstained?: boolean;
}

export interface VisitPriority {
  score: number;
  /** HIGH | MEDIUM | LOW — gives the bare score a meaning. */
  band: "HIGH" | "MEDIUM" | "LOW";
  /** Always three, in a fixed order. */
  components: VisitPriorityComponent[];
  reason: string;
  is_modelled: boolean;
  model_version: string;
  /** Age of the recovery rate behind the value term. */
  rate_as_of?: string | null;
}

export interface ManagerCaseDetail {
  id: string;
  case_number: string;
  status: string;
  priority: string;
  target_amount: number;
  collected_amount: number;
  allocation_date: string;
  visit_count: number;
  is_escalated: boolean;
  agent_id: string | null;
  agent_name: string | null;
  last_reassignment?: CaseReassignment | null;
  collection_stage: string | null;
  bank_ptp_date: string | null;
  bank_ptp_amount: number | null;
  bank_agent_remarks: string | null;
  /** The loan's recovery band, from the nightly snapshot. Null when unscored —
   *  render "Not scored", never LOW. */
  recovery: {
    recovery_potential: "HIGH" | "MEDIUM" | "LOW";
    rate_30: number;
    rate_60: number;
    rate_90: number;
    as_of: string | null;
    is_modelled: boolean;
  } | null;
  /** Null when the loan carried no balance to score. */
  visit_priority: VisitPriority | null;
  customer: {
    id: string;
    full_name: string;
    phone_primary: string;
    city: string;
    state: string;
    risk_category: string;
    cibil_score: number;
    is_hostile: boolean;
    do_not_contact: boolean;
    /** Bank-set: this customer may only be visited by a female agent.
     *  Enforced in allocation; shown here so a manager does not reassign
     *  the case past the rule by hand. */
    requires_female_agent: boolean;
  };
  loan: {
    id: string;
    loan_account_number: string;
    loan_type: string;
    bank_name: string;
    total_outstanding: number;
    overdue_amount: number;
    /** Nothing renders this today — the modal's arrears row was removed on
     *  2026-08-28. Kept on the wire because it is a real ledger column and
     *  the alternative, a pre-summed total, is what kept misleading. */
    penal_charges: number;
    dpd: number;
    dpd_bucket: string;
    legal_status: string;
  };
  visits: VisitRecord[];
  payments: PaymentRecord[];
  ptps: PTPRecord[];
  photos: Array<{ photo_type: string; view_url: string | null; storage_key: string; captured_at: string | null; visit_id: string }>;
}

export interface AgentMonthlyPerf {
  month: string;
  collected: number;
  target: number;
  visits: number;
  ptps_set: number;
  ptps_honored: number;
  collection_rate_pct: number;
}

export interface AgentPerfEntry {
  agent_id: string;
  agent_name: string;
  employee_code: string;
  tier: string;
  territory: string;
  ranking_score: number;
  status: string;
  total_target: number;
  total_collected: number;
  overall_rate_pct: number;
  monthly: AgentMonthlyPerf[];
}

export interface AgentsPerformanceData {
  months: string[];
  agents: AgentPerfEntry[];
}

export interface MonthlyTrend {
  month: string;
  collected_lakhs: number;
  target_lakhs: number;
  total_visits: number;
  /** This team's own progress against its own case targets — live. Renamed
   *  from collection_rate_pct (F1, coordinator audit 2026-10-07) so it is
   *  never read as collection_efficiency_pct below, a different number from
   *  a different source. */
  recovery_vs_target_pct: number;
  /** The bank's own collection-efficiency figure for this agency and month
   *  (agency_scorecard.compute_metrics — verified_collections /
   *  collectible_due, nightly). null when collectible_due is unknown that
   *  month: an abstention (ADR 0005), never a 0 to render as "no reading". */
  collection_efficiency_pct: number | null;
  /** PTP CAPTURE — promises won as a share of the visits where a promise was the
   *  right outcome. A different question from ptp_conversion (promises KEPT).
   *  Added to the API in the PTP fix pass; the type was never updated, and the
   *  vacuous `tsc --noEmit` hid it. */
  visits_needing_promise: number;
  ptps_captured: number;
  ptp_capture_pct: number;
}

export interface DPDBreakdown {
  bucket: string;
  case_count: number;
  target_lakhs: number;
  collected_lakhs: number;
  collection_rate_pct: number;
}

export interface LeaderboardEntry {
  agent_id: string;
  agent_name: string;
  total_collected: number;
  total_visits: number;
  ptps_set: number;
  ptps_honored: number;
  collection_rate_pct: number;
  ranking_score: number;
  tier: string;
}

export interface PTPRiskItem {
  case_number: string;
  customer_name: string;
  dpd: number;
  dpd_bucket: string;
  committed_amount: number;
  broken_ptp_count: number;
  is_hostile: boolean;
  risk: "HIGH" | "MEDIUM" | "LOW";
}

export interface BriefingData {
  ai_generated?: boolean;
  ai_status?: string;
  generated_at: string;
  headline: string;
  key_insight: string;
  recommended_actions: Array<{ action: string; impact: "HIGH" | "MEDIUM"; urgency: "NOW" | "TODAY" }>;
  ptp_risk: {
    high: number;
    medium: number;
    low: number;
    total: number;
    high_risk_cases: PTPRiskItem[];
  };
  collection_velocity: {
    collected_lakh: number;
    target_lakh: number;
    pace_pct: number;
    projected_eod_pct: number;
  };
  stalled_agents: Array<{ id: string; name: string }>;
  dpd_breakdown: DPDBreakdown[];
  total_cases_in_portfolio: number;
  pending_actions: {
    ptps_due: number;
    cases_pending_first_visit: number;
    escalated_cases: number;
  };
}

export interface AgentInsight {
  /** False when the written-in fallback produced this rather than the model. */
  ai_generated?: boolean;
  ai_status?: string;
  agent_id: string;
  performance_signal: "IMPROVING" | "DECLINING" | "STABLE";
  insight_text: string;
  recommended_action: string;
  monthly_data: Array<{
    month: string;
    collection_rate_pct: number;
    visits: number;
    ptp_rate_pct: number;
    collected: number;
  }>;
  current_month: { collection_rate_pct: number; ptp_rate_pct: number; visits: number; visits_today: number; per_visit_yield: number; active_cases: number; resolved_cases: number };
  team_avg: { collection_rate_pct: number; ptp_rate_pct: number; visits_per_month: number; per_visit_yield: number };
  case_mix_by_dpd: Record<string, number>;
}

export interface ReallocationSuggestion {
  case_id: string;
  case_number: string;
  customer_name: string;
  dpd_bucket: string;
  dpd: number;
  target_amount: number;
  priority: string;
  to_agent_id: string;
  to_agent_name: string;
  to_agent_tier: string;
  to_agent_available_slots: number;
  match_score: number;
  match_reason: string;
}

export interface ReallocationPlan {
  from_agent: { id: string; name: string; territory: string; tier: string };
  total_cases: number;
  suggested_reallocations: ReallocationSuggestion[];
  unallocatable_cases: Array<{ case_number: string; customer_name: string; target_amount: number; reason: string }>;
  summary: { can_reallocate: number; cannot_reallocate: number; agents_receiving: number };
}

export async function getBriefing(refresh = false): Promise<BriefingData> {
  const { data } = await api.get<BriefingData>("/manager/ai/briefing", {
    params: refresh ? { refresh: true } : undefined,
  });
  return data;
}

export async function getAgentInsight(agentId: string): Promise<AgentInsight> {
  const { data } = await api.get<AgentInsight>(`/manager/agents/${agentId}/ai-insight`);
  return data;
}

export async function getReallocationPlan(agentId: string): Promise<ReallocationPlan> {
  const { data } = await api.get<ReallocationPlan>(`/manager/agents/${agentId}/reallocation-plan`);
  return data;
}

export async function updateAgentStatus(
  agentId: string,
  status: "ON_DUTY" | "OFF_DUTY"
): Promise<{ agent_id: string; agent_name: string; new_status: string; updated_at: string }> {
  const { data } = await api.put(`/manager/agents/${agentId}/status`, { status });
  return data;
}

// Real action behind the SOS "Respond" buttons — sends the agent a real
// SMS/WhatsApp confirming their manager has seen the alert. See
// backend/app/api/v1/endpoints/manager.py (POST .../sos/acknowledge).
export async function acknowledgeAgentSos(
  agentId: string
): Promise<{ acknowledged: boolean; agent_id: string; agent_name: string; acknowledged_at: string }> {
  const { data } = await api.post(`/manager/agents/${agentId}/sos/acknowledge`);
  return data;
}

export interface AgentCalendarDay {
  date: string;
  day_of_week: string;
  status: "ON_DUTY" | "OFF_DUTY";
  beat_status: string | null;
  cases: number;
}

export interface AgentAvailabilityCalendar {
  agent_id: string;
  agent_name: string;
  current_status: string;
  calendar: AgentCalendarDay[];
  summary: {
    total_working_days: number;
    on_duty_days: number;
    off_duty_days: number;
    attendance_rate_pct: number;
  };
  monthly_summary: Array<{
    month: string;
    on_duty: number;
    off_duty: number;
    total_cases: number;
    attendance_pct: number;
  }>;
}

export interface AgentDPDRow {
  bucket: string;
  case_count: number;
  target_lakhs: number;
  collected_lakhs: number;
  collection_rate_pct: number;
}

export async function getManagerAgentCalendar(agentId: string): Promise<AgentAvailabilityCalendar> {
  const { data } = await api.get<AgentAvailabilityCalendar>(`/manager/agents/${agentId}/availability-calendar`);
  return data;
}

export async function getAgentDPDBreakdown(agentId: string, month?: string): Promise<AgentDPDRow[]> {
  const { data } = await api.get<AgentDPDRow[]>(`/manager/agents/${agentId}/dpd-breakdown`, {
    params: month ? { month } : undefined,
  });
  return data;
}

/** Verified collections by payment mode for the manager's agents — all
 *  time, or one calendar month with the same `month=YYYY-MM` convention as
 *  the DPD bucket card, so one month-click filters both. */
export async function getPaymentModes(month?: string): Promise<PaymentModes> {
  const { data } = await api.get<PaymentModes>("/manager/analytics/payment-modes", {
    params: month ? { month } : undefined,
  });
  return data;
}

/** How promises ended, by the month they fell due — team-wide, or one of
 *  this manager's agents. 2026-09-18; see pages/manager/ptpOutcomes.ts. */
export async function getPtpOutcomes(months = 6, agentId?: string): Promise<import("@/pages/manager/ptpOutcomes").PtpOutcomes> {
  const { data } = await api.get("/manager/analytics/ptp-outcomes", {
    params: { months, ...(agentId ? { agent_id: agentId } : {}) },
  });
  return data;
}

/** A dimension the team's book can be broken down by (known issue 8). The
 *  bucket dimension is the long-standing DPD card; the other three were always
 *  in the data and never surfaced. */
export type BreakdownDimension = "bucket" | "product" | "branch" | "city";

export interface BreakdownRow {
  /** The dimension's value: a bucket name, a loan type, a branch code, a city.
   *  "Not recorded" when the column is empty, rather than the row being dropped. */
  key: string;
  case_count: number;
  target_lakhs: number;
  collected_lakhs: number;
  collection_rate_pct: number;
}

/** The team's book by branch, city, product or bucket. Rows come back largest
 *  collection first, except bucket, which keeps its severity order. */
export async function getTeamBreakdown(dimension: BreakdownDimension, month?: string): Promise<BreakdownRow[]> {
  const { data } = await api.get<BreakdownRow[]>("/manager/analytics/breakdown", {
    params: { dimension, ...(month ? { month } : {}) },
  });
  return data;
}

export async function getTeamDPDBreakdown(month?: string): Promise<AgentDPDRow[]> {
  const { data } = await api.get<AgentDPDRow[]>("/manager/analytics/dpd-breakdown", {
    params: month ? { month } : undefined,
  });
  return data;
}

export interface TeamAttendance {
  month: string;
  total_agents: number;
  by_date: Record<string, number>;
  working_days: number;
  total_leave_agent_days: number;
  leave_by_type: Record<string, number>;
}

export async function getTeamAttendance(month?: string): Promise<TeamAttendance> {
  const { data } = await api.get<TeamAttendance>("/manager/analytics/team-attendance", {
    params: month ? { month } : undefined,
  });
  return data;
}

export interface MonthlyReport {
  month: string;
  scope: string;
  report_text: string;
  /** False when the computed fallback wrote this rather than the model. */
  ai_generated?: boolean;
  ai_status?: string;
  /** The model that actually answered. Rendered instead of a hardcoded name —
   *  the page displayed "GPT-4o-mini" long after that stopped being true. */
  ai_model?: string | null;
  ai_provider?: string | null;
}

export async function getMonthlyReport(month: string, agentId?: string): Promise<MonthlyReport> {
  const params: Record<string, string> = { month };
  if (agentId) params.agent_id = agentId;
  const { data } = await api.get<MonthlyReport>("/manager/ai/monthly-report", { params });
  return data;
}

/** One HIGH/MEDIUM/LOW row of the open book. */
export interface RecoveryBreakdown {
  band: "HIGH" | "MEDIUM" | "LOW";
  cases: number;
  /** Instalments missed plus penalties, summed. A ledger fact.
   *  Was `due_now` until 2026-08-27 — see the card's docblock. */
  arrears_and_penal: number;
  /**
   * rate_90 x live TOTAL OUTSTANDING, summed. Rendered as "90-day recovery
   * estimate"; it includes principal not yet due, so it is not a collections
   * target and must never be shown as one.
   */
  expected_recoverable_amount: number;
  total_outstanding: number;
}

export interface AnalyticsData {
  monthly_trend: MonthlyTrend[];
  dpd_breakdown: DPDBreakdown[];
  recovery_breakdown: RecoveryBreakdown[];
  recovery_summary: {
    scored_cases: number;
    /** Reported, not folded into LOW — an unscored loan is unknown, not written off. */
    unscored_cases: number;
    /** Every open case, scored or not — wider than the per-band rows. */
    arrears_and_penal: number;
    expected_recoverable_amount: number;
    label_horizon_days: number;
    /** Always false. A hand-weighted scorecard, never a trained model. */
    is_modelled: boolean;
  };
  leaderboard: LeaderboardEntry[];
  leave_summary: { total_leave_days_30d: number; by_type: Record<string, number> };
  kpis: {
    overall_collection_rate_pct: number;
    total_collected_lakhs: number;
    total_target_lakhs: number;
    ptp_conversion_rate_pct: number;
    /** Capture, not conversion — see MonthlyTrend above. */
    ptp_capture_rate_pct: number;
    avg_visits_per_agent_current_month: number;
  };
}

// ─── Live agent locations (2026-08-18) ──────────────────────────────────────

export interface LiveAgentPosition {
  agent_id: string;
  employee_code: string;
  full_name: string;
  status: string;
  sos_active: boolean;
  sos_triggered_at: string | null;
  latitude: number | null;
  longitude: number | null;
  accuracy_metres: number | null;
  battery_pct: number | null;
  recorded_at: string | null;
  /** Seconds since the fix was captured, computed server-side so every client
   *  agrees on what counts as stale. */
  age_seconds: number | null;
}

export interface TrailPoint {
  latitude: number;
  longitude: number;
  accuracy_metres: number | null;
  recorded_at: string;
  source: "HEARTBEAT" | "CHECK_IN" | "VISIT" | "SOS";
  is_sos: boolean;
  battery_pct: number | null;
}

export interface AgentTrail {
  agent_id: string;
  employee_code: string;
  full_name: string;
  sos_active: boolean;
  date: string;
  point_count: number;
  distance_metres: number;
  points: TrailPoint[];
}

export async function getAgentsLive() {
  const { data } = await api.get<{
    agents: LiveAgentPosition[];
    sos_count: number;
    tracked_count: number;
  }>("/manager/agents/live");
  return data;
}

export async function getAgentTrail(agentId: string, date?: string, sosOnly = false) {
  const { data } = await api.get<AgentTrail>(`/manager/agents/${agentId}/trail`, {
    params: { ...(date ? { date } : {}), ...(sosOnly ? { sos_only: true } : {}) },
  });
  return data;
}

// ─── Allocation eligibility (2026-08-19) ────────────────────────────────────

export interface UnallocatedCase {
  case_id: string;
  case_number: string;
  customer_name: string | null;
  city: string | null;
  priority: string;
  target_amount: number;
  /** DO_NOT_CONTACT | NO_ELIGIBLE_AGENT | NO_AGENT_ON_DUTY | NO_CAPACITY | AWAITING_ALLOCATION */
  reason: string;
  detail: string;
}

export interface UnallocatedReport {
  cases: UnallocatedCase[];
  counts: Record<string, number>;
  total: number;
}

export async function getUnallocatedCases() {
  const { data } = await api.get<UnallocatedReport>("/manager/cases/unallocated");
  return data;
}

// ─── Field-visit anomaly detection (2026-08-19) ─────────────────────────────

export interface FraudFinding {
  /** IMPOSSIBLE_TRAVEL | OVERLAPPING_VISITS | PHOTO_LOCATION_MISMATCH
   *  | DUPLICATE_PHOTO | VISIT_TOO_SHORT | FAR_FROM_CUSTOMER */
  type: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  occurred_at: string;
  summary: string;
  evidence: Record<string, unknown>;
  agent_id: string;
  agent_name: string | null;
  employee_code: string | null;
  visit_id: string;
  case_id: string;
  case_number: string | null;
  /** A manager's standing verdict, or null if nobody has judged it yet. */
  review: { verdict: "CONFIRMED" | "DISMISSED"; note: string | null; reviewed_at: string | null } | null;
}

export interface AgentAnomalyTally {
  agent_id: string;
  agent_name: string | null;
  employee_code: string | null;
  total: number;
  high: number;
  confirmed: number;
}

export interface FraudReport {
  findings: FraudFinding[];
  counts: Record<string, number>;
  by_severity: Record<string, number>;
  /** Which agents account for the findings — one agent with twelve is a very
   *  different conversation from twelve agents with one each. */
  by_agent: AgentAnomalyTally[];
  /** Dismissed findings excluded from `findings`. Counted, never silently lost. */
  dismissed_hidden: number;
  visits_examined: number;
  date_from: string;
  date_to: string;
}

export async function getFraudAlerts(opts?: { dateFrom?: string; dateTo?: string; includeDismissed?: boolean }) {
  const { data } = await api.get<FraudReport>("/manager/fraud-alerts", {
    params: {
      ...(opts?.dateFrom ? { date_from: opts.dateFrom } : {}),
      ...(opts?.dateTo ? { date_to: opts.dateTo } : {}),
      ...(opts?.includeDismissed ? { include_dismissed: true } : {}),
    },
  });
  return data;
}

/** Record a verdict on one anomaly. Confirmed findings stay visible; dismissed
 *  ones leave the default view but are never deleted. Writes an audit entry. */
export async function reviewFraudAlert(
  visitId: string, findingType: string,
  verdict: "CONFIRMED" | "DISMISSED", note?: string,
) {
  const { data } = await api.post<{
    visit_id: string; finding_type: string; verdict: string;
    note: string | null; previous_verdict: string | null;
  }>("/manager/fraud-alerts/review", {
    visit_id: visitId, finding_type: findingType, verdict, note: note ?? null,
  });
  return data;
}

// ─── Smart Nightly Case Allocation & Beat Planning ───────────────────────────

export interface AllocationBeatItem {
  beat_id: string;
  agent_id: string;
  agent_name: string;
  agent_code: string;
  beat_number: string;
  total_cases: number;
  estimated_distance_km: number;
  estimated_duration_minutes: number;
  total_target_amount: number;
  /** Balance still owed across this beat's stops (target − collected). The
   *  beat cards show this so fifteen of them sum to the KPI above. Absent on a
   *  response from before 2026-09-16. */
  total_collectable_amount?: number;
  status: string;
}

export interface AllocationDecisionItem {
  decision_id: string;
  case_id: string;
  case_number: string;
  target_amount: number;
  /** target − collected: what is still owed on the case, the base the allocator
   *  multiplies. The reason line prints this. Absent before 2026-09-16. */
  collectable_amount?: number;
  outcome: "ALLOCATED" | "DEFERRED" | "DEFERRED_ROUTE_INFEASIBLE" | "BLOCKED" | string;
  allocated_agent_id: string | null;
  allocated_agent_name: string | null;
  visit_priority_score: number;
  fit_score: number;
  reason: string;
  score_breakdown: Record<string, unknown>;
  /**
   * What the model contributed to THIS decision, straight off the persisted
   * row. Added 2026-09-09: the response already carried ML-derived numbers —
   * the assignment and the rupee figure both come from the model — while
   * exposing nothing that said so, so a client could render a model-driven
   * decision with no way to know it was one.
   *
   * `probability_used` is null when the model did not drive the decision. A
   * client must NOT fall back to `shadow_prob_recovery` in that case: the two
   * answer different questions and differ by roughly 6x on the live book.
   */
  ml?: {
    used_for_decision: boolean;
    probability_used: number | null;
    borrower_p_recover: number | null;
    shadow_prob_recovery: number | null;
    value_transform: string | null;
    expected_recovery_inr: number | null;
    prediction_id: string | null;
    model_name: string | null;
    /** From the prediction row, never a constant — a hardcoded version would
     *  keep reporting the old model straight through a rollback. */
    model_version: string | null;
    feature_coverage: number | null;
  };
}

export interface AllocationPlanReport {
  has_plan: boolean;
  run_id?: string;
  plan_date?: string;
  strategy?: string;
  status?: string;
  total_cases_evaluated?: number;
  total_cases_allocated?: number;
  total_cases_deferred?: number;
  total_cases_blocked?: number;
  total_agents_planned?: number;
  expected_recovery_total?: number;
  /** Sum of `Case.target_amount` over the ALLOCATED cases — the lifetime figure,
   *  the same quantity the dashboard's "Today's Collections" target uses. */
  allocated_target_total?: number;
  /** Sum of `target_amount - collected_amount` over the ALLOCATED cases. This,
   *  not the target, is what the allocator multiplies by `prob_recovery_ml`, so
   *  it is the only denominator that yields the model's own recovery rate. */
  allocated_collectable_total?: number;
  created_at?: string;
  beats?: AllocationBeatItem[];
  decisions?: AllocationDecisionItem[];
  message?: string;
  // Set when the most recent allocation attempt for this date failed and has
  // not since been superseded by a successful run. Present on BOTH the
  // has_plan and no-plan responses: a manager who re-planned by hand still
  // needs to know the nightly job is broken, because tomorrow it breaks again.
  last_failure?: AllocationFailure | null;
}

export interface AllocationFailure {
  run_id: string;
  failed_at: string | null;
  error_type: string | null;
  error: string | null;
  trigger: string | null;
}

export async function getLatestAllocation(planDate?: string): Promise<AllocationPlanReport> {
  const { data } = await api.get<AllocationPlanReport>("/manager/allocation/latest", {
    params: planDate ? { plan_date: planDate } : {},
  });
  return data;
}

export async function triggerAllocationPlan(opts?: {
  strategy?: "SMART" | "LEGACY";
  objective?: "BALANCED" | "MAX_RECOVERY" | "MIN_DISTANCE";
  plan_date?: string;
  simulate?: boolean;
  force_replan?: boolean;
}) {
  // LONG_RUNNING_MS, not the 15s default: this endpoint solves the assignment,
  // calls OSRM and writes every beat before it answers. See api/axios.ts.
  const { data } = await api.post("/manager/allocation/plan", opts ?? {}, {
    timeout: LONG_RUNNING_MS,
  });
  return data;
}

export async function getAllocationSettings(): Promise<{
  objective: "BALANCED" | "MAX_RECOVERY" | "MIN_DISTANCE";
  max_territory_radius_km: number;
  max_daily_stops_per_agent: number;
}> {
  const { data } = await api.get("/manager/allocation/settings");
  return data;
}

export async function updateAllocationSettings(settings: {
  objective?: "BALANCED" | "MAX_RECOVERY" | "MIN_DISTANCE";
  max_territory_radius_km?: number;
  max_daily_stops_per_agent?: number;
}) {
  const { data } = await api.post("/manager/allocation/settings", settings);
  return data;
}

export async function rollbackAllocationPlan(runId: string) {
  // Also long: it deletes and rewrites the same beats the plan wrote.
  const { data } = await api.post("/manager/allocation/rollback", { run_id: runId },
    { timeout: LONG_RUNNING_MS });
  return data;
}

export async function exportAllocationDecisions(runId: string): Promise<Blob> {
  const response = await api.get("/manager/allocation/export-decisions", {
    params: { run_id: runId },
    responseType: "blob",
  });
  return response.data;
}

// ── Audit trail ─────────────────────────────────────────────────────────────
// Replaces the six hardcoded rows the Compliance page used to render under
// "Today's Audit Log". Scoped server-side to this manager's own team.

export interface AuditEntry {
  id: string;
  created_at: string | null;
  action: string;
  /** null when the row has no recorded actor. */
  actor_name: string | null;
  entity_type: string | null;
  entity_id: string | null;
  success: boolean;
  failure_reason: string | null;
  ip_address: string | null;
  details: Record<string, unknown> | null;
}

export interface AuditLogPage {
  window_days: number;
  since: string;
  total: number;
  limit: number;
  offset: number;
  entries: AuditEntry[];
  counts_by_action: Record<string, number>;
  /** What the trail does NOT record. Without this a short log reads as a
   *  quiet week rather than as missing instrumentation. */
  coverage: {
    declared_action_types: number;
    not_instrumented: string[];
    excludes_system_rows: boolean;
    note: string;
  };
}

export async function getAuditLog(limit = 50, offset = 0): Promise<AuditLogPage> {
  const { data } = await api.get<AuditLogPage>("/manager/audit-log", {
    params: { limit, offset },
  });
  return data;
}

export async function exportAuditLog(): Promise<Blob> {
  const response = await api.get("/manager/audit-log/export", { responseType: "blob" });
  return response.data;
}

// ─── Leave requests (2026-09-21) ─────────────────────────────────────────────
export type { LeaveRequest, LeaveType } from "@/api/agent";
export async function getLeaveRequests(status?: string): Promise<{ requests: LeaveRequest[]; pending: number }> {
  const { data } = await api.get("/manager/leave-requests", { params: status ? { status } : undefined });
  return data;
}
export async function decideLeave(id: string, decision: "approve" | "reject" | "revoke", note?: string): Promise<LeaveRequest & { cases_released_to_pool?: number }> {
  const { data } = await api.post(`/manager/leave-requests/${id}/${decision}`, { note: note || undefined });
  return data;
}
export async function markAgentLeave(agentId: string, body: { from_date: string; to_date: string; leave_type: LeaveType; reason?: string }): Promise<LeaveRequest & { cases_released_to_pool?: number }> {
  const { data } = await api.post(`/manager/agents/${agentId}/leave`, body);
  return data;
}

// ─── Manage Agents: create / edit / suspend / reactivate / reset-login (2026-09-28) ─
// Backed by manager_agents_admin.py, a separate router file sharing manager.py's
// "/manager" prefix — see that file's own changelog for why. `territory_region_id`
// is deliberately not in either body: there is no regions-list endpoint yet, so
// the field is left out of the UI entirely and the backend treats it as optional.

export interface CreateAgentBody {
  full_name: string;
  email: string;
  phone: string;
  employee_code: string;
  id_card_number: string;
  base_latitude: number;
  base_longitude: number;
  territory: string;
  gender?: string;
  specialization?: "SECURED" | "UNSECURED" | "BOTH";
  vehicle_type?: string;
  max_cases_per_day?: number;
  languages_spoken?: string[];
}

export interface CreateAgentResult {
  agent_id: string;
  user_id: string;
  employee_code: string;
  full_name: string;
  email: string;
  phone: string;
  territory: string;
  status: string;
  /** Whether the one-time set-password link was texted to the new agent.
   *  `sent: false` most often means no PUBLIC_BASE_URL is configured in this
   *  environment — the agent row still exists; Reset Login is the recovery path. */
  activation: { sent: boolean; expires_at?: string; error?: string };
}

export async function createAgent(body: CreateAgentBody): Promise<CreateAgentResult> {
  const { data } = await api.post<CreateAgentResult>("/manager/agents", body);
  return data;
}

/** Same shape as CreateAgentBody minus email/employee_code/id_card_number/
 *  territory (not editable — see manager_agents_admin.EditAgentRequest) — and
 *  every field optional: omitted means "leave unchanged", never "clear it".
 *  base_latitude/base_longitude must be sent together or not at all. */
export interface EditAgentBody {
  full_name?: string;
  phone?: string;
  territory?: string;
  base_latitude?: number;
  base_longitude?: number;
  gender?: string;
  specialization?: "SECURED" | "UNSECURED" | "BOTH";
  vehicle_type?: string;
  max_cases_per_day?: number;
  languages_spoken?: string[];
}

export async function editAgent(agentId: string, body: EditAgentBody): Promise<{ agent_id: string; changed: string[] }> {
  const { data } = await api.patch(`/manager/agents/${agentId}`, body);
  return data;
}

export async function suspendAgent(agentId: string, reason: string): Promise<{ agent_id: string; status: "SUSPENDED"; suspended_reason: string }> {
  const { data } = await api.post(`/manager/agents/${agentId}/suspend`, { reason });
  return data;
}

export async function reactivateAgent(agentId: string): Promise<{ agent_id: string; status: string }> {
  const { data } = await api.post(`/manager/agents/${agentId}/reactivate`);
  return data;
}

/** Ends the agent's current sessions and texts them a fresh set-password
 *  link. Can answer 503 (no PUBLIC_BASE_URL configured) or 429 (rate-limited,
 *  shared with create's own activation SMS) — both real on a local preview
 *  and neither a crash; callers surface them through errorDetail(). */
export async function resetAgentLogin(agentId: string): Promise<{ sent: boolean; expires_at?: string }> {
  const { data } = await api.post(`/manager/agents/${agentId}/reset-login`);
  return data;
}
