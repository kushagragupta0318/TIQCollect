// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { AlertsPage } from "./AlertsPage";
import type { BankAlertsPayload } from "@/api/bankAlerts";
import type { DecisionAlert } from "../components/DecisionAlerts";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn() } }));
const api = (await import("@/api/axios")).default as unknown as { get: ReturnType<typeof vi.fn> };

const navigateSpy = vi.fn();
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => navigateSpy };
});

afterEach(() => {
  cleanup();
  api.get.mockReset();
  navigateSpy.mockReset();
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

function payload(over: Partial<BankAlertsPayload> = {}): BankAlertsPayload {
  return { alerts: [alert()], rules_total: 6, rules_failed: [], ...over };
}

function show(body: BankAlertsPayload) {
  api.get.mockImplementation((url: string) =>
    Promise.resolve({ data: url === "/bank/alerts" ? body : {} }));
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
    show(payload({
      alerts: [
        alert(),
        alert({ id: "agency_sla_miss", severity: "warning", title: "Coromandel: 4 first visits missed their SLA this month" }),
        alert({ id: "pending_placement", severity: "info", title: "₹2.10 Cr delinquent and never placed with an agency" }),
      ],
    }));
    await waitFor(() => expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy());
    expect(screen.getByText("Critical")).toBeTruthy();
    expect(screen.getByText("Warning")).toBeTruthy();
    expect(screen.getByText("Info")).toBeTruthy();
    expect(screen.getByText(/Coromandel: 4 first visits/)).toBeTruthy();
    expect(screen.getByText(/never placed with an agency/)).toBeTruthy();
  });

  it("does not render a section for a severity with nothing firing", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy());
    expect(screen.getByText("Critical")).toBeTruthy();
    expect(screen.queryByText("Warning")).toBeNull();
    expect(screen.queryByText("Info")).toBeNull();
  });

  it("says plainly when nothing is firing and every rule ran clean", async () => {
    show(payload({ alerts: [] }));
    await waitFor(() => expect(screen.getByText(/No rule is firing on the book right now/)).toBeTruthy());
  });

  it("admits a failed rule rather than reading it as a clean 'nothing is firing'", async () => {
    show(payload({ alerts: [], rules_failed: ["Roll-forward spike"] }));
    await waitFor(() => expect(screen.getByText(/1 of 6 rules could not be evaluated/)).toBeTruthy());
    expect(screen.getByText(/Roll-forward spike/)).toBeTruthy();
    expect(screen.getByText(/No rule that DID run is firing right now/)).toBeTruthy();
    expect(screen.queryByText(/No rule is firing on the book right now/)).toBeNull();
  });

  it("shows the degraded banner even when some rules did fire", async () => {
    show(payload({ rules_failed: ["Fraud / off-hours / out-of-fence"] }));
    await waitFor(() => expect(screen.getByText(/1 of 6 rules could not be evaluated/)).toBeTruthy());
    expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy();
  });

  it("navigates to the Exposure tab when an alert's action button is clicked", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy());
    fireEvent.click(screen.getByText("GNPA at 6.2%, past the 5% alert line"));   // open the card
    fireEvent.click(screen.getByText("Open Exposure"));
    expect(navigateSpy).toHaveBeenCalledWith("/bank/analytics?tab=exposure");
  });

  it("does nothing (and does not throw) for a target with no route yet", async () => {
    show(payload({ alerts: [alert({ actions: [{ label: "Open Mystery", target: "mystery" }] })] }));
    await waitFor(() => expect(screen.getByText(/GNPA at 6\.2%/)).toBeTruthy());
    fireEvent.click(screen.getByText("GNPA at 6.2%, past the 5% alert line"));
    fireEvent.click(screen.getByText("Open Mystery"));
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});
