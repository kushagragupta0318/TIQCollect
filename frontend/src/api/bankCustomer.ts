// The bank's borrower page (C08): GET /bank/customers/{id}/360,
// GET /bank/loans/{id}/customer-360, GET /bank/cases/{id}/timeline.
//
// Nothing here is summed across cases. `latest_probability` is THIS case's
// P(no material payment next cycle) — never a borrower-level number; the
// explanation panel states its polarity and provenance.
import api from "./axios";

export interface CustomerHeader {
  customer_id: string;
  full_name: string;
  phone_primary: string | null;
  address_line1: string | null;
  city: string | null;
  state: string | null;
  pincode: string | null;
  /** Masked at the source; there is no unmasked form. */
  pan_masked: string | null;
  aadhaar_masked: string | null;
  language_preference: string | null;
  is_hostile: boolean;
  do_not_contact: boolean;
  tags: string[];
}

export interface CaseRow {
  case_id: string;
  case_number: string;
  status: string;
  agency_id: string | null;
  agency_name: string | null;
  placed_on: string | null;
  agent_id: string | null;
  agent_name: string | null;
  loan_id: string;
  loan_type: string | null;
  dpd: number | null;
  dpd_bucket: string | null;
  total_outstanding: number | null;
  overdue_amount: number | null;
  target_amount: number | null;
  collected_verified: number | null;
  last_visit_at: string | null;
  last_visit_outcome: string | null;
  last_call_at: string | null;
  last_call_outcome: string | null;
  last_contact_at: string | null;
  active_ptp_date: string | null;
  active_ptp_amount: number | null;
  has_open_dispute: boolean | null;
  is_escalated: boolean | null;
  latest_probability: number | null;
  latest_band: string | null;
  latest_model_version: string | null;
  latest_disposition: string | null;
}

export interface LoanWithoutCase {
  loan_id: string;
  loan_account_number: string;
  loan_type: string | null;
  dpd: number;
  total_outstanding: number;
  overdue_amount: number;
}

export interface Customer360 {
  customer: CustomerHeader;
  cases: CaseRow[];
  loans_without_cases: LoanWithoutCase[];
  /** True when a region limit narrowed what is shown: a partial borrower. */
  region_limited: boolean;
  loans_truncated: boolean;
}

export type TimelineKind = "VISIT" | "CALL" | "PAYMENT" | "PTP";

export interface TimelineEntry {
  kind: TimelineKind;
  at: string | null;
  actor_type: string;
  actor_id: string | null;
  entity_id: string;
  detail: Record<string, unknown>;
}

export interface CaseTimeline {
  case_id: string;
  entries: TimelineEntry[];
  truncated: boolean;
  limit: number;
}

export async function getCustomer360(customerId: string): Promise<Customer360> {
  const { data } = await api.get<Customer360>(`/bank/customers/${customerId}/360`);
  return data;
}

/** The entry from Placements, which lists loans rather than customers. */
export async function getCustomer360ForLoan(loanId: string): Promise<Customer360> {
  const { data } = await api.get<Customer360>(`/bank/loans/${loanId}/customer-360`);
  return data;
}

export async function getCaseTimeline(caseId: string): Promise<CaseTimeline> {
  const { data } = await api.get<CaseTimeline>(`/bank/cases/${caseId}/timeline`);
  return data;
}

export interface CustomerHit {
  customer_id: string;
  full_name: string;
  city: string | null;
  /** Loans of this borrower the CALLER may see, not the borrower's whole book. */
  loans: number;
  first_account: string | null;
}

export interface CustomerSearch {
  items: CustomerHit[];
  /** The query was shorter than `min_query_length`: empty for that reason, not
   *  because nothing matched. The two are never confused in the UI. */
  query_too_short: boolean;
  min_query_length: number;
  /** More matched than were returned. There is no page 2 by design. */
  truncated: boolean;
}

/** Borrowers of the caller's own bank, inside their region limit. The bank is
 *  never a parameter, and a borrower outside the caller's scope is returned as
 *  nothing at all, indistinguishable from a genuine no-match. */
export async function searchCustomers(q: string): Promise<CustomerSearch> {
  const { data } = await api.get<CustomerSearch>("/bank/customers/search", { params: { q } });
  return data;
}
