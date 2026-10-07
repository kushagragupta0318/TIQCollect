// @vitest-environment jsdom
/**
 * Admin > Bank Users (K01). Behaviours worth a test, API mocked:
 *
 *  · the page lists the bank's staff with role, status, MFA and last login;
 *  · a BANK_ADMIN row offers no actions (a fellow admin / yourself is out of
 *    reach — the server would refuse), while an analyst row offers the
 *    role/deactivate/reset actions;
 *  · deactivating goes through a reason dialog and calls the right endpoint.
 *
 * The backend rules (scope, who-may-act-on-whom, audit) are tested in
 * backend/tests/test_bank_users_admin.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { BankUser } from "@/api/bankUsers";
import BankUsersPage from "./BankUsersPage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() }, LONG_RUNNING_MS: 180_000 }));

const user = (o: Partial<BankUser> & { user_id: string; full_name: string; role: BankUser["role"] }): BankUser => ({
  email: `${o.user_id}@example.in`, phone: "9800000000", is_active: true, status: "ACTIVE",
  mfa_enabled: false, mfa_required: false, last_login_at: null, ...o,
});

const ROWS: BankUser[] = [
  user({ user_id: "admin", full_name: "Asha Admin", role: "BANK_ADMIN", mfa_enabled: true,
         last_login_at: "2026-10-05T09:00:00+00:00" }),
  user({ user_id: "nilesh", full_name: "Nilesh Analyst", role: "BANK_ANALYST", mfa_required: true }),
  user({ user_id: "tara", full_name: "Tara Techops", role: "BANK_TECHOPS", is_active: false, status: "DEACTIVATED" }),
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><BankUsersPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
});

describe("Bank Users page", () => {
  it("lists the bank's staff with role, status and MFA", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: ROWS });
    renderPage();

    expect(await screen.findByText("Nilesh Analyst")).toBeTruthy();
    expect(screen.getByText("Asha Admin")).toBeTruthy();
    expect(screen.getByText("Tara Techops")).toBeTruthy();
    // the deactivated techops shows a Deactivated status
    expect(screen.getByText("Deactivated")).toBeTruthy();
    // MFA states: admin On, analyst Required
    expect(screen.getByText("On")).toBeTruthy();
    expect(screen.getByText("Required")).toBeTruthy();
  });

  it("offers no actions on a BANK_ADMIN row, and the lifecycle actions on others", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: ROWS });
    renderPage();

    const adminRow = (await screen.findByText("Asha Admin")).closest("tr")!;
    expect(within(adminRow).queryByRole("button")).toBeNull();

    const analystRow = screen.getByText("Nilesh Analyst").closest("tr")!;
    // an active analyst can be made Tech Ops, deactivated, and reset
    expect(within(analystRow).getByText(/Make Tech Ops/)).toBeTruthy();
    expect(within(analystRow).getByText(/Deactivate/)).toBeTruthy();
    expect(within(analystRow).getByText(/Reset login/)).toBeTruthy();

    // the deactivated techops offers Reactivate instead of Deactivate
    const techopsRow = screen.getByText("Tara Techops").closest("tr")!;
    expect(within(techopsRow).getByText(/Reactivate/)).toBeTruthy();
    expect(within(techopsRow).queryByText(/^Deactivate$/)).toBeNull();
  });

  it("deactivates through the reason dialog", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: ROWS });
    vi.mocked(api.post).mockResolvedValue({ data: { ...ROWS[1], is_active: false, status: "DEACTIVATED" } });
    renderPage();

    const analystRow = (await screen.findByText("Nilesh Analyst")).closest("tr")!;
    fireEvent.click(within(analystRow).getByText(/Deactivate/));

    const reason = await screen.findByLabelText("Reason");
    fireEvent.change(reason, { target: { value: "Left the team" } });
    // the dialog's own confirm button (the row's Deactivate also matches the name)
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Deactivate$/ }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith("/bank/users/nilesh/deactivate", { reason: "Left the team" });
    });
  });
});
