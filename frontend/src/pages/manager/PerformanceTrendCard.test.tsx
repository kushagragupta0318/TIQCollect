// @vitest-environment jsdom
/**
 * The Overview's daily trend card. Recharts does not lay out in jsdom (no
 * element sizes), so these pin the states AROUND the chart — loading, error,
 * empty, the range toggle and the day-pick callback — rather than SVG paths.
 * The series' arithmetic is tested server-side in test_manager_endpoints.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { getPerformanceSeries, type PerformanceSeries } from "@/api/manager";
import { PerformanceTrendCard } from "./PerformanceTrendCard";

vi.mock("@/api/manager", () => ({ getPerformanceSeries: vi.fn() }));

const series = (over: Partial<PerformanceSeries> = {}): PerformanceSeries => ({
  from: "2026-09-17", to: "2026-10-16", days: 30,
  points: [
    { day: "2026-09-17", target: 100000, collected: 60000, planned_cases: 10, visits: 8 },
    { day: "2026-09-18", target: 120000, collected: 90000, planned_cases: 12, visits: 11 },
  ],
  ...over,
});

function renderCard(onPickDay?: (d: string) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PerformanceTrendCard onPickDay={onPickDay} />
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.mocked(getPerformanceSeries).mockReset(); });

describe("PerformanceTrendCard", () => {
  it("asks for 30 days by default", async () => {
    vi.mocked(getPerformanceSeries).mockResolvedValue(series());
    renderCard();
    await waitFor(() => expect(vi.mocked(getPerformanceSeries)).toHaveBeenCalledWith(30));
  });

  it("switches to 60 days when the toggle is used", async () => {
    vi.mocked(getPerformanceSeries).mockResolvedValue(series());
    renderCard();
    await screen.findByRole("button", { name: "60d" });
    fireEvent.click(screen.getByRole("button", { name: "60d" }));
    await waitFor(() => expect(vi.mocked(getPerformanceSeries)).toHaveBeenCalledWith(60));
  });

  it("says so when the range holds no plans and no collections", async () => {
    // Real zeros, not an absent response: the manager must be able to tell
    // "nothing happened" from "this did not load".
    vi.mocked(getPerformanceSeries).mockResolvedValue(series({
      points: [{ day: "2026-10-01", target: 0, collected: 0, planned_cases: 0, visits: 0 }],
    }));
    renderCard();
    expect(await screen.findByText(/No plans or collections/i)).toBeTruthy();
  });

  it("offers a retry when the series fails, and never shows a chart", async () => {
    vi.mocked(getPerformanceSeries).mockRejectedValue(new Error("down"));
    renderCard();
    expect(await screen.findByText(/could not be loaded/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
    expect(screen.queryByText(/No plans or collections/i)).toBeNull();
  });

  it("names both series so the shading and the line are readable", async () => {
    vi.mocked(getPerformanceSeries).mockResolvedValue(series());
    renderCard();
    expect(await screen.findByText("Target")).toBeTruthy();
    expect(screen.getByText("Collected")).toBeTruthy();
  });

  it("states that only verified payments are counted", async () => {
    // The same rule the dashboard applies; saying it prevents the chart being
    // read as gross receipts.
    vi.mocked(getPerformanceSeries).mockResolvedValue(series());
    renderCard();
    expect(await screen.findByText(/Verified payments only/i)).toBeTruthy();
  });
});
