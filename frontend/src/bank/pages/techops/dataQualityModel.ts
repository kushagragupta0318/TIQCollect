// The Data Quality page's types, formatting and fetch — beside the page
// rather than in it, same reasoning usageCostModel.ts states (fast refresh
// + the lint rule that guards it).
import api from "@/api/axios";

export interface FeedFreshness {
  feed_type: string;
  last_business_date: string | null;
  last_received_at: string | null;
  rows_total: number | null;
  rows_accepted: number | null;
  rows_quarantined: number | null;
  rows_skipped: number | null;
  days_stale: number | null;
}

export interface QuarantinedByReason {
  reason: string;
  rows: number;
}

export interface QuarantinedRow {
  row_no: number;
  loan_account_number: string | null;
  customer_ref: string | null;
  case_number: string | null;
  feed_type: string;
  business_date: string;
  dq_errors: { reason: string; detail: string }[];
  created_at: string;
}

export interface DuplicateCustomerPhone {
  phone_primary: string;
  customers: number;
}

export interface OutOfRangeLoan {
  loan_id: string;
  loan_account_number: string;
  overdue_amount: number;
  total_outstanding: number;
  outstanding_principal: number;
}

export interface DataQualityPayload {
  feed_freshness: FeedFreshness[];
  quarantined_by_reason: QuarantinedByReason[];
  quarantined_sample: QuarantinedRow[];
  duplicate_customer_phones: { count: number; sample: DuplicateCustomerPhone[] };
  out_of_range_loans: { count: number; sample: OutOfRangeLoan[] };
}

export async function getDataQuality(): Promise<DataQualityPayload> {
  const { data } = await api.get<DataQualityPayload>("/bank/data-quality");
  return data;
}

/** SCREAMING_SNAKE reads as shouting in a table — same reasoning
 *  usageCostModel.featureLabel and auditModel.actionLabel both state. */
export function reasonLabel(reason: string): string {
  return reason.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function rupees(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

/** No reading at all is not "fresh" — distinct from a feed that has run
 *  before but has gone quiet. */
export function freshnessLabel(f: FeedFreshness): string {
  if (f.days_stale == null) return "No reading to compare against";
  if (f.days_stale <= 1) return "Fresh";
  return `${f.days_stale} days stale`;
}
