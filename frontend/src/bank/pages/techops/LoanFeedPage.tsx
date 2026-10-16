// Tech Ops › Loan Feed — upload the daily overdue-loan CSV and read this
// bank's ingestion history. Front end for the Phase 3 API
// (backend/app/api/v1/endpoints/bank_feed.py).
//
// WHAT THIS PAGE PROMISES. A successful upload means the file was validated,
// staged, RECORDED and queued — not that it is in the loan book. Processing is
// a background worker (workers/tasks/feed_ingestion) that finishes after this
// response, so the page says "queued for processing" and never "ingested";
// the history table below is where the outcome actually shows up. It also
// reports `queued: false` rather than hiding it: a batch whose dispatch was
// lost is durable but is not being worked on until the recovery sweep.
//
// AUTHORISATION is the server's: every route is gated on bank_feed.upload, and
// the bank comes from the token. The 403 handling here is usability only — it
// does not decide anything, and it deliberately does not restate the capability
// matrix (core/permissions.py), which would be a second copy that could drift.
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, FileText, X, CheckCircle2, AlertTriangle, Info } from "lucide-react";

import { errorDetail, errorStatus } from "@/lib/apiError";
import QuarantineReview from "./QuarantineReview";
import {
  MAX_UPLOAD_BYTES, formatBytes, getFeedBatch, getFeedHistory, statusMeaning,
  uploadFeed, validateFeed,
  type FeedBatch, type FeedUploadResult, type FeedValidateResult,
} from "./feedUploadModel";
import { AnalyticsError, AnalyticsLoading, Panel, Tile } from "../../components/analytics";
import { DataTable, type DataColumn } from "../../components/DataTable";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";

const CAPABILITY_REFUSED =
  "Uploading a loan feed needs a capability this role does not hold.";

function fmtDateTime(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

const TONE_CLASS: Record<string, string> = {
  good: "text-success-600",
  warn: "text-warning-600",
  bad: "text-destructive",
  pending: "text-muted-foreground",
};

function StatusCell({ status }: { status: string }) {
  const m = statusMeaning(status);
  return (
    <span className={`text-[12px] font-semibold ${TONE_CLASS[m.tone]}`} title={m.help}>
      {m.label}
    </span>
  );
}

const HISTORY_COLUMNS: DataColumn<FeedBatch>[] = [
  { key: "business_date", header: "Business date", render: (b) => b.business_date ?? "—" },
  { key: "file_name", header: "File", render: (b) => b.file_name ?? "—" },
  { key: "feed_type", header: "Feed" },
  { key: "status", header: "Status", render: (b) => <StatusCell status={b.status} /> },
  { key: "rows_total", header: "Rows", render: (b) => (b.rows_total ?? "—") },
  // Only shown once processing has filled them in; never invented.
  { key: "rows_quarantined", header: "Quarantined", render: (b) => (b.rows_quarantined ?? "—") },
  { key: "received_at", header: "Received", render: (b) => fmtDateTime(b.received_at) },
];

export default function LoanFeedPage() {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [validation, setValidation] = useState<FeedValidateResult | null>(null);
  const [result, setResult] = useState<FeedUploadResult | null>(null);
  const [openBatchId, setOpenBatchId] = useState<string | null>(null);

  const history = useQuery({
    queryKey: ["bank", "feed", "history"],
    queryFn: () => getFeedHistory(25, 0),
    retry: false,
  });

  const detail = useQuery({
    queryKey: ["bank", "feed", "batch", openBatchId],
    queryFn: () => getFeedBatch(openBatchId as string),
    enabled: !!openBatchId,
    retry: false,
  });

  const validateM = useMutation({
    mutationFn: (f: File) => validateFeed(f),
    onSuccess: (v) => setValidation(v),
    onSettled: () => { inFlight.current = false; },
  });

  const uploadM = useMutation({
    mutationFn: (f: File) => uploadFeed(f),
    onSuccess: (r) => {
      setResult(r);
      void queryClient.invalidateQueries({ queryKey: ["bank", "feed", "history"] });
    },
    onSettled: () => { inFlight.current = false; },
  });

  // One in-flight request at a time.
  //
  // `disabled` alone is NOT enough: React batches the state update, so two or
  // three clicks dispatched in the same tick all see the still-enabled button
  // and each fires a request (caught by a test — it sent 3). This ref flips
  // synchronously on the first click, so the later ones return immediately.
  // The server is the real guarantee either way: a repeat returns the SAME
  // batch with duplicate: true rather than ingesting twice.
  const inFlight = useRef(false);
  const busy = validateM.isPending || uploadM.isPending;

  function submitOnce(run: () => void) {
    if (inFlight.current) return;
    inFlight.current = true;
    run();
  }

  function pick(next: File | null) {
    setFile(next);
    setValidation(null);
    setResult(null);
    inFlight.current = false;
    validateM.reset();
    uploadM.reset();
  }

  function clearFile() {
    pick(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  const tooBig = !!file && file.size > MAX_UPLOAD_BYTES;
  const blocked = (validation && !validation.ok) || tooBig;

  const uploadError = uploadM.isError
    ? (errorStatus(uploadM.error) === 403
        ? CAPABILITY_REFUSED
        : errorDetail(uploadM.error, "The file could not be uploaded."))
    : null;
  const validateError = validateM.isError
    ? (errorStatus(validateM.error) === 403
        ? CAPABILITY_REFUSED
        : errorDetail(validateM.error, "The file could not be checked."))
    : null;

  return (
    <PageRoot>
      <ToolHeader
        title="Loan Feed"
        icon={Upload}
        description="Upload the daily overdue-loan CSV for your bank. Files are validated and staged for processing; nothing reaches the loan book until it is processed."
      />

      <Panel title="Upload a feed" hint={`CSV only · up to ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`}>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          data-testid="feed-file-input"
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />

        {!file ? (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="flex w-full flex-col items-center gap-2 rounded-card border border-dashed border-border px-6 py-10 text-center transition-colors hover:bg-primary/5"
          >
            <Upload size={20} className="text-muted-foreground" />
            <span className="text-[13px] font-semibold text-foreground">Choose a CSV file</span>
            <span className="text-[11.5px] text-muted-foreground">
              Your bank is taken from your sign-in — there is nothing else to pick.
            </span>
          </button>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-border px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <FileText size={16} className="shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="truncate text-[13px] font-semibold text-foreground">{file.name}</p>
                <p className="text-[11.5px] text-muted-foreground">{formatBytes(file.size)}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => submitOnce(() => validateM.mutate(file))}
                disabled={busy || tooBig}
                className="rounded-control border border-border px-3 py-1.5 text-[12px] font-semibold disabled:opacity-50"
              >
                {validateM.isPending ? "Checking…" : "Check file"}
              </button>
              <button
                type="button"
                onClick={() => submitOnce(() => uploadM.mutate(file))}
                disabled={busy || blocked}
                className="rounded-control bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
                data-testid="feed-upload-button"
              >
                {uploadM.isPending ? "Uploading…" : "Upload"}
              </button>
              <button
                type="button"
                onClick={clearFile}
                disabled={busy}
                aria-label="Remove file"
                className="rounded-control border border-border p-1.5 disabled:opacity-50"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        )}

        {tooBig && (
          <p role="alert" className="mt-3 text-[12.5px] text-destructive">
            This file is {formatBytes(file.size)} — larger than the {MAX_UPLOAD_BYTES / (1024 * 1024)} MB limit.
          </p>
        )}

        {validateError && (
          <p role="alert" className="mt-3 text-[12.5px] text-destructive">{validateError}</p>
        )}

        {validation && (
          <div className="mt-4 rounded-card border border-border px-4 py-3" data-testid="feed-validation">
            <p className="flex items-center gap-1.5 text-[12.5px] font-semibold">
              {validation.ok
                ? <><CheckCircle2 size={14} className="text-success-600" /> Ready to upload — {validation.rows} row(s)</>
                : <><AlertTriangle size={14} className="text-destructive" /> This file cannot be ingested</>}
            </p>
            {validation.blocking.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-[12px] text-destructive">
                {validation.blocking.map((b) => <li key={b}>{b}</li>)}
              </ul>
            )}
            {validation.warnings.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-[12px] text-warning-600">
                {validation.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}
          </div>
        )}

        {uploadError && (
          <p role="alert" className="mt-3 text-[12.5px] text-destructive" data-testid="feed-upload-error">
            {uploadError}
          </p>
        )}

        {result && (
          <div className="mt-4 rounded-card border border-border px-4 py-3" data-testid="feed-upload-result">
            {result.duplicate ? (
              <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-warning-600">
                <Info size={14} /> Already received — this exact file was uploaded for {result.business_date}. No second ingestion was created.
              </p>
            ) : (
              <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-success-600">
                <CheckCircle2 size={14} /> Received — {result.rows_total ?? 0} row(s) queued for processing.
              </p>
            )}
            {/* Said plainly, because "uploaded" is routinely misread as "live". */}
            <p className="mt-1 text-[11.5px] text-muted-foreground">
              Status <span className="font-semibold">{statusMeaning(result.status).label}</span>. Nothing has been
              written to the loan book yet — refresh the history below to see the result.
            </p>
            {/* A committed batch nobody was told about. Durable, but not moving
                until the 15-minute sweep finds it — so it is said, not hidden. */}
            {result.queued === false && !result.duplicate && (
              <p className="mt-2 flex items-start gap-1.5 text-[11.5px] font-semibold text-warning-600"
                 data-testid="feed-upload-not-queued">
                <AlertTriangle size={13} className="mt-px shrink-0" />
                Your file is saved, but the processing queue did not accept it yet. It will be picked up
                automatically within 15 minutes — no need to upload it again.
              </p>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Ingestion history" hint="Your bank's feeds, newest first">
        {history.isPending && <AnalyticsLoading label="Loading ingestion history…" />}
        {history.isError && (
          <div>
            <AnalyticsError>
              {errorStatus(history.error) === 403
                ? CAPABILITY_REFUSED
                : errorDetail(history.error, "The ingestion history could not be loaded.")}
            </AnalyticsError>
            <button
              type="button"
              onClick={() => history.refetch()}
              className="mt-3 rounded-control border border-border px-3 py-1.5 text-[12px] font-semibold"
            >
              Retry
            </button>
          </div>
        )}
        {history.data && history.data.items.length === 0 && (
          <p className="py-6 text-center text-[12.5px] text-muted-foreground">
            No feeds uploaded yet.
          </p>
        )}
        {history.data && history.data.items.length > 0 && (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Tile label="Feeds received" value={history.data.total} />
              <Tile label="Latest status" value={statusMeaning(history.data.items[0].status).label} />
              <Tile label="Latest rows" value={history.data.items[0].rows_total ?? "—"} />
              <Tile label="Latest received" value={fmtDateTime(history.data.items[0].received_at)} />
            </div>
            <DataTable
              columns={HISTORY_COLUMNS}
              rows={history.data.items}
              rowKey={(b) => b.batch_id}
              onRowClick={(b) => setOpenBatchId(b.batch_id)}
            />
          </>
        )}
      </Panel>

      {openBatchId && (
        <Panel title="Batch detail" hint="From the server, for this batch">
          {detail.isPending && <AnalyticsLoading label="Loading batch…" />}
          {detail.isError && (
            <AnalyticsError>
              {errorDetail(detail.error, "This batch could not be loaded.")}
            </AnalyticsError>
          )}
          {detail.data && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[12.5px] md:grid-cols-3" data-testid="feed-batch-detail">
              <Field label="Batch" value={detail.data.batch_id} />
              <Field label="Business date" value={detail.data.business_date ?? "—"} />
              <Field label="Feed type" value={detail.data.feed_type} />
              <Field label="File" value={detail.data.file_name ?? "—"} />
              <Field label="Status" value={statusMeaning(detail.data.status).label} />
              <Field label="Received via" value={detail.data.received_via} />
              <Field label="Rows total" value={String(detail.data.rows_total ?? "—")} />
              <Field label="Rows accepted" value={String(detail.data.rows_accepted ?? "—")} />
              <Field label="Rows quarantined" value={String(detail.data.rows_quarantined ?? "—")} />
              <Field label="Rows skipped" value={String(detail.data.rows_skipped ?? "—")} />
              <Field label="Received at" value={fmtDateTime(detail.data.received_at)} />
              <Field label="Loaded at" value={fmtDateTime(detail.data.loaded_at)} />
            </dl>
          )}
          <button
            type="button"
            onClick={() => setOpenBatchId(null)}
            className="mt-4 rounded-control border border-border px-3 py-1.5 text-[12px] font-semibold"
          >
            Close
          </button>
        </Panel>
      )}

      {/* Quarantine review for the open batch: the rows THIS file held, and
          the release action. Its own component, with its own query, so the
          batch detail above stays a plain read of the server's fields. */}
      {openBatchId && <QuarantineReview batchId={openBatchId} />}
    </PageRoot>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium text-foreground" title={value}>{value}</dd>
    </div>
  );
}
