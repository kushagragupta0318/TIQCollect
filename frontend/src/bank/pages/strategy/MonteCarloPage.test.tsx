// @vitest-environment jsdom
/**
 * The simulator page (E05). Three behaviours worth a test:
 *
 *  · the run's caveat is on screen, rendered from the response's own stamp —
 *    a figure must never be readable without it (ADR 0014);
 *  · a book with too little history ABSTAINS, and the page says so instead of
 *    showing an empty chart;
 *  · moving a lever does not restate the figures on screen. They belong to the
 *    run that produced them until a new run is asked for.
 *
 * The API is mocked; the engine's own arithmetic is tested in
 * backend/tests/test_monte_carlo*.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { Band, SimulationRun } from "@/api/bankStrategy";
import MonteCarloPage from "./MonteCarloPage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const band = (p50: number, sem: number): Band => ({
  p5: p50 * 0.7, p10: p50 * 0.8, p50, p90: p50 * 1.2, p95: p50 * 1.3, mean: p50, sem,
});

const CAVEAT = "SYNTHETIC: generated, not observed. UNCALIBRATED: read the bands as scenario arithmetic.";

const run = (o: Partial<SimulationRun> = {}): SimulationRun => ({
  engine_version: "mc-1.2.0", seed: 0, n_paths: 500, horizon_months: 12, numpy_version: "2.4.0",
  subsampled: false, synthetic_inputs: true, calibrated_by_backtest: false,
  synthetic_warning: CAVEAT, assumptions: ["Macro sensitivities are hand-authored, never fitted."],
  scenario: { name: "Baseline", gdp: 0, cpi: 0, repo_bps: 0, unemployment: 0, sector: 0 },
  metrics: {
    GNPA_PCT: { p5: 10.6, p10: 11.3, p50: 13.8564, p90: 16.6, p95: 17.4, mean: 13.9, sem: 0.0927 },
    RECOVERED_CASH: band(47559266, 362162), NET_RECOVERY: band(39000000, 300000),
    WRITE_OFFS: band(14744985, 206000), SETTLEMENT_CASH: band(0, 0), COST: band(8500000, 90000),
    ECL: band(62000000, 400000), WRITE_OFF_ACCOUNTS: band(412, 4),
    RECOVERED_ACCOUNTS: band(638, 5), NPA_ACCOUNTS: band(1211, 7),
    STATE_SHARE: { CURRENT: { p5: 52.9, p10: 53.9, p50: 56.7333, p90: 59.7, p95: 60.5, mean: 56.7, sem: 0.106 } },
  } as SimulationRun["metrics"],
  recovery_at_risk: 33451279,
  ifrs9: {
    stage1: { EAD: band(1043885508, 1655030), ECL: band(41381821, 186694), COVERAGE: band(0.0398, 0.00015), PD: band(0.0664, 0.00026) },
    stage2: { EAD: band(214000000, 900000), ECL: band(31000000, 150000), COVERAGE: band(0.1449, 0.0004), PD: band(0.2415, 0.0007) },
    stage3: { EAD: band(96000000, 700000), ECL: band(54000000, 320000), COVERAGE: band(0.5625, 0.0012), PD: band(0.75, 0.0016) },
  },
  synthetic: true, calibrated: false, data_version: "2026-09-30",
  basis: "1,478 synthetic loans at 2026-09-30, transitions from 24 month-ends",
  text: CAVEAT,
  ...o,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MonteCarloPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.mocked(api.post).mockReset();
});

describe("Monte Carlo simulator", () => {
  it("shows the run's own caveat beside its figures, at the run's precision", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: run() });
    renderPage();

    expect(await screen.findByText(CAVEAT)).toBeTruthy();
    expect(screen.getByText("13.9%")).toBeTruthy();                       // p50, 1 dp from sem
    expect(screen.getByText("11.3% to 16.6% at 12 months")).toBeTruthy(); // the band beneath it
    expect(screen.getByText(/8 in 10 simulated paths/)).toBeTruthy();
    expect(screen.getByText(/1,478 synthetic loans at 2026-09-30/)).toBeTruthy();
    expect(screen.getByText("Macro sensitivities are hand-authored, never fitted.", { exact: false })).toBeTruthy();
  });

  it("says a book with too little history cannot be simulated, and shows no figures", async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 422, data: { detail: "This bank has 1 month-end(s) of history.", code: "INSUFFICIENT_HISTORY" } },
    });
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Not enough history to simulate this book/);
    expect(screen.queryByText(/8 in 10 simulated paths/)).toBeNull();
  });

  it("says a role that cannot simulate cannot, without reciting the capability matrix", async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: { detail: "Access denied. Required capability: strategy.simulate" } },
    });
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/needs the strategy-simulation capability/);
    expect(alert.textContent).not.toMatch(/BANK_ADMIN|BANK_ANALYST|Access denied/);
  });

  it("keeps the figures with the run that produced them until a new run is asked for", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: run() });
    renderPage();
    await screen.findByText("13.9%");
    expect(vi.mocked(api.post)).toHaveBeenCalledTimes(1);

    // Move a lever: the figures on screen still belong to the previous run.
    fireEvent.change(screen.getByLabelText("Placed with agencies"), { target: { value: "0.8" } });
    expect(screen.getByText("Changed: Placed with agencies: 80%")).toBeTruthy();
    expect(screen.getByText("13.9%")).toBeTruthy();
    expect(vi.mocked(api.post)).toHaveBeenCalledTimes(1);

    // Running sends only the lever that moved; the rest stay the engine's defaults.
    vi.mocked(api.post).mockResolvedValue({ data: run({ metrics: { ...run().metrics, GNPA_PCT: { p5: 9, p10: 9.5, p50: 11.2, p90: 13, p95: 13.5, mean: 11.2, sem: 0.09 } } as SimulationRun["metrics"] }) });
    fireEvent.click(screen.getByRole("button", { name: /Run simulation/ }));

    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.post).mock.calls[1]).toEqual([
      "/bank/strategy/simulate",
      { preset: "baseline", levers: { placement_rate: 0.8 }, horizon_months: 12, n_paths: 500, seed: 0 },
    ]);
    await screen.findByText("11.2%");
  });
});
