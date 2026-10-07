// @vitest-environment jsdom
/**
 * Board Reports (E10). The figures and the rendered file are the backend's
 * job (backend/tests/test_bank_reports_api.py, test_report_engine.py); this
 * is the page's own plumbing: picking a pack, gating Generate on an agency
 * when the pack needs one, and surfacing the download link it gets back.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import BoardReportsPage from "./BoardReportsPage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));

// jsdom has no real window.open; a success handler calling it is normal here
// (the page opens the freshly generated file), not something this suite tests.
vi.stubGlobal("open", vi.fn());

const REPORT_TYPES = [
  { template: "board", name: "Board Pack", description: "Portfolio KPIs for the board.", requires_agency: false },
  { template: "agency_review", name: "Agency Review", description: "One agency's scorecard.", requires_agency: true },
];

const AGENCIES = [{
  agency_id: "ag-1", code: "AGENCY-TIQ-001", legal_name: "Aravalli Field Services Pvt. Ltd.",
  trade_name: "Aravalli Field Services", status: "ACTIVE", activated_at: null,
  covered_regions: [], authorised_products: [],
}];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><BoardReportsPage /></QueryClientProvider>);
}

function mockGet(byUrl: Record<string, unknown>) {
  vi.mocked(api.get).mockImplementation(((url: string) =>
    url in byUrl ? Promise.resolve({ data: byUrl[url] }) : Promise.reject(new Error(`unexpected GET ${url}`))
  ) as typeof api.get);
}

afterEach(() => {
  cleanup();
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
});

describe("Board Reports", () => {
  it("generates a board pack and shows its download link, without a date range control", async () => {
    mockGet({ "/bank/reports/types": REPORT_TYPES });
    vi.mocked(api.post).mockResolvedValue({
      data: {
        report_id: "board-bank1-2026-10-31", format: "pdf", url: "https://files.example/board.pdf",
        expires_minutes: 15, size_bytes: 48213, sha256: "abc123",
      },
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /generate/i }));

    expect(await screen.findByText(/board-bank1-2026-10-31/)).toBeTruthy();
    const link = screen.getByRole("link", { name: /download/i });
    expect(link.getAttribute("href")).toBe("https://files.example/board.pdf");
    expect(api.post).toHaveBeenCalledWith(
      "/bank/reports/generate",
      expect.objectContaining({ template: "board", format: "pdf", period: "mtd" }),
    );
  });

  it("disables Generate for Agency Review until an agency is picked, and sends no period for it", async () => {
    mockGet({ "/bank/reports/types": REPORT_TYPES, "/bank/agencies-directory": AGENCIES });
    vi.mocked(api.post).mockResolvedValue({
      data: {
        report_id: "agency_review-bank1-ag-1-2026-09-30", format: "pptx", url: "https://files.example/ar.pptx",
        expires_minutes: 15, size_bytes: 102400, sha256: "def456",
      },
    });
    renderPage();

    // Wait for the report types to load — their own descriptions are what
    // populate the "Report" select's options, including Agency Review's.
    await screen.findByText(/portfolio kpis for the board/i);
    fireEvent.change(screen.getByLabelText("Report"), { target: { value: "agency_review" } });
    const button = screen.getByRole("button", { name: /generate/i }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/pick an agency first/i)).toBeTruthy();

    await screen.findByRole("option", { name: "Aravalli Field Services" });
    fireEvent.change(screen.getByLabelText("Agency"), { target: { value: "ag-1" } });
    expect(button.disabled).toBe(false);

    fireEvent.click(button);
    expect(await screen.findByText(/agency_review-bank1-ag-1-2026-09-30/)).toBeTruthy();
    const body = vi.mocked(api.post).mock.calls[0][1] as Record<string, unknown>;
    expect(body).toMatchObject({ template: "agency_review", agency_id: "ag-1" });
    expect(body.period).toBeUndefined();
  });

  it("names the missing capability on a 403, not a role", async () => {
    mockGet({ "/bank/reports/types": REPORT_TYPES });
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: { detail: "Access denied. Required capability: reports.download" } },
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /generate/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/capability this role does not hold/);
    expect(alert.textContent).not.toMatch(/BANK_ADMIN|BANK_ANALYST/);
  });
});
