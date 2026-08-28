export type UserRole = "FIELD_AGENT" | "AGENCY_MANAGER" | "AGENCY_ADMIN";

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  role: UserRole;
  user_id: string;
  full_name: string;
}

export type DPDBucket = "CURRENT" | "BUCKET_1" | "BUCKET_2" | "BUCKET_3" | "NPA";
export type CaseStatus = "UNASSIGNED" | "ASSIGNED" | "IN_PROGRESS" | "PTP_SET" | "PARTIALLY_PAID" | "PAID" | "ESCALATED" | "CLOSED" | "WRITTEN_OFF";
export type CasePriority = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
/**
 * How much of a loan we expect to recover. Computed by the backend's
 * recovery scorecard, banded on the 90-day estimate.
 *
 * Note the direction is the OPPOSITE of CasePriority: HIGH here is good news.
 */
export type RecoveryPotential = "HIGH" | "MEDIUM" | "LOW";
export type AgentTier = "TIER_1" | "TIER_2" | "TIER_3";
export type AgentStatus = "ON_DUTY" | "OFF_DUTY" | "ON_LEAVE" | "SUSPENDED";
export type VisitOutcome =
  | "PAID_FULL"       // Full target amount collected
  | "PART_PAID"       // Partial amount collected, no PTP
  | "PTP"             // Promise to Pay — no money today
  | "PART_PAID_PTP"   // Partial payment + PTP for remainder
  | "BROKEN_PTP"      // Had a PTP, didn't honour it
  | "RTP"             // Refuse to Pay
  | "DISPUTE"         // Disputes loan / amount
  | "NOT_AVAILABLE"   // Customer not present
  | "ADDRESS_ISSUE"   // Wrong address / shifted
  | "DECEASED"        // Customer deceased
  | "REVISIT";        // Needs revisit

export type PersonMet =
  | "BORROWER" | "CO_BORROWER" | "SPOUSE" | "PARENT"
  | "SIBLING" | "CHILD" | "RELATIVE" | "EMPLOYER"
  | "NEIGHBOR" | "SECURITY" | "OTHER";

export type DefaultReason =
  | "JOB_LOSS" | "SALARY_CUT" | "BUSINESS_FAILURE" | "MEDICAL"
  | "DEATH_IN_FAMILY" | "MARITAL_DISPUTE" | "ALREADY_PAID"
  | "AMOUNT_DISPUTED" | "FRAUD_CLAIM" | "OVER_LEVERAGED" | "OTHER";
export type PaymentMode = "CASH" | "UPI" | "NEFT" | "RTGS" | "CHEQUE" | "DD" | "ONLINE";
export type RiskCategory = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Agent {
  id: string;
  user_id: string;
  employee_code: string;
  id_card_number: string;
  full_name: string;
  territory: string;
  status: AgentStatus;
  tier: AgentTier;
  ranking_score: number;
  current_month_visits: number;
  current_month_collections: number;
  current_month_ptps_set: number;
  current_month_ptps_honored: number;
  lifetime_collection_rate: number;
  last_known_latitude: number | null;
  last_known_longitude: number | null;
  sos_active: boolean;
  max_cases_per_day: number;
  specialization: string;
  languages_spoken: string[];
  today_collected: number;
  today_target: number;
  ptp_rate_pct: number;
  collection_rate_pct: number;
  /** Complete months only, oldest first — the in-progress month is excluded so
   *  the trend is not dragged down by a part-month. rate_pct is null for a
   *  month with no snapshot, which renders as a break in the line. */
  collection_rate_trend: { month: string; rate_pct: number | null }[];
  /** Change in points between the two most recent complete months. */
  collection_rate_delta_pts: number | null;
}

export interface Customer {
  id: string;
  customer_ref: string;
  full_name: string;
  phone_primary: string;
  address_line1: string;
  address_line2: string | null;
  pincode: string;
  city: string;
  state: string;
  latitude: number;
  longitude: number;
  // The agent API sends these with a LEADING UNDERSCORE. schemas/agent.py
  // declares them under plain names with the underscore key as the alias, and
  // FastAPI's response_model_by_alias=True default means the alias is what
  // goes on the wire. Declaring them here without the underscore claimed a
  // field that never arrives — `case.customer.risk_score` typechecked fine and
  // was `undefined` at runtime. Fixed 2026-08-21 by correcting the TYPE, not
  // the wire: changing the alias would break the agent response contract to
  // fix a field nothing renders.
  _risk_category?: RiskCategory;
  _risk_score?: number;
  _fraud_flag?: boolean;
  cibil_score: number | null;
  is_hostile: boolean;
  language_preference: string;
}

export interface Loan {
  id: string;
  loan_account_number: string;
  loan_account_masked: string | null;
  loan_type: string;
  bank_name: string;
  dpd: number;
  dpd_bucket: DPDBucket;
  status: string;
  npa_flag: boolean;
  // financial — only shown after borrower verification in detail page
  total_outstanding: number;
  overdue_amount: number;
  emi_amount: number;
  outstanding_principal: number;
  outstanding_interest: number;
  penal_charges: number;
  interest_rate: number;
  tenure_months: number;
  last_payment_date: string | null;
  last_payment_amount: number | null;
  next_due_date: string | null;
  legal_status: string;
  settlement_status: string;
}

/**
 * The recovery estimate for one loan, as the manager endpoints send it.
 *
 * Read from the nightly snapshot, not from Loan.recovery_potential — that column
 * stays untouched until RECOVERY_WRITE_LABEL is enabled on the backend.
 *
 * `is_modelled` is always false: this is a hand-weighted scorecard with no
 * training behind it, so there is no accuracy figure and none is sent. Do not
 * render it as a model output.
 */
export interface RecoveryScore {
  recovery_potential: RecoveryPotential;
  rate_30: number;
  rate_60: number;
  rate_90: number;
  label_horizon_days: number;
  speed_index: number | null;
  evidence_coverage: number | null;
  model_version: string | null;
  source: string | null;
  is_modelled: boolean;
  as_of: string | null;
}

export interface Case {
  id: string;
  case_number: string;
  customer: Customer;
  loan: Loan;
  /**
   * There is deliberately no pre-summed arrears figure here. `due_now`
   * (overdue_amount + penal_charges) lived on this type from 2026-08-24 and was
   * removed on 2026-08-27: on a case row it was the largest rupee number
   * present, so it read as the amount to collect. Use loan.overdue_amount and
   * loan.penal_charges, and say which one you mean.
   */
  /** null when this loan has not been scored yet — render "Not scored", not LOW. */
  recovery?: RecoveryScore | null;
  agent_id: string | null;
  agent_name?: string | null;
  status: CaseStatus;
  priority: CasePriority;
  target_amount: number;
  collected_amount: number;
  allocation_date: string | null;
  collection_stage: string | null;
  visit_count: number;
  max_visits_allowed: number;
  is_escalated: boolean;
  handover_notes: string | null;
  is_visited_today?: boolean;
  ptp_due_today?: boolean;
  /** Why this case is worth visiting, and how much. See
   *  backend/app/ml/visit_priority.py. Null when the loan carried no balance to
   *  score — such a case sorts last, never first. */
  visit_priority?: VisitPriority | null;
}

/** One of the three named terms behind a visit-priority score. Always all three,
 *  in a fixed order, so a manager comparing two cases reads the same rows in the
 *  same places. */
export interface VisitPriorityComponent {
  code: "RECOVERABLE_VALUE" | "URGENCY" | "EFFORT";
  points: number;
  summary: string;
  evidence?: Record<string, unknown>;
  /** True when the term had no data. Distinguishes "measured and low" from
   *  "not measured" — the two read identically without it. */
  abstained?: boolean;
}

export interface VisitPriority {
  score: number;
  /** HIGH | MEDIUM | LOW — gives the bare score a meaning. */
  band: "HIGH" | "MEDIUM" | "LOW";
  components: VisitPriorityComponent[];
  reason: string;
  /** Always false. A hand-weighted scorecard, not a learned model. */
  is_modelled: boolean;
  model_version: string;
  /** How old the recovery rate behind the value term is. */
  rate_as_of?: string | null;
}

export interface Beat {
  id: string;
  beat_date: string;
  beat_number: string;
  ordered_case_ids: string[];
  total_cases: number;
  estimated_distance_km: number;
  estimated_duration_minutes: number;
  total_target_amount: number;
  status: "PLANNED" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED";
  cases_completed: number;
  amount_collected: number;
}

export interface PTP {
  id: string;
  committed_amount: number;
  committed_date: string;
  status: "ACTIVE" | "HONORED" | "BROKEN" | "PARTIALLY_HONORED" | "EXPIRED" | "RESCHEDULED";
  customer_reason: string | null;
}

export interface DashboardSummary {
  total_agents: number;
  agents_on_duty: number;
  total_cases: number;
  cases_assigned: number;
  cases_today: number;
  cases_resolved_today: number;
  visits_today: number;
  amount_collected_today: number;
  amount_target_today: number;
  collection_rate_today: number;   // already a percentage (0–100)
  ptps_due_today: number;
  sos_active_count: number;
  /**
   * The day the "today" figures above were measured against (latest beat date,
   * ISO yyyy-mm-dd). Optional: an offline snapshot captured before this field
   * existed won't carry it, so consumers must fall back to the wall clock.
   */
  effective_date?: string;
}
