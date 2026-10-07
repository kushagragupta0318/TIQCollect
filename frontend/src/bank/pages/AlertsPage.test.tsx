// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { AlertsPage } from "./AlertsPage";
import type { DecisionAlert } from "../components/DecisionAlerts";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn() } }));
const api = (await import("@/api/axios")).default as unknown as { get: ReturnType<typeof vi.fn> };

afterEach(() => {
  cleanup();
  api.get.mockReset();
});

function alert(over: Partial<DecisionAlert> = {}): DecisionAlert {
  return {
    id: "gnpa_breach", severity: "critical", title: "GNPA at 6.2%, past the 5% alert line",
    summary: "NPA exposure is 6.2% of the book.", metrics: [{ label: "GNPA %", value: "6.2%" }],
    actions: [{ label: "Open Exposure", target: "exposure" }],
    basis: "NPA exposure divided by the whole book's outstanding. Fires past 5%.",
    ...over,
  };
}

function show(alerts: DecisionAlert[]) {
  api.get.mockImplementation((url: string) =>
    Promise.resolve({ data: url === "/bank/alerts" ? alerts : {} }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AlertsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("AlertsPage", () => {
  it("groups alerts into their severity sections", async () => {
    show([
      alert(),
      alert({ id: "agency_sla_miss", severity: "warning", title: "Coromandel: 4 first visits missed their SLA this month" }),
      alert({ id: "pending_placement", severity: "info", title: "₹2.10 Cr delinquent and never placed with an agency" }),
    ]);
    await waitFor(() => expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy());
    expect(screen.getByText("Critical")).toBeTruthy();
    expect(screen.getByText("Warning")).toBeTruthy();
    expect(screen.getByText("Info")).toBeTruthy();
    expect(screen.getByText(/Coromandel: 4 first visits/)).toBeTruthy();
    expect(screen.getByText(/never placed with an agency/)).toBeTruthy();
  });

  it("does not render a section for a severity with nothing firing", async () => {
    show([alert()]);
    await waitFor(() => expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy());
    expect(screen.getByText("Critical")).toBeTruthy();
    expect(screen.queryByText("Warning")).toBeNull();
    expect(screen.queryByText("Info")).toBeNull();
  });

  it("says plainly when nothing is firing, not an error", async () => {
    show([]);
    await waitFor(() => expect(screen.getByText(/No rule is firing on the book right now/)).toBeTruthy());
  });
});
