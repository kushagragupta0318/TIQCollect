// @vitest-environment jsdom
/**
 * Admin → Settings (K01). Behaviours worth a test:
 *  · the page renders the editable policy AND the read-only engine constants,
 *    with the engine values shown from the response (not restated);
 *  · a save PATCHes only the fields the admin changed (a partial update);
 *  · the DPD thresholds render as read-only rows.
 * The API is mocked; the service's arithmetic is tested in
 * backend/tests/test_bank_settings.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { BankSettings } from "@/api/bankSettings";
import SettingsPage from "./SettingsPage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), patch: vi.fn() } }));
vi.mock("react-hot-toast", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

const settings = (o: Partial<BankSettings> = {}): BankSettings => ({
  bank: { id: "bank-1", display_name: "Meridian Trust Bank", timezone: "Asia/Kolkata" },
  contact_hours: { start: 8, end: 19, default_start: 8, default_end: 19, is_override: false, editable: true },
  geofence_metres: { value: 100, default: 100, is_override: false, editable: true },
  sla_first_visit_days: { value: 7, default: 7, is_override: false, editable: true },
  engine_constants: [
    { key: "allocator_exploration_rate", label: "Allocation exploration rate", value: 0.1,
      unit: "fraction", description: "Exploration share.", editable: false },
    { key: "location_retention_days", label: "GPS location retention", value: 90,
      unit: "days", description: "GPS sweep.", editable: false },
  ],
  dpd_buckets: [
    { bucket: "CURRENT", label: "0 DPD", min_dpd: 0, max_dpd: 0 },
    { bucket: "BUCKET_1", label: "1–30 DPD", min_dpd: 1, max_dpd: 30 },
    { bucket: "NPA", label: "91+ DPD", min_dpd: 91, max_dpd: null },
  ],
  note: "Editable values are stored and audited.",
  ...o,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><SettingsPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsPage", () => {
  it("renders editable policy and read-only engine constants", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: settings() });
    renderPage();
    expect(await screen.findByText("Contact hours")).toBeTruthy();
    // engine constant surfaced from the response, read-only table
    expect(screen.getByText("Allocation exploration rate")).toBeTruthy();
    expect(screen.getByText("Engine constants")).toBeTruthy();
    // DPD threshold row rendered
    expect(screen.getByText("91+ DPD")).toBeTruthy();
    // the geo-fence input is seeded from the loaded value
    expect((screen.getByLabelText(/Radius/i) as HTMLInputElement).value).toBe("100");
  });

  it("saves only the fields that changed (partial PATCH)", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: settings() });
    vi.mocked(api.patch).mockResolvedValue({ data: settings({ geofence_metres: { value: 150, default: 100, is_override: true, editable: true } }) });
    renderPage();
    await screen.findByText("Contact hours");

    fireEvent.change(screen.getByLabelText(/Radius/i), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: /Save settings/i }));

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    // only geofence_metres in the body — contact hours and SLA were untouched
    expect(api.patch).toHaveBeenCalledWith("/bank/settings", { geofence_metres: 150 });
  });

  it("shows a failure state when the read fails", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText(/could not load/i)).toBeTruthy();
  });
});
