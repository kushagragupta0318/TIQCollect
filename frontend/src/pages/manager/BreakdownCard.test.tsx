// @vitest-environment jsdom
/**
 * The breakdown card's own decisions: which dimension it asks for, that the
 * tabs actually change the request, and that a long list says what it left out
 * instead of silently ending. A branch list is 522 rows on the demo book, so
 * the truncation is not cosmetic -- a card that shows eight of them without
 * saying so is a card that misleads.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { BreakdownRow } from "@/api/manager";
import { getTeamBreakdown } from "@/api/manager";
import { BreakdownCard } from "./BreakdownCard";

vi.mock("@/api/manager", () => ({ getTeamBreakdown: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.mocked(getTeamBreakdown).mockReset();
});

function row(key: string, collected: number, cases = 4): BreakdownRow {
  return { key, case_count: cases, target_lakhs: collected * 2, collected_lakhs: collected,
           collection_rate_pct: 50 };
}

function show(rows: BreakdownRow[], props: Partial<{ selMonth: string | null; apiMonth: string | null }> = {}) {
  vi.mocked(getTeamBreakdown).mockResolvedValue(rows);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BreakdownCard barReady selMonth={props.selMonth ?? null} apiMonth={props.apiMonth ?? null} />
    </QueryClientProvider>,
  );
}

describe("BreakdownCard", () => {
  it("opens on branch, because that is the question the page could not answer before", async () => {
    show([row("BR1", 9), row("BR01", 4)]);
    await waitFor(() => expect(screen.getByText("BR1")).toBeTruthy());
    expect(vi.mocked(getTeamBreakdown).mock.calls[0][0]).toBe("branch");
  });

  it("asks for the dimension the tab names", async () => {
    show([row("BR1", 9)]);
    await waitFor(() => expect(screen.getByText("BR1")).toBeTruthy());
    fireEvent.click(screen.getByText("City"));
    await waitFor(() => expect(vi.mocked(getTeamBreakdown).mock.calls.map((c) => c[0])).toContain("city"));
  });

  it("passes the page's selected month through, so the card and the DPD card agree", async () => {
    show([row("BR1", 9)], { selMonth: "Sep 2026", apiMonth: "2026-09" });
    await waitFor(() => expect(screen.getByText("BR1")).toBeTruthy());
    expect(vi.mocked(getTeamBreakdown).mock.calls[0][1]).toBe("2026-09");
  });

  it("says how many rows it is not showing rather than ending quietly", async () => {
    show(Array.from({ length: 12 }, (_, i) => row(`BR${i}`, 12 - i)));
    await waitFor(() => expect(screen.getByText("BR0")).toBeTruthy());
    expect(screen.getByText(/Top 8 of 12/)).toBeTruthy();
    expect(screen.getByText(/4 more not shown/)).toBeTruthy();
    expect(screen.queryByText("BR9")).toBeNull();      // beyond the top eight
  });

  it("shows nothing-to-show rather than an empty card", async () => {
    show([]);
    await waitFor(() => expect(screen.getByText(/No case data available/)).toBeTruthy());
  });

  it("titles a product by its words, and leaves a branch code alone", async () => {
    show([row("BR1", 9)]);
    await waitFor(() => expect(screen.getByText("BR1")).toBeTruthy());
    vi.mocked(getTeamBreakdown).mockResolvedValue([row("CREDIT_CARD", 9)]);
    fireEvent.click(screen.getByText("Product"));
    await waitFor(() => expect(screen.getByText("Credit Card")).toBeTruthy());
  });
});
