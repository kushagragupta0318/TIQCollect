// @vitest-environment jsdom
/**
 * The day-1 "set up your team" flow: a brand-new manager adds agents one at a
 * time, each lands in a roster with its login, and only "Continue" flips the
 * gate (invalidates the agents query). The real Add Agent drawer is stubbed so
 * these tests drive ManagerOnboarding's own roster + credentials logic without
 * the form/map.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { CreateAgentResult } from "@/api/manager";
import ManagerOnboarding from "./ManagerOnboarding";

// A queue the stubbed drawer hands back, one agent per "create" click.
const stub = vi.hoisted(() => ({ queue: [] as CreateAgentResult[] }));

vi.mock("@/pages/manager/CreateAgentDrawer", () => ({
  CreateAgentDrawer: ({ open, onCreated, onClose }: {
    open: boolean;
    onCreated: (r: CreateAgentResult) => void;
    onClose: () => void;
  }) =>
    open ? (
      <button
        type="button"
        data-testid="stub-create"
        onClick={() => {
          onCreated(stub.queue.shift() as CreateAgentResult);
          onClose();
        }}
      >
        create
      </button>
    ) : null,
}));

afterEach(() => {
  cleanup();
  stub.queue = [];
});

const SHARED_PW = "SharedDemoPass99";

function demoAgent(n: number): CreateAgentResult {
  const email = `ravi.n${n}@aravallifs.test`;
  return {
    agent_id: `a${n}`, user_id: `u${n}`, employee_code: `EMP100${n}`,
    full_name: `Agent ${n}`, email, phone: `98100000${n}`,
    territory: "Sector 44", status: "OFF_DUTY",
    activation: { sent: false },
    demo_login: { email, password: SHARED_PW },
  };
}

function prodAgent(n: number): CreateAgentResult {
  const email = `ravi.n${n}@realbank.example`;
  return {
    agent_id: `a${n}`, user_id: `u${n}`, employee_code: `EMP200${n}`,
    full_name: `Agent ${n}`, email, phone: `98100000${n}`,
    territory: "Sector 44", status: "OFF_DUTY",
    activation: { sent: true },
    // no demo_login — the real activation-link flow
  };
}

function renderOnboarding() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <ManagerOnboarding userName="Prakash Raj" agencyName="Aravalli Field Services" onLogout={() => {}} />
    </QueryClientProvider>,
  );
  return { invalidate };
}

// Open the drawer (first or subsequent) and let the stub report one agent.
function addOneAgent() {
  fireEvent.click(screen.getByRole("button", { name: /add (your first agent|another agent)/i }));
  fireEvent.click(screen.getByTestId("stub-create"));
}

describe("ManagerOnboarding", () => {
  it("starts on the welcome state, not the roster", () => {
    renderOnboarding();
    expect(screen.getByText(/Welcome, Prakash/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /add your first agent/i })).toBeTruthy();
    expect(screen.queryByText(/Set up your team/)).toBeNull();
  });

  it("accumulates added agents in the roster, without exposing the password as a banner", () => {
    stub.queue = [demoAgent(1), demoAgent(2)];
    renderOnboarding();

    addOneAgent();
    expect(screen.getByText(/Set up your team/)).toBeTruthy();
    expect(screen.getByText(/Agents added \(1\)/)).toBeTruthy();
    expect(screen.getByText("ravi.n1@aravallifs.test")).toBeTruthy();

    addOneAgent();
    expect(screen.getByText(/Agents added \(2\)/)).toBeTruthy();
    expect(screen.getByText("ravi.n2@aravallifs.test")).toBeTruthy();

    // The password is never shown on screen — it travels only via Copy login /
    // Copy all (one Copy login per agent row).
    expect(screen.queryByText(/All agents log in with this password/)).toBeNull();
    expect(screen.queryByText(SHARED_PW)).toBeNull();
    expect(screen.getAllByText(/Copy login/i).length).toBe(2);
  });

  it("does NOT flip the gate while adding — only Continue invalidates the agents query", () => {
    stub.queue = [demoAgent(1)];
    const { invalidate } = renderOnboarding();

    addOneAgent();
    // Adding an agent must not invalidate (the manager stays in setup).
    expect(invalidate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["manager", "agents"] });
  });

  it("shows activation status instead of a password when there is no demo login", () => {
    stub.queue = [prodAgent(1)];
    renderOnboarding();

    addOneAgent();
    expect(screen.getByText(/Activation link sent/)).toBeTruthy();
    expect(screen.queryByText(/All agents log in with this password/)).toBeNull();
  });
});
