// @vitest-environment jsdom
/**
 * The agency-admin Managers page: lists the agency's managers (with agent
 * counts), shows an empty state, and shows an access message when the
 * AGENCY_ADMIN-only API refuses. The Add Manager drawer is stubbed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AgencyManager } from "@/api/manager";
import { getAgencyManagers } from "@/api/manager";
import AgencyManagersPage from "./AgencyManagersPage";

vi.mock("@/api/manager", () => ({ getAgencyManagers: vi.fn() }));
vi.mock("./AddManagerDrawer", () => ({ AddManagerDrawer: () => null }));

afterEach(() => {
  cleanup();
  vi.mocked(getAgencyManagers).mockReset();
});

function mgr(name: string, agents: number, active = true): AgencyManager {
  return {
    id: name, full_name: name, email: `${name.toLowerCase()}@a.test`, phone: "9810000000",
    is_active: active, created_at: "2026-10-14", agents_count: agents,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AgencyManagersPage />
    </QueryClientProvider>,
  );
}

describe("AgencyManagersPage", () => {
  it("lists the agency's managers with their agent counts, and an Add button", async () => {
    vi.mocked(getAgencyManagers).mockResolvedValue([mgr("Vikram", 19), mgr("Sunita", 4)]);
    renderPage();
    await waitFor(() => expect(screen.getByText("Vikram")).toBeTruthy());
    expect(screen.getByText("Sunita")).toBeTruthy();
    expect(screen.getByText("19")).toBeTruthy();
    expect(screen.getByRole("button", { name: /add manager/i })).toBeTruthy();
  });

  it("shows an empty state when there are no managers", async () => {
    vi.mocked(getAgencyManagers).mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No managers yet/i)).toBeTruthy());
  });

  it("shows an access message when the API refuses (non-admin / 403)", async () => {
    vi.mocked(getAgencyManagers).mockRejectedValue(new Error("403"));
    renderPage();
    await waitFor(() => expect(screen.getByText(/Only an agency administrator/i)).toBeTruthy());
  });
});
