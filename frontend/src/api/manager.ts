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
import api from "./axios";
import type { DashboardSummary, Agent } from "@/types";

export async function getDashboard(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>("/manager/dashboard");
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

export async function getCompliance() {
  const { data } = await api.get("/manager/compliance");
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
  collection_stage: string | null;
  bank_ptp_date: string | null;
  bank_ptp_amount: number | null;
  bank_agent_remarks: string | null;
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
  };
  loan: {
    id: string;
    loan_account_number: string;
    loan_type: string;
    bank_name: string;
    total_outstanding: number;
    overdue_amount: number;
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
  collection_rate_pct: number;
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
}

export async function getMonthlyReport(month: string, agentId?: string): Promise<MonthlyReport> {
  const params: Record<string, string> = { month };
  if (agentId) params.agent_id = agentId;
  const { data } = await api.get<MonthlyReport>("/manager/ai/monthly-report", { params });
  return data;
}

export interface AnalyticsData {
  monthly_trend: MonthlyTrend[];
  dpd_breakdown: DPDBreakdown[];
  leaderboard: LeaderboardEntry[];
  leave_summary: { total_leave_days_30d: number; by_type: Record<string, number> };
  kpis: {
    overall_collection_rate_pct: number;
    total_collected_lakhs: number;
    total_target_lakhs: number;
    ptp_conversion_rate_pct: number;
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
