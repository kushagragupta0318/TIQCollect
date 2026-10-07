// @vitest-environment jsdom
/**
 * Admin > Regions. Three behaviours worth a test:
 *
 *  · every node on screen shows its OWN rolled-up figures (a child's loans
 *    are not double-counted into a sibling, and a parent's figures are not
 *    recomputed in the component — they come straight off the response);
 *  · a deeper node starts collapsed and expands on click;
 *  · the unassigned-branches banner appears only when there is something to
 *    disclose, never as a misleading "0 branches" line.
 *
 * The API is mocked; the roll-up arithmetic itself is tested in
 * backend/tests/test_bank_regions_tree_api.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { RegionTreeResponse } from "@/api/bank";
import RegionsPage from "./RegionsPage";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const TREE: RegionTreeResponse = {
  roots: [
    {
      id: "z-north", level: "ZONE", code: "NORTH", name: "North", loan_count: 2, exposure: 562000, agency_count: 1,
      children: [
        {
          id: "s-hr", level: "STATE", code: "HR", name: "Haryana", loan_count: 2, exposure: 562000, agency_count: 1,
          children: [
            {
              id: "c-ggn", level: "CITY", code: "GGN", name: "Gurugram", loan_count: 2, exposure: 562000, agency_count: 1,
              children: [
                { id: "b-ggn044", level: "BRANCH", code: "GGN044", name: "Meridian Trust Bank GGN044",
                  loan_count: 1, exposure: 281000, agency_count: 1, children: [] },
                { id: "b-gg01", level: "BRANCH", code: "GG01", name: "Meridian Trust Bank GG01",
                  loan_count: 1, exposure: 281000, agency_count: 0, children: [] },
              ],
            },
          ],
        },
      ],
    },
  ],
  unassigned: { branch_count: 6, loan_count: 0, exposure: 0 },
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><RegionsPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("RegionsPage", () => {
  it("shows the rolled-up zone and the unassigned banner, with the city collapsed by default", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: TREE });
    renderPage();

    expect(await screen.findByText("North")).toBeTruthy();
    // North (open by default) and its only child Haryana both show "2
    // loans" / the same rolled-up exposure — a single-child subtree summing
    // to the same total as its parent, not a bug to query around.
    expect(screen.getAllByText("2 loans").length).toBeGreaterThan(0);
    expect(screen.getAllByText("₹5,62,000").length).toBeGreaterThan(0);
    expect(screen.getByText(/6 branches with no region/)).toBeTruthy();

    // STATE is depth 1 and starts expanded (depth < 1), CITY is depth 2 and starts collapsed.
    expect(screen.getByText("Haryana")).toBeTruthy();
    expect(screen.queryByText("Gurugram")).toBeNull();
  });

  it("expands a deeper node on click, down to its branch leaves", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: TREE });
    renderPage();

    await screen.findByText("Haryana");
    fireEvent.click(screen.getByText("Haryana"));
    expect(await screen.findByText("Gurugram")).toBeTruthy();

    fireEvent.click(screen.getByText("Gurugram"));
    expect(await screen.findByText(/GGN044/)).toBeTruthy();
    expect(screen.getByText(/\bGG01\b/)).toBeTruthy();
  });

  it("omits the unassigned banner when every branch has a region", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...TREE, unassigned: { branch_count: 0, loan_count: 0, exposure: 0 } },
    });
    renderPage();

    await screen.findByText("North");
    expect(screen.queryByText(/branch(es)? with no region/)).toBeNull();
  });
});
