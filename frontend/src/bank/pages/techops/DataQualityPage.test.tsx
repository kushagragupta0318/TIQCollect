// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { DataQualityPage } from "./DataQualityPage";
import type { DataQualityPayload } from "./dataQualityModel";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn() } }));
const api = (await import("@/api/axios")).default as unknown as { get: ReturnType<typeof vi.fn> };

afterEach(() => {
  cleanup();
  api.get.mockReset();
});

function payload(over: Partial<DataQualityPayload> = {}): DataQualityPayload {
  return {
    feed_freshness: [
      { feed_type: "DAILY_BOOK", last_business_date: "2026-10-05", last_received_at: "2026-10-05T03:30:00+00:00",
        rows_total: 320, rows_accepted: 300, rows_quarantined: 12, rows_skipped: 8, days_stale: 1 },
    ],
    quarantined_by_reason: [{ reason: "UNKNOWN_BRANCH", rows: 12 }],
    quarantined_sample: [
      { row_no: 7, loan_account_number: "LN-00881", customer_ref: "C-4471", case_number: null,
        feed_type: "DAILY_BOOK", business_date: "2026-10-05",
        dq_errors: [{ reason: "UNKNOWN_BRANCH", detail: "branch ZZZZZ is not a branch of this bank" }],
        created_at: "2026-10-05T03:31:00+00:00" },
    ],
    duplicate_customer_phones: { count: 1, sample: [{ phone_primary: "9876500112", customers: 2 }] },
    out_of_range_loans: {
      count: 1,
      sample: [{ loan_id: "l1", loan_account_number: "LN-00220", overdue_amount: 50_000, total_outstanding: 40_000,
                outstanding_principal: 35_000 }],
    },
    ...over,
  };
}

function show(body: DataQualityPayload) {
  api.get.mockResolvedValue({ data: body });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <DataQualityPage />
    </QueryClientProvider>,
  );
}

describe("DataQualityPage", () => {
  it("shows feed freshness and the quarantine breakdown", async () => {
    show(payload());
    // "DAILY_BOOK" and "Unknown branch" each appear twice on purpose: once in
    // the Feed freshness tile / Quarantined-by-reason panel, once more in the
    // quarantined sample row below (its own feed_type and reason) — the
    // fixture's one sample row shares both values. getByText would throw on
    // the ambiguity; getAllByText with an exact count asserts both renders.
    await waitFor(() => expect(screen.getAllByText("DAILY_BOOK")).toHaveLength(2));
    expect(screen.getAllByText("Unknown branch")).toHaveLength(2);
    expect(screen.getByText(/300 accepted/)).toBeTruthy();
  });

  it("lists a quarantined row with its reason and detail", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("LN-00881")).toBeTruthy());
    expect(screen.getByText(/branch ZZZZZ is not a branch/)).toBeTruthy();
  });

  it("shows a duplicate customer phone and an out-of-range loan", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("9876500112")).toBeTruthy());
    expect(screen.getByText("LN-00220")).toBeTruthy();
    expect(screen.getByText("₹50,000")).toBeTruthy();
  });

  it("says plainly when nothing is quarantined, not an error", async () => {
    show(payload({ quarantined_by_reason: [], quarantined_sample: [] }));
    await waitFor(() => expect(screen.getAllByText("Nothing is quarantined.")).toHaveLength(2));
  });

  it("says plainly when no feed has ever been received", async () => {
    show(payload({ feed_freshness: [] }));
    await waitFor(() => expect(screen.getByText(/No feed has ever been received/)).toBeTruthy());
  });

  it("says plainly when neither structural check finds anything", async () => {
    show(payload({ duplicate_customer_phones: { count: 0, sample: [] }, out_of_range_loans: { count: 0, sample: [] } }));
    await waitFor(() => expect(screen.getAllByText("None found.")).toHaveLength(2));
  });
});
