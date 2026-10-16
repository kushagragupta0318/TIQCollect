// @vitest-environment jsdom
/**
 * Tech Ops › Loan Feed. The assertions that matter are the honest ones: that a
 * RECEIVED batch is never described as ingested, that a duplicate is reported
 * as a duplicate, and that a double click cannot send two uploads.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import LoanFeedPage from "./LoanFeedPage";
import {
  getFeedBatch, getFeedHistory, uploadFeed, validateFeed,
  // The mock below spreads `...real`, so this is the genuine implementation,
  // not a stub — these assertions test the real status table.
  statusMeaning as realStatusMeaning,
  type FeedBatch, type FeedUploadResult, type FeedValidateResult,
} from "./feedUploadModel";

vi.mock("./feedUploadModel", async (importOriginal) => {
  const real = await importOriginal<typeof import("./feedUploadModel")>();
  return {
    ...real,                       // keep MAX_UPLOAD_BYTES, formatBytes, statusMeaning
    getFeedHistory: vi.fn(),
    getFeedBatch: vi.fn(),
    uploadFeed: vi.fn(),
    validateFeed: vi.fn(),
  };
});

const batch = (over: Partial<FeedBatch> = {}): FeedBatch => ({
  batch_id: "b-1", feed_type: "DAILY_BOOK", business_date: "2026-10-15",
  file_name: "book.csv", file_sha256: "a".repeat(64), received_via: "UPLOAD",
  uploaded_by: "u-1", status: "RECEIVED", rows_total: 2, rows_accepted: null,
  rows_quarantined: null, rows_skipped: null,
  received_at: "2026-10-15T09:00:00Z", loaded_at: null, ...over,
});

const okValidation: FeedValidateResult = {
  file_name: "book.csv", ok: true, rows: 2, headers: ["customer_ref"],
  missing_headers: [], blocking: [], warnings: [], duplicate_rows_in_file: 0,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><LoanFeedPage /></QueryClientProvider>);
}

function choose(name = "book.csv") {
  const input = screen.getByTestId("feed-file-input") as HTMLInputElement;
  const f = new File(["customer_ref,loan_account_number,case_number\nA,B,C\n"], name, { type: "text/csv" });
  fireEvent.change(input, { target: { files: [f] } });
  return f;
}

beforeEach(() => {
  vi.mocked(getFeedHistory).mockResolvedValue({ total: 0, items: [] });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("history states", () => {
  it("shows an empty state when the bank has uploaded nothing", async () => {
    renderPage();
    expect(await screen.findByText(/No feeds uploaded yet/i)).toBeTruthy();
  });

  it("shows a loading state first", () => {
    vi.mocked(getFeedHistory).mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText(/Loading ingestion history/i)).toBeTruthy();
  });

  it("shows an error with a retry when history fails", async () => {
    vi.mocked(getFeedHistory).mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("lists received batches", async () => {
    vi.mocked(getFeedHistory).mockResolvedValue({ total: 1, items: [batch()] });
    renderPage();
    expect(await screen.findByText("book.csv")).toBeTruthy();
    expect(screen.getAllByText(/Received/i).length).toBeGreaterThan(0);
  });
});

describe("validation", () => {
  it("reports a file that is ready to upload", async () => {
    vi.mocked(validateFeed).mockResolvedValue(okValidation);
    renderPage();
    choose();
    fireEvent.click(screen.getByRole("button", { name: /check file/i }));
    expect(await screen.findByText(/Ready to upload/i)).toBeTruthy();
  });

  it("shows blocking errors and refuses to enable upload", async () => {
    vi.mocked(validateFeed).mockResolvedValue({
      ...okValidation, ok: false, rows: 0,
      blocking: ["Missing required column(s): loan_account_number"],
    });
    renderPage();
    choose();
    fireEvent.click(screen.getByRole("button", { name: /check file/i }));

    expect(await screen.findByText(/cannot be ingested/i)).toBeTruthy();
    expect(screen.getByText(/Missing required column/i)).toBeTruthy();
    expect((screen.getByTestId("feed-upload-button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows warnings without blocking the upload", async () => {
    vi.mocked(validateFeed).mockResolvedValue({
      ...okValidation, warnings: ["3 duplicate loan_account_number(s) within this file; the last wins."],
    });
    renderPage();
    choose();
    fireEvent.click(screen.getByRole("button", { name: /check file/i }));

    expect(await screen.findByText(/duplicate loan_account_number/i)).toBeTruthy();
    expect((screen.getByTestId("feed-upload-button") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("upload", () => {
  it("reports a received batch as QUEUED, never as ingested", async () => {
    vi.mocked(uploadFeed).mockResolvedValue({
      ...batch(), duplicate: false, queued: true,
      validation: { ok: true, rows: 2, headers: [], missing_headers: [], blocking: [], warnings: [], duplicate_rows_in_file: 0 },
    } as FeedUploadResult);
    renderPage();
    choose();
    fireEvent.click(screen.getByTestId("feed-upload-button"));

    const box = await screen.findByTestId("feed-upload-result");
    expect(box.textContent).toMatch(/queued for processing/i);
    expect(box.textContent).toMatch(/Nothing has been written to the loan book/i);
    // The word that would be a lie at this stage: the worker has not finished.
    expect(box.textContent).not.toMatch(/\bingested\b/i);
    // Queued successfully, so no "not accepted by the queue" warning.
    expect(screen.queryByTestId("feed-upload-not-queued")).toBeNull();
  });

  it("says so when the batch was stored but never enqueued", async () => {
    // queued:false is the broker-down window. The file IS safe, so the message
    // must not read as a failure — but it must not read as "being processed".
    vi.mocked(uploadFeed).mockResolvedValue({
      ...batch(), duplicate: false, queued: false,
      validation: { ok: true, rows: 2, headers: [], missing_headers: [], blocking: [], warnings: [], duplicate_rows_in_file: 0 },
    } as FeedUploadResult);
    renderPage();
    choose();
    fireEvent.click(screen.getByTestId("feed-upload-button"));

    const warn = await screen.findByTestId("feed-upload-not-queued");
    expect(warn.textContent).toMatch(/saved/i);
    expect(warn.textContent).toMatch(/within 15 minutes/i);
    expect(warn.textContent).toMatch(/no need to upload it again/i);
  });

  it("presents a duplicate upload honestly", async () => {
    vi.mocked(uploadFeed).mockResolvedValue({
      ...batch(), duplicate: true, queued: false,
      validation: { ok: true, rows: 2, headers: [], missing_headers: [], blocking: [], warnings: [], duplicate_rows_in_file: 0 },
    } as FeedUploadResult);
    renderPage();
    choose();
    fireEvent.click(screen.getByTestId("feed-upload-button"));

    const box = await screen.findByTestId("feed-upload-result");
    expect(box.textContent).toMatch(/Already received/i);
    expect(box.textContent).toMatch(/No second ingestion was created/i);
  });

  it("shows a capability message when the server refuses with 403", async () => {
    vi.mocked(uploadFeed).mockRejectedValue({ response: { status: 403 }, isAxiosError: true });
    renderPage();
    choose();
    fireEvent.click(screen.getByTestId("feed-upload-button"));
    expect((await screen.findByTestId("feed-upload-error")).textContent)
      .toMatch(/capability this role does not hold/i);
  });

  it("shows a readable error when the upload fails", async () => {
    vi.mocked(uploadFeed).mockRejectedValue(new Error("network down"));
    renderPage();
    choose();
    fireEvent.click(screen.getByTestId("feed-upload-button"));
    expect(await screen.findByTestId("feed-upload-error")).toBeTruthy();
  });

  it("does not send a second upload while one is in flight", async () => {
    vi.mocked(uploadFeed).mockReturnValue(new Promise(() => {}));   // never settles
    renderPage();
    choose();
    const btn = screen.getByTestId("feed-upload-button");
    fireEvent.click(btn);
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(true));
    expect(vi.mocked(uploadFeed)).toHaveBeenCalledTimes(1);
  });

  it("lets the file be removed before uploading", async () => {
    renderPage();
    choose();
    expect(screen.getByText("book.csv")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /remove file/i }));
    await waitFor(() => expect(screen.queryByText("book.csv")).toBeNull());
  });
});

describe("batch detail", () => {
  it("opens a batch and shows the server's own fields", async () => {
    vi.mocked(getFeedHistory).mockResolvedValue({ total: 1, items: [batch()] });
    vi.mocked(getFeedBatch).mockResolvedValue(batch({ rows_quarantined: 4, status: "PARTIAL" }));
    renderPage();

    fireEvent.click(await screen.findByText("book.csv"));
    const detail = await screen.findByTestId("feed-batch-detail");
    expect(vi.mocked(getFeedBatch)).toHaveBeenCalledWith("b-1");
    expect(detail.textContent).toMatch(/Partially loaded/i);
    expect(detail.textContent).toMatch(/4/);
  });
});

describe("statusMeaning covers the backend's own vocabulary", () => {
  // models/lending.FEED_STATUSES. REJECTED was missing and fell through to the
  // default, rendering a refused feed in a neutral "pending" tone with no
  // explanation — i.e. looking like it was still being processed.
  const DB_STATUSES = ["RECEIVED", "VALIDATING", "LOADED", "PARTIAL", "REJECTED"];

  it("explains every status the database can store", () => {
    for (const s of DB_STATUSES) {
      const m = realStatusMeaning(s);
      expect(m.label, s).not.toBe(s);          // a real label, not the raw enum
      expect(m.help, s).not.toBe("");          // and a sentence saying what it means
    }
  });

  it("never calls an unfinished or refused run good", () => {
    expect(realStatusMeaning("RECEIVED").tone).toBe("pending");
    expect(realStatusMeaning("REJECTED").tone).toBe("bad");
    expect(realStatusMeaning("LOADED").tone).toBe("good");   // the only success
  });

  it("says a RECEIVED batch is not yet in the loan book", () => {
    expect(realStatusMeaning("RECEIVED").help).toMatch(/not yet in the loan book/i);
  });

  it("shows an unknown status verbatim, but never as success", () => {
    expect(realStatusMeaning("SOMETHING_NEW").tone).not.toBe("good");
  });
});
