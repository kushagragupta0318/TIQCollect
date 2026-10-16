// @vitest-environment jsdom
/**
 * Tech Ops › Loan Feed › quarantine review. The assertions that matter are the
 * honest ones: that a row refused AGAIN is never reported as released, that a
 * row with no loan cannot be selected at all, that release asks first, and
 * that counts come from the server rather than from the page of rows shown.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QuarantineReview from "./QuarantineReview";
import {
  getQuarantine, releaseRows,
  // The mock spreads `...real`, so these are the genuine implementations.
  outcomeMeaning as realOutcomeMeaning,
  type QuarantineList, type QuarantineRow, type ReleaseResult,
} from "./quarantineModel";

vi.mock("./quarantineModel", async (importOriginal) => {
  const real = await importOriginal<typeof import("./quarantineModel")>();
  return { ...real, getQuarantine: vi.fn(), releaseRows: vi.fn() };
});

const row = (over: Partial<QuarantineRow> = {}): QuarantineRow => ({
  row_id: "r-1", row_no: 1, status: "QUARANTINED",
  loan_account_number: "LN00000001", customer_ref: "C-00001", case_number: "CASE0001",
  reason: "NO_CONTRACT_IN_FORCE", detail: "no contract in force for this agency",
  attempts: 1, releasable: true, blocked_because: null,
  processed_at: "2026-10-15T09:00:00Z", released_at: null, released_by: null, ...over,
});

const list = (over: Partial<QuarantineList> = {}): QuarantineList => ({
  batch_id: "b-1", batch_status: "PARTIAL", batch_reviewable: true,
  business_date: "2026-10-15", file_name: "book.csv",
  total_quarantined: 1, total_blocked: 0, total_releasable: 1, released_count: 0,
  items: [row()], ...over,
});

const result = (over: Partial<ReleaseResult> = {}): ReleaseResult => ({
  batch_id: "b-1", batch_status: "PARTIAL",
  released: 1, still_refused: 0, not_releasable: 0, already_released: 0, not_found: 0,
  remaining_quarantined: 0,
  verdicts: [{ row_id: "r-1", row_no: 1, outcome: "RELEASED", case_number: "PL261015ABCD" }],
  ...over,
});

function renderReview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><QuarantineReview batchId="b-1" /></QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getQuarantine).mockResolvedValue(list());
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("states", () => {
  it("shows a loading state first", () => {
    vi.mocked(getQuarantine).mockReturnValue(new Promise(() => {}));
    renderReview();
    expect(screen.getByText(/Loading quarantined rows/i)).toBeTruthy();
  });

  it("shows an empty state when the file held nothing", async () => {
    vi.mocked(getQuarantine).mockResolvedValue(
      list({ total_quarantined: 0, total_releasable: 0, items: [] }));
    renderReview();
    expect(await screen.findByTestId("quarantine-empty")).toBeTruthy();
  });

  it("shows an error with a retry when the list fails", async () => {
    vi.mocked(getQuarantine).mockRejectedValue(new Error("boom"));
    renderReview();
    expect(await screen.findByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("reports a 403 as a capability message", async () => {
    vi.mocked(getQuarantine).mockRejectedValue({ response: { status: 403 }, isAxiosError: true });
    renderReview();
    expect((await screen.findByText(/capability this role does not hold/i))).toBeTruthy();
  });

  it("says when a batch is not ready to be reviewed", async () => {
    vi.mocked(getQuarantine).mockResolvedValue(
      list({ batch_status: "RECEIVED", batch_reviewable: false }));
    renderReview();
    expect(await screen.findByTestId("quarantine-not-reviewable")).toBeTruthy();
  });
});

describe("the listing", () => {
  it("shows the recorded reason and the attempt count", async () => {
    vi.mocked(getQuarantine).mockResolvedValue(list({ items: [row({ attempts: 3 })] }));
    renderReview();
    const tr = await screen.findByTestId("quarantine-row-1");
    expect(tr.textContent).toMatch(/No contract in force/i);   // not SCREAMING_SNAKE
    expect(tr.textContent).toMatch(/3/);
  });

  it("uses the server's counts, not the number of rows on this page", async () => {
    // One row returned, but the batch holds 12. Saying 1 would be a lie.
    vi.mocked(getQuarantine).mockResolvedValue(
      list({ total_quarantined: 12, total_releasable: 8, total_blocked: 4, items: [row()] }));
    renderReview();
    const tiles = await screen.findByTestId("quarantine-tiles");
    expect(tiles.textContent).toMatch(/12/);
    expect(tiles.textContent).toMatch(/8/);
    expect(tiles.textContent).toMatch(/4/);
  });

  it("cannot select a row that never created a loan", async () => {
    vi.mocked(getQuarantine).mockResolvedValue(list({
      total_blocked: 1, total_releasable: 0,
      items: [row({ releasable: false, blocked_because: "NO_LOAN_TO_PLACE", reason: "UNKNOWN_BRANCH" })],
    }));
    renderReview();
    const box = await screen.findByLabelText(/select row 1/i);
    expect((box as HTMLInputElement).disabled).toBe(true);
    // And the page says what to do instead of leaving it a dead end.
    expect(screen.getByTestId("quarantine-blocked-note").textContent).toMatch(/re-ingest/i);
  });
});

describe("release", () => {
  async function selectAndOpenConfirm() {
    renderReview();
    fireEvent.click(await screen.findByLabelText(/select row 1/i));
    fireEvent.click(screen.getByTestId("quarantine-release-button"));
    return screen.findByTestId("quarantine-confirm");
  }

  it("asks for confirmation before releasing anything", async () => {
    vi.mocked(releaseRows).mockResolvedValue(result());
    const box = await selectAndOpenConfirm();
    expect(box.textContent).toMatch(/Release 1 row for processing\?/i);
    // Nothing was sent just by opening the confirmation.
    expect(vi.mocked(releaseRows)).not.toHaveBeenCalled();
  });

  it("warns in the confirmation that nothing is forced through", async () => {
    const box = await selectAndOpenConfirm();
    expect(box.textContent).toMatch(/refused again and stay quarantined/i);
  });

  it("can be cancelled without releasing", async () => {
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByTestId("quarantine-confirm")).toBeNull());
    expect(vi.mocked(releaseRows)).not.toHaveBeenCalled();
  });

  it("cannot be started with nothing selected", async () => {
    renderReview();
    const btn = await screen.findByTestId("quarantine-release-button");
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("reports a released row as placed, with its case", async () => {
    vi.mocked(releaseRows).mockResolvedValue(result());
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByTestId("quarantine-confirm-button"));

    const out = await screen.findByTestId("quarantine-release-result");
    expect(out.textContent).toMatch(/1 row released/i);
    expect(out.textContent).toMatch(/case opened/i);
    expect(out.textContent).toMatch(/PL261015ABCD/);
  });

  it("never reports a row that was refused again as released", async () => {
    // The honesty case: the request succeeded, the ROW did not.
    vi.mocked(releaseRows).mockResolvedValue(result({
      released: 0, still_refused: 1, remaining_quarantined: 1,
      verdicts: [{ row_id: "r-1", row_no: 1, outcome: "STILL_REFUSED", reason: "CONTRACT_FULL" }],
    }));
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByTestId("quarantine-confirm-button"));

    const out = await screen.findByTestId("quarantine-release-result");
    expect(out.textContent).toMatch(/No rows were released/i);
    expect(screen.getByTestId("quarantine-still-refused").textContent)
      .toMatch(/has not been resolved/i);
    expect(out.textContent).toMatch(/Still refused/i);
    expect(out.textContent).not.toMatch(/case opened/i);
  });

  it("reports an already-handled row as done once, not done again", async () => {
    vi.mocked(releaseRows).mockResolvedValue(result({
      released: 0, already_released: 1, remaining_quarantined: 0,
      verdicts: [{ row_id: "r-1", row_no: 1, outcome: "ALREADY_RELEASED" }],
    }));
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByTestId("quarantine-confirm-button"));

    expect((await screen.findByTestId("quarantine-already-released")).textContent)
      .toMatch(/Nothing was done twice/i);
  });

  it("shows how many rows are still held after a partial release", async () => {
    vi.mocked(releaseRows).mockResolvedValue(result({
      released: 1, still_refused: 2, remaining_quarantined: 2,
      verdicts: [
        { row_id: "r-1", row_no: 1, outcome: "RELEASED", case_number: "PL1" },
        { row_id: "r-2", row_no: 2, outcome: "STILL_REFUSED", reason: "CONTRACT_FULL" },
        { row_id: "r-3", row_no: 3, outcome: "NOT_RELEASABLE", blocked_because: "NO_LOAN_TO_PLACE" },
      ],
    }));
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByTestId("quarantine-confirm-button"));

    const out = await screen.findByTestId("quarantine-release-result");
    expect(out.textContent).toMatch(/2 rows still held/i);
    expect(out.textContent).toMatch(/Cannot be released here/i);
  });

  it("reports a 403 on release as a capability message", async () => {
    vi.mocked(releaseRows).mockRejectedValue({ response: { status: 403 }, isAxiosError: true });
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByTestId("quarantine-confirm-button"));

    expect((await screen.findByTestId("quarantine-error")).textContent)
      .toMatch(/capability this role does not hold/i);
  });

  it("says nothing was changed when the release fails", async () => {
    vi.mocked(releaseRows).mockRejectedValue(new Error("network down"));
    await selectAndOpenConfirm();
    fireEvent.click(screen.getByTestId("quarantine-confirm-button"));

    expect((await screen.findByTestId("quarantine-error")).textContent)
      .toMatch(/Nothing was changed/i);
  });

  it("sends only the rows that were selected", async () => {
    vi.mocked(getQuarantine).mockResolvedValue(list({
      total_quarantined: 2, total_releasable: 2,
      items: [row(), row({ row_id: "r-2", row_no: 2 })],
    }));
    vi.mocked(releaseRows).mockResolvedValue(result());
    renderReview();
    fireEvent.click(await screen.findByLabelText(/select row 2/i));
    fireEvent.click(screen.getByTestId("quarantine-release-button"));
    fireEvent.click(await screen.findByTestId("quarantine-confirm-button"));

    await waitFor(() => expect(vi.mocked(releaseRows)).toHaveBeenCalledWith("b-1", ["r-2"]));
  });
});

describe("outcomeMeaning is honest about every verdict", () => {
  it("treats only a real placement as success", () => {
    expect(realOutcomeMeaning("RELEASED").tone).toBe("good");
    for (const o of ["STILL_REFUSED", "NOT_RELEASABLE", "ALREADY_RELEASED", "NOT_FOUND"] as const) {
      expect(realOutcomeMeaning(o).tone, o).not.toBe("good");
    }
  });

  it("explains every outcome the API can return", () => {
    for (const o of ["RELEASED", "STILL_REFUSED", "NOT_RELEASABLE", "ALREADY_RELEASED", "NOT_FOUND"] as const) {
      const m = realOutcomeMeaning(o);
      expect(m.label, o).not.toBe(o);
      expect(m.help, o).not.toBe("");
    }
  });

  it("says a still-refused row stays quarantined", () => {
    expect(realOutcomeMeaning("STILL_REFUSED").help).toMatch(/stays quarantined/i);
  });

  it("never calls an unknown outcome a success", () => {
    // @ts-expect-error — deliberately outside the union
    expect(realOutcomeMeaning("SOMETHING_NEW").tone).not.toBe("good");
  });
});
