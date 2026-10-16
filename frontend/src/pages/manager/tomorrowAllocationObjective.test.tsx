// @vitest-environment jsdom
/**
 * Choosing an objective must not generate a plan.
 *
 * Until 2026-10-16 handleSelectObjective() called handleRunPlan(obj), so
 * clicking Balanced / Max Recovery / Min Distance started a full 40-60s
 * allocation nobody had asked for — three clicks meant three concurrent runs
 * and the Generate button was decorative. The objective is saved on click;
 * only Generate Plan plans.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  getLatestAllocation,
  getAllocationSettings,
  updateAllocationSettings,
  triggerAllocationPlan,
} from "@/api/manager";
import { TomorrowAllocationCard } from "./TomorrowAllocationCard";

vi.mock("@/api/manager", () => ({
  getLatestAllocation: vi.fn(),
  getAllocationSettings: vi.fn(),
  updateAllocationSettings: vi.fn(),
  triggerAllocationPlan: vi.fn(),
  rollbackAllocationPlan: vi.fn(),
  exportAllocationDecisions: vi.fn(),
}));

vi.mock("react-hot-toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// jsdom implements neither; the card calls both when the drawer opens.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
if (!window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as never;
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function renderCard(plan?: Record<string, unknown>) {
  vi.mocked(getLatestAllocation).mockResolvedValue((plan ?? {
    has_plan: false,
    target_date: "2026-10-17",
    message: "No allocation plan generated yet for 2026-10-17.",
    run: null,
    last_failure: null,
  }) as never);
  vi.mocked(getAllocationSettings).mockResolvedValue({ objective: "BALANCED" } as never);
  vi.mocked(updateAllocationSettings).mockResolvedValue({} as never);
  vi.mocked(triggerAllocationPlan).mockResolvedValue({} as never);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TomorrowAllocationCard defaultExpanded />
    </QueryClientProvider>,
  );
}

describe("objective selection is not plan generation", () => {
  it("saves the objective without triggering an allocation run", async () => {
    renderCard();
    const maxRecovery = await screen.findByRole("radio", { name: /max recovery/i });

    fireEvent.click(maxRecovery);

    await waitFor(() =>
      expect(updateAllocationSettings).toHaveBeenCalledWith(
        { objective: "MAX_RECOVERY" }, undefined,
      ),
    );
    // The whole point: no plan was generated.
    expect(triggerAllocationPlan).not.toHaveBeenCalled();
  });

  it("generates only when Generate Plan is clicked, using the chosen objective", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("radio", { name: /min distance/i }));
    expect(triggerAllocationPlan).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /generate plan/i }));

    await waitFor(() => expect(triggerAllocationPlan).toHaveBeenCalledTimes(1));
    expect(vi.mocked(triggerAllocationPlan).mock.calls[0][0]).toMatchObject({
      objective: "MIN_DISTANCE",
      force_replan: true,
    });
  });

  it("states that generation is under way, so the wait is not mistaken for nothing happening", async () => {
    renderCard();
    let release: (v: unknown) => void = () => {};
    vi.mocked(triggerAllocationPlan).mockReturnValue(
      new Promise((res) => { release = res; }) as never,
    );

    fireEvent.click(await screen.findByRole("button", { name: /generate plan/i }));

    const status = await screen.findByRole("status");
    expect(status.textContent).toMatch(/generating the field plan/i);
    release({});
  });
});

/** A plan whose decision list is larger than one render page. */
function planWithDecisions(n: number) {
  return {
    has_plan: true,
    run_id: "run-1",
    plan_date: "2026-10-17",
    status: "PLANNED",
    strategy: "SMART",
    total_cases_evaluated: n,
    total_cases_allocated: n,
    total_cases_deferred: 0,
    total_cases_blocked: 0,
    total_agents_planned: 1,
    expected_recovery_total: 0,
    allocated_target_total: 0,
    allocated_collectable_total: 0,
    created_at: "2026-10-16T09:00:00Z",
    last_failure: null,
    beats: [{
      beat_id: "b1", agent_id: "a1", agent_name: "Agent One", agent_code: "EMP1",
      beat_number: "BEAT-1", total_cases: n, estimated_distance_km: 10,
      estimated_duration_minutes: 60, total_target_amount: 1000,
      status: "PLANNED", total_collectable_amount: 1000,
    }],
    decisions: Array.from({ length: n }, (_, i) => ({
      decision_id: `d${i}`,
      case_id: `c${i}`,
      case_number: `CASE-${i}`,
      outcome: "ALLOCATED",
      reason: "reason",
      visit_priority_score: 1,
      fit_score: 1,
      collectable_amount: 1000,
      score_breakdown: null,
      allocated_agent_id: "a1",
      allocated_agent_name: "Agent One",
    })),
  };
}

describe("the decision drawer does not mount the whole book at once", () => {
  it("renders a first page and offers the rest, rather than ~1.7k rows in one go", async () => {
    renderCard(planWithDecisions(250));

    fireEvent.click(await screen.findByRole("button", { name: /view explainable decisions/i }));

    // One page rendered, not all 250.
    expect(screen.getAllByText(/^CASE-\d+$/).length).toBe(100);
    const more = screen.getByRole("button", { name: /show 100 more/i });
    expect(more.textContent).toMatch(/showing 100 of 250/);

    fireEvent.click(more);
    expect(screen.getAllByText(/^CASE-\d+$/).length).toBe(200);
  });
});
