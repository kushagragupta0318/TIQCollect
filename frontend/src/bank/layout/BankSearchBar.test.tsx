// @vitest-environment jsdom
/**
 * Borrower lookup in the top bar.
 *
 * The server decides what may be found (bank from the caller's row, region
 * limit, uniform emptiness); what is pinned here is that the component does not
 * undo any of it — it asks only once typing pauses, never for a query too short
 * to identify anyone, and an empty answer is rendered the same way whatever the
 * reason, so the box cannot become an oracle for "does this borrower exist".
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import api from "@/api/axios";
import { BankSearchBar } from "./BankSearchBar";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const EMPTY = { items: [], query_too_short: false, min_query_length: 3, truncated: false };

const hit = (o: Record<string, unknown> = {}) => ({
  customer_id: "cu-1", full_name: "Farhan Siddiqui", city: "Gurugram",
  loans: 2, first_account: "LN00000001", ...o,
});

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/bank/overview"]}>
        <BankSearchBar />
        <Where />
        <Routes><Route path="*" element={null} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByLabelText("Search");
}

const type = (box: HTMLElement, value: string) => fireEvent.change(box, { target: { value } });

afterEach(() => {
  cleanup();
  vi.mocked(api.get).mockReset();
});

describe("borrower lookup", () => {
  it("does not ask the server for a query too short to identify anyone", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: EMPTY });
    const box = renderBar();
    type(box, "Fa");
    await new Promise((r) => setTimeout(r, 400));
    expect(vi.mocked(api.get)).not.toHaveBeenCalled();
  });

  it("asks once typing pauses, and opens the borrower's page when a hit is chosen", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { ...EMPTY, items: [hit()] } });
    const box = renderBar();
    type(box, "Sid");
    type(box, "Siddiqui");            // still mid-word: the earlier timer is dropped

    const row = await screen.findByText("Farhan Siddiqui");
    expect(vi.mocked(api.get)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.get).mock.calls[0]).toEqual(["/bank/customers/search", { params: { q: "Siddiqui" } }]);
    expect(screen.getByText("Gurugram · LN00000001 · 2 loans")).toBeTruthy();

    fireEvent.mouseDown(row);
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/bank/customers/cu-1"));
  });

  it("renders an empty answer the same way whatever the reason for it", async () => {
    // Nothing matched, and "matched only outside your scope", are the same body
    // from the server; the box must not tell them apart either.
    vi.mocked(api.get).mockResolvedValue({ data: EMPTY });
    const box = renderBar();
    type(box, "Venkataraghavan");
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalled());
    const message = await screen.findByText(/No results for/);
    expect(message.textContent).not.toMatch(/bank|region|permission|access/i);
  });

  it("shows no borrower rows when the server says the query was too short", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { ...EMPTY, query_too_short: true, items: [hit()] } });
    const box = renderBar();
    type(box, "Siddiqui");
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalled());
    expect(screen.queryByText("Farhan Siddiqui")).toBeNull();
  });

  it("keeps page hits working when the lookup fails, rather than blanking the box", async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 500, data: {} } });
    const box = renderBar();
    type(box, "Overview");
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalled());
    expect(screen.getByText("Overview")).toBeTruthy();
  });
});
