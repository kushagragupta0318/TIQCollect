// Quarantine review and release — the client half of the Phase 6 API
// (backend/app/api/v1/endpoints/bank_feed_quarantine.py). Types mirror the
// server's real response shape (feed_quarantine_service.row_dict and
// release_rows) field for field; a field the API does not return is not shown.
//
// The bank is NEVER sent from here: the server reads it from the authenticated
// context, so there is no bank id to pass and none to tamper with. Whether the
// caller may release is likewise the SERVER's answer (a 403), not a role list
// copied into the client — core/permissions.py stays the only permission model.
import api from "@/api/axios";

/** Mirrors feed_quarantine_service.row_dict. */
export interface QuarantineRow {
  row_id: string;
  row_no: number;
  status: string;
  loan_account_number: string | null;
  customer_ref: string | null;
  case_number: string | null;
  reason: string | null;
  detail: string | null;
  /** How many times this row has been refused — a re-try appends, never replaces. */
  attempts: number;
  releasable: boolean;
  /** Why not, when `releasable` is false. */
  blocked_because: string | null;
  processed_at: string | null;
  released_at: string | null;
  released_by: string | null;
  /** Every column as received. Only present when include_raw was requested. */
  raw?: Record<string, unknown>;
}

/** Mirrors feed_quarantine_service.list_quarantined. Counts are the database's
 *  own, over the whole batch rather than the page returned. */
export interface QuarantineList {
  batch_id: string;
  batch_status: string;
  batch_reviewable: boolean;
  business_date: string | null;
  file_name: string | null;
  total_quarantined: number;
  total_blocked: number;
  total_releasable: number;
  released_count: number;
  /**
   * Whether this response actually carries `raw` borrower data (Phase 9).
   * Asking for it is not the same as being allowed it: the server honours
   * include_raw only for a caller holding bank_feed.pii.read, and only once
   * the read is audited. False with a reason means it was withheld.
   */
  raw_pii_available?: boolean;
  /** "not_authorised" | "audit_unavailable" | null */
  raw_pii_withheld_reason?: string | null;
  items: QuarantineRow[];
}

export type ReleaseOutcome =
  | "RELEASED"
  | "STILL_REFUSED"
  | "NOT_RELEASABLE"
  | "ALREADY_RELEASED"
  | "NOT_FOUND";

export interface ReleaseVerdict {
  row_id: string;
  row_no?: number;
  outcome: ReleaseOutcome;
  reason?: string | null;
  detail?: string | null;
  blocked_because?: string | null;
  placement_id?: string;
  case_id?: string;
  case_number?: string;
  agency_id?: string;
  released_at?: string | null;
}

export interface ReleaseResult {
  batch_id: string;
  batch_status: string;
  released: number;
  still_refused: number;
  not_releasable: number;
  already_released: number;
  not_found: number;
  remaining_quarantined: number;
  verdicts: ReleaseVerdict[];
}

/** The server's cap (feed_quarantine_service.MAX_RELEASE_ROWS). Shown to the
 *  user; the SERVER enforces it and remains authoritative. */
export const MAX_RELEASE_ROWS = 200;

export async function getQuarantine(
  batchId: string,
  opts: { includeRaw?: boolean; limit?: number; offset?: number } = {},
): Promise<QuarantineList> {
  const { data } = await api.get(`/bank/feed/${batchId}/quarantine`, {
    params: {
      include_raw: opts.includeRaw ? true : undefined,
      limit: opts.limit ?? 100,
      offset: opts.offset ?? 0,
    },
  });
  return data as QuarantineList;
}

export async function releaseRows(batchId: string, rowIds: string[]): Promise<ReleaseResult> {
  const { data } = await api.post(`/bank/feed/${batchId}/quarantine/release`, { row_ids: rowIds });
  return data as ReleaseResult;
}

/**
 * What a release verdict MEANS to a bank user.
 *
 * The distinction that matters: RELEASED means the row was placed with an
 * agency and a case now exists — not merely that the request succeeded. A row
 * whose reason is still unfixed comes back STILL_REFUSED and is deliberately
 * NOT reported as a success, because the rules refused it again.
 */
export function outcomeMeaning(outcome: ReleaseOutcome): {
  label: string;
  tone: "good" | "warn" | "bad" | "pending";
  help: string;
} {
  switch (outcome) {
    case "RELEASED":
      return { label: "Released", tone: "good",
               help: "Placed with the agency and a case was opened." };
    case "STILL_REFUSED":
      return { label: "Still refused", tone: "warn",
               help: "The rules refused this row again — the reason it was held has not been resolved yet. It stays quarantined." };
    case "NOT_RELEASABLE":
      return { label: "Cannot be released here", tone: "bad",
               help: "This row never created a loan, so there is nothing to place. Fix the branch and re-ingest the file." };
    case "ALREADY_RELEASED":
      return { label: "Already handled", tone: "pending",
               help: "This row was released earlier. Nothing was done a second time." };
    case "NOT_FOUND":
      return { label: "Not found", tone: "bad",
               help: "This row is not part of the batch." };
    default:
      // An unknown outcome is shown verbatim rather than guessed at, and never
      // as success.
      return { label: String(outcome), tone: "pending", help: "" };
  }
}

/** SCREAMING_SNAKE reads as shouting in a table — the same reasoning
 *  dataQualityModel.reasonLabel states. Reused rather than restated. */
export { reasonLabel } from "./dataQualityModel";
