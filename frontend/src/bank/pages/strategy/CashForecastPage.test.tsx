// @vitest-environment jsdom
/**
 * The cash forecast page (E06). Three behaviours worth a test, same
 * discipline as MonteCarloPage.test.tsx:
 *
 *  · the run's caveat is on screen, rendered from the response's own stamp —
 *    a figure must never be readable without it (ADR 0014);
 *  · a book with too little VERIFIED payment history ABSTAINS, and the page
 *    says so instead of showing an empty chart;
 *  · the headline totals and the backtest caption read straight off the
 *    response, with no number invented by the page.
 *
 * The API is mocked; the engine's own arithmetic is tested in
 * backend/tests/test_cash_forecast_engine.py / test_cash_forecast_reads.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { CashForecastRun, CashForecastWeek } from "@/api/bankStrategy";
import CashForecastPage from "./CashForecastPage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() }, LONG_RUNNING_MS: 180_000 }));

const CAVEAT = "SYNTHETIC: generated, not observed. UNCALIBRATED: read the bands as scenario arithmetic.";

function week(i: number, o: Partial<CashForecastWeek> = {}): CashForecastWeek {
  const d = new Date(Date.UTC(2026, 9, 7 + 7 * i));
  return {
    week_start: d.toISOString().slice(0, 10),
    p10: 400000, p50: 500000, p90: 650000,
    ptp_scheduled: 100000, bottom_up: 120000, top_down: 480000,
    ...o,
  };
}

function run(o: Partial<CashForecastRun> = {}): CashForecastRun {
  return {
    weeks: Array.from({ length: 13 }, (_, i) => week(i)),
    totals: { p10: 5200000, p50: 6500000, p90: 8450000 },
    history_weeks: 20,
    ptp_honor_rate: 0.62,
    ptp_resolved_count: 14,
    recovery_informed_total: 180000,
    recovery_informed_loans: 9,
    backtest: { mape: 0.21, n_folds: 3, calibrated: true, reason: "" },
    engine_version: "cf-1.0.0",
    synthetic: true, calibrated: true, data_version: "2026-10-07",
    basis: "20 week(s) of VERIFIED payments, Holt ETS (alpha=0.4, beta=0.2), 14 resolved PTP(s)",
    text: CAVEAT,
    ...o,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><CashForecastPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.mocked(api.get).mockReset();
});

describe("Cash Forecast page", () => {
  it("shows the run's own caveat and the headline totals, with no invented number", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: run() });
    renderPage();

    expect(await screen.findByText(CAVEAT)).toBeTruthy();
    // totals.p50 appears both as the headline tile and the table's footer total.
    expect(screen.getAllByText("₹65.00L").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("₹52.00L to ₹84.50L (p10–p90)")).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();                     // ptp_honor_rate
  });

  it("says a book with too little payment history cannot be forecast, and shows no figures", async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 422, data: { detail: "This bank has 3 week(s) of VERIFIED payment history.", code: "INSUFFICIENT_HISTORY" } },
    });
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Not enough VERIFIED payment history/);
    expect(screen.queryByText(CAVEAT)).toBeNull();
  });

  it("reports the backtest honestly when it has not cleared the calibration bar", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: run({
      backtest: { mape: 0.78, n_folds: 2, calibrated: false, reason: "MAPE 78% exceeds the 50% ceiling" },
    }) });
    renderPage();

    expect(await screen.findByText(/MAPE 78%/)).toBeTruthy();
    expect(screen.getByText(/2 rolling-origin fold\(s\)/)).toBeTruthy();
  });

  it("shows no PTP honor rate as a rate rather than a number when the book has none yet", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: run({ ptp_honor_rate: null, ptp_resolved_count: 0 }) });
    renderPage();

    expect(await screen.findByText("No PTP history yet")).toBeTruthy();
  });
});
