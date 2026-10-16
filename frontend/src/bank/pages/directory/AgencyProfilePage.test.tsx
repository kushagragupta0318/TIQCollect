// @vitest-environment jsdom
/**
 * The agency profile's Agency details card and its Edit action. API mocked:
 *
 *  · Edit is BANK_ADMIN's alone — agency.update belongs to that role only
 *    (core/permissions), so an analyst must not be offered a button the
 *    server would refuse;
 *  · saving PATCHes the edited fields to this agency;
 *  · the save carries only what the card shows, so a field edited elsewhere
 *    (CIN, PAN, the contacts list) is never blanked by a save from here.
 *
 * The server's rules — who may call it, scope, audit — are tested in
 * backend/tests/test_bank_agencies_admin.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { BankAgencyProfile } from "@/api/bank";
import { useAuthStore } from "@/store/authStore";
import AgencyProfilePage from "./AgencyProfilePage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() }, LONG_RUNNING_MS: 180_000 }));

const AGENCY_ID = "8fc42163-143e-50bc-a8ac-74c09168a0ed";

const PROFILE: BankAgencyProfile = {
  agency: {
    legal_name: "Aravalli Field Services Pvt. Ltd.",
    trade_name: "Aravalli Field Services",
    rbi_registration_no: null,
    status: "ACTIVE",
    hq_city: "Gurugram",
    contact_name: "Meera Khanna",
    contact_email: "meera.khanna@aravallifs.test",
    contact_phone: "9810460212",
  },
  contract: null,
  commission_terms: [],
  placed_volume: { active_count: 0, active_exposure: 0, lifetime_count: 0 },
  people: { agent_count: 0, agents_on_duty: 0, managers: [] },
};

function renderPage(role: string) {
  useAuthStore.setState({ user: { id: "u1", email: "b@t.test", full_name: "B", role, is_active: true } } as never);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/bank/agencies/profile/${AGENCY_ID}`]}>
        <Routes>
          <Route path="/bank/agencies/profile/:agencyId" element={<AgencyProfilePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.patch).mockReset();
});

describe("the agency profile's Agency details card", () => {
  it("offers Edit to a BANK_ADMIN", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: PROFILE });
    renderPage("BANK_ADMIN");
    expect(await screen.findByRole("button", { name: /Edit/ })).toBeTruthy();
  });

  it("offers no Edit to an analyst, who could not save it", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: PROFILE });
    renderPage("BANK_ANALYST");
    // Wait for the card itself, so this is "loaded and absent", not "not yet".
    await screen.findByText("Gurugram");
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
  });

  it("saves the edited fields to this agency, and only the fields on the card", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: PROFILE });
    vi.mocked(api.patch).mockResolvedValue({ data: PROFILE.agency });
    renderPage("BANK_ADMIN");

    fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
    fireEvent.change(await screen.findByLabelText("RBI registration"), { target: { value: "REG/2024/0188" } });
    fireEvent.change(screen.getByLabelText("HQ city"), { target: { value: "Noida" } });
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(vi.mocked(api.patch)).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(api.patch).mock.calls[0];
    expect(url).toBe(`/bank/agencies/${AGENCY_ID}`);
    expect(body).toMatchObject({
      legal_name: "Aravalli Field Services Pvt. Ltd.",   // unchanged, required by the request shape
      rbi_registration_no: "REG/2024/0188",
      hq_city: "Noida",
      contact_name: "Meera Khanna",
    });
    // Never sent from this card: blanking these is how an edit here would
    // destroy data the onboarding form owns.
    for (const key of ["cin", "pan", "gstin", "registered_address", "contacts", "status"]) {
      expect(body).not.toHaveProperty(key);
    }
  });
});
