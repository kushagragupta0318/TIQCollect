// @vitest-environment jsdom
/**
 * The Engine tab of Agencies › Placement (D09, ADR 0010): four-eyes on Apply,
 * a confirmation before anything changes, the exploration bound, and the
 * synthetic-model warning on a run's decisions. The API is mocked; the
 * server's own rules are tested in backend/tests/test_placement_engine_api.py.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import { useAuthStore } from "@/store/authStore";
import { BankPlacementPage } from "./BankPlacementPage";
import type { EngineRun } from "./placementModel";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const run = (over: Partial<EngineRun>): EngineRun => ({
  run_id: "r-1", plan_date: "2026-10-15", status: "PLANNED", simulate: false, strategy: "MIN_COST_FLOW",
  exploration_rate: 0, seed: 20261015, created_by: "u-planner", applied_by: null, applied_at: null,
  totals: { evaluated: 5, placed: 4, kept: 2, blocked: 1, deferred: 0, recalled: 1 },
  expected_recovery_total: 120000, summary: { synthetic_warning: "SYNTHETIC: trained on synthetic borrowers" },
  ...over,
});

function mockGet(runs: EngineRun[]) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url === "/bank/placements/runs") return { data: { items: runs } };
    if (url.startsWith("/bank/placements/runs/")) {
      return { data: { run: runs[0], items: [], total: 0, page: 1, page_size: 50 } };
    }
    return { data: { items: [], total: 0, page: 1, page_size: 50, on: "2026-10-15" } };
  });
}

function renderEngine() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><BankPlacementPage /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("tab", { name: "Engine" }));
}

beforeEach(() => {
  useAuthStore.setState({ user: { id: "u-me", email: "me@example.test", full_name: "Me", role: "BANK_ADMIN", is_active: true } } as never);
});
afterEach(() => {
  cleanup();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
});

describe("Engine tab", () => {
  it("disables Apply on a run the viewer planned", async () => {
    mockGet([run({ created_by: "u-me" })]);
    renderEngine();
    const apply = await screen.findByRole("button", { name: "Apply" });
    expect((apply as HTMLButtonElement).disabled).toBe(true);
    expect(apply.parentElement?.getAttribute("title")).toMatch(/Another bank admin/);
  });

  it("asks before applying another admin's run, then posts to apply", async () => {
    mockGet([run({})]);
    vi.mocked(api.post).mockResolvedValue({
      data: run({ status: "APPLIED", summary: { apply: { placed: 4, recalled: 1, skipped_total: 0, skipped: [] } } }),
    });
    renderEngine();
    fireEvent.click(await screen.findByRole("button", { name: "Apply" }));
    expect(api.post).not.toHaveBeenCalled();                                  // nothing changes before confirming
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/4 loans are placed and 1 placements recalled/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply run" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/bank/placements/runs/r-1/apply"));
    expect(await screen.findByText("Applied: 4 placed, 1 recalled, 0 skipped.")).toBeTruthy();
  });

  it("refuses an exploration rate above 20 percent before it reaches the server", async () => {
    mockGet([]);
    renderEngine();
    const box = await screen.findByLabelText(/Exploration/);
    fireEvent.change(box, { target: { value: "25" } });
    expect((screen.getByRole("button", { name: /Plan for today/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toMatch(/0 to 20/);
    fireEvent.change(box, { target: { value: "10" } });
    vi.mocked(api.post).mockResolvedValue({ data: run({}) });
    fireEvent.click(screen.getByRole("button", { name: /Plan for today/ }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/bank/placements/runs", { mode: "plan", exploration_rate: 0.1 }));
  });

  it("shows the synthetic-model warning on a run's decisions", async () => {
    mockGet([run({})]);
    renderEngine();
    fireEvent.click(await screen.findByRole("button", { name: /Open the run of 2026-10-15/ }));
    expect(await screen.findByText("Scores use synthetic-data models")).toBeTruthy();
  });
});
