// @vitest-environment jsdom
/**
 * The Usage & Cost page's own decisions: tokens and cost render from the
 * server's numbers, an unpriced call is flagged rather than silently folded
 * into the total as zero, and the honesty line about unattributed calls only
 * shows up when there is something to admit — the same guarantee
 * AuditPage.test.tsx checks for its own "pending attribution" line.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { UsageCostPage } from "./UsageCostPage";
import type { UsagePayload } from "./usageCostModel";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn() } }));
const api = (await import("@/api/axios")).default as unknown as { get: ReturnType<typeof vi.fn> };

afterEach(() => {
  cleanup();
  api.get.mockReset();
});

function payload(over: Partial<UsagePayload> = {}): UsagePayload {
  return {
    since: "2026-09-07T00:00:00+00:00",
    until: null,
    totals: {
      calls: 42, input_tokens: 50_000, output_tokens: 8_000, cache_tokens: 1_000,
      cost_usd: 0.12, unpriced_calls: 0,
    },
    by_feature: [
      { feature: "briefing", calls: 30, input_tokens: 40_000, output_tokens: 6_000, cache_tokens: 800,
        cost_usd: 0.09 },
      { feature: "case_ranking", calls: 12, input_tokens: 10_000, output_tokens: 2_000, cache_tokens: 200,
        cost_usd: 0.03 },
    ],
    by_day: [
      { day: "2026-10-05", calls: 20, cost_usd: 0.06 },
      { day: "2026-10-06", calls: 22, cost_usd: 0.06 },
    ],
    coverage: { pending_attribution: 0, note: "Calls with no bank recorded cannot be charged to one." },
    ...over,
  };
}

function show(body: UsagePayload) {
  api.get.mockResolvedValue({ data: body });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <UsageCostPage />
    </QueryClientProvider>,
  );
}

describe("UsageCostPage", () => {
  it("shows the totals and the by-feature breakdown", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("42")).toBeTruthy());
    expect(screen.getByText("Briefing")).toBeTruthy();
    expect(screen.getByText("Case ranking")).toBeTruthy();
    expect(screen.getByText("$0.12")).toBeTruthy();
  });

  it("flags an unpriced call rather than hiding it in the total", async () => {
    show(payload({ totals: { ...payload().totals, unpriced_calls: 3 } }));
    await waitFor(() => expect(screen.getByText(/3 calls at an unknown price/)).toBeTruthy());
  });

  it("says nothing about unpriced calls when there are none", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("$0.12")).toBeTruthy());
    expect(screen.queryByText(/unknown price/)).toBeNull();
  });

  it("SAYS how many calls are pending attribution", async () => {
    show(payload({ coverage: { ...payload().coverage, pending_attribution: 7 } }));
    await waitFor(() => expect(screen.getByText(/7 calls pending attribution/)).toBeTruthy());
  });

  it("says nothing about attribution when there is nothing to admit", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("$0.12")).toBeTruthy());
    expect(screen.queryByText(/pending attribution/)).toBeNull();
  });

  it("shows an empty window as empty rather than as an error", async () => {
    show(payload({ by_feature: [], by_day: [] }));
    await waitFor(() => expect(screen.getAllByText(/No calls in this window/)).toHaveLength(2));
  });
});
