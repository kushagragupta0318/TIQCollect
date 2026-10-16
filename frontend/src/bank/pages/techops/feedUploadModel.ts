// Tech Ops › Loan Feed — the client half of the Phase 3 upload API
// (backend/app/api/v1/endpoints/bank_feed.py). Types mirror the server's real
// response shape (services/bank/feed_upload_service.batch_dict and
// _validation_dict) field for field: nothing here is invented, and a field the
// API does not return is not displayed.
//
// The bank is NEVER sent from here. The server reads it from the authenticated
// context, so there is no bank id to pass and none to tamper with.
import api from "@/api/axios";

/** Mirrors feed_upload_service._validation_dict. */
export interface FeedValidation {
  ok: boolean;
  rows: number;
  headers: string[];
  missing_headers: string[];
  /** Stops ingestion. */
  blocking: string[];
  /** Does not stop ingestion — rows will be skipped or quarantined with reasons. */
  warnings: string[];
  duplicate_rows_in_file: number;
}

/** Mirrors feed_upload_service.batch_dict. */
export interface FeedBatch {
  batch_id: string;
  feed_type: string;
  business_date: string | null;
  file_name: string | null;
  file_sha256: string;
  received_via: string;
  uploaded_by: string | null;
  status: string;
  rows_total: number | null;
  rows_accepted: number | null;
  rows_quarantined: number | null;
  rows_skipped: number | null;
  received_at: string | null;
  loaded_at: string | null;
}

/** POST /bank/feed — the batch, plus whether this exact file was already received. */
export interface FeedUploadResult extends FeedBatch {
  duplicate: boolean;
  /**
   * Whether the ingestion task was actually enqueued (feed_upload_service.
   * _dispatch). FALSE is a real, reportable state: the batch is committed and
   * durable, but the broker did not take the prompt, so processing waits for
   * the recovery sweep. Showing a plain success here would tell the user their
   * feed is being processed when nothing is working on it.
   */
  queued: boolean;
  validation: FeedValidation;
}

/** POST /bank/feed/validate — a dry run; writes nothing. */
export interface FeedValidateResult extends FeedValidation {
  file_name: string;
}

export interface FeedHistory {
  total: number;
  items: FeedBatch[];
}

/** Mirrors feed_upload_service.MAX_UPLOAD_BYTES. Shown to the user and used for
 *  a courtesy check; the SERVER enforces it (413) and remains authoritative. */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export async function validateFeed(file: File): Promise<FeedValidateResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  const { data } = await api.post("/bank/feed/validate", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
  return data as FeedValidateResult;
}

export async function uploadFeed(file: File, businessDate?: string): Promise<FeedUploadResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (businessDate) form.append("business_date", businessDate);
  const { data } = await api.post("/bank/feed", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
  return data as FeedUploadResult;
}

export async function getFeedHistory(limit = 25, offset = 0): Promise<FeedHistory> {
  const { data } = await api.get("/bank/feed", { params: { limit, offset } });
  return data as FeedHistory;
}

export async function getFeedBatch(batchId: string): Promise<FeedBatch> {
  const { data } = await api.get(`/bank/feed/${batchId}`);
  return data as FeedBatch;
}

/** Bytes as a short human string, for the selected-file line. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * What a batch status MEANS to a bank user.
 *
 * RECEIVED still means NOTHING IS IN THE LOAN BOOK. A worker now picks the
 * batch up (workers/tasks/feed_ingestion), but it finishes after the upload
 * response, so the only honest thing to say at this point is that the file is
 * staged and queued — never "ingested". Unknown statuses fall through to the
 * raw value rather than being guessed at.
 */
export function statusMeaning(status: string): { label: string; tone: "pending" | "good" | "warn" | "bad"; help: string } {
  switch (status) {
    case "RECEIVED":
      return { label: "Received", tone: "pending",
               help: "Staged and queued for processing. Not yet in the loan book." };
    case "VALIDATING":
      return { label: "Validating", tone: "pending", help: "Being checked row by row." };
    case "LOADED":
      return { label: "Loaded", tone: "good", help: "Processed into the loan book." };
    case "PARTIAL":
      return { label: "Partially loaded", tone: "warn",
               help: "Processed, but some rows were skipped or quarantined." };
    // REJECTED is the status the DB constraint actually allows for a bad run
    // (models/lending.FEED_STATUSES = RECEIVED, VALIDATING, LOADED, PARTIAL,
    // REJECTED). It was missing, so a rejected feed fell through to the
    // default below and rendered as a neutral "pending" tone with no
    // explanation — reading as still-in-progress, which is the opposite of
    // what happened. FAILED is kept beside it: scripts/ingest_daily's
    // IngestionResult.status returns that word, so whichever of the two a
    // future handoff writes, the row explains itself rather than going blank.
    case "REJECTED":
      return { label: "Rejected", tone: "bad",
               help: "Not processed — the file was refused. Nothing entered the loan book." };
    case "FAILED":
      return { label: "Failed", tone: "bad", help: "Processing did not complete." };
    default:
      // An unknown status is shown verbatim rather than hidden, but never as
      // "good": a status this build does not know about has not been verified
      // to mean success.
      return { label: status, tone: "pending", help: "" };
  }
}
