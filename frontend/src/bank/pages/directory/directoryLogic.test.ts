import { describe, expect, it } from "vitest";
import { CONTRACT_EXPIRY_WARN_DAYS, contractSummary, performanceCell, productLabels, regionMarkersFromAgencies, summariseCoveredRegions } from "./directoryLogic";
import type { AgencyDirectoryRow, PerformanceIndex } from "@/api/bank";

function row(overrides: Partial<AgencyDirectoryRow>): AgencyDirectoryRow {
  return {
    agency_id: "a1", code: "AG-001", legal_name: "Sarthak Recovery Services LLP", trade_name: null,
    entity_type: null, cin: null, rbi_registration_no: null, pan: null, gstin: null,
    registered_address: null, hq_city: null, website: null, contacts: null,
    contact_name: null, contact_email: null, contact_phone: null,
    status: "ACTIVE", activated_at: null,
    contract: null, covered_regions: [], authorised_products: [],
    ...overrides,
  };
}

const NCR = { region_id: "r-ncr", name: "NCR", level: "REGION", latitude: 28.5, longitude: 77.1 };
const MUMBAI = { region_id: "r-mum", name: "Mumbai", level: "CITY", latitude: 19.07, longitude: 72.87 };
const NO_LOCATION = { region_id: "r-none", name: "Unmapped Zone", level: "ZONE", latitude: null, longitude: null };

describe("regionMarkersFromAgencies — the coverage map's data source", () => {
  it("no rows, no markers", () => {
    expect(regionMarkersFromAgencies([])).toEqual([]);
  });

  it("skips a covered region that carries no lat/lng", () => {
    const rows = [row({ covered_regions: [NO_LOCATION] })];
    expect(regionMarkersFromAgencies(rows)).toEqual([]);
  });

  it("one marker per region, tagged with the covering agency's name", () => {
    const rows = [row({ legal_name: "Sarthak Recovery Services LLP", covered_regions: [NCR] })];
    const markers = regionMarkersFromAgencies(rows);
    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ region_id: "r-ncr", latitude: 28.5, longitude: 77.1, agencyNames: ["Sarthak Recovery Services LLP"] });
  });

  it("prefers trade_name over legal_name for the marker label", () => {
    const rows = [row({ legal_name: "Sarthak Recovery Services LLP", trade_name: "Sarthak Recovery Services", covered_regions: [NCR] })];
    expect(regionMarkersFromAgencies(rows)[0].agencyNames).toEqual(["Sarthak Recovery Services"]);
  });

  it("two agencies covering the same region collapse into one marker with both names", () => {
    const rows = [
      row({ agency_id: "a1", legal_name: "Sarthak Recovery Services LLP", covered_regions: [NCR] }),
      row({ agency_id: "a2", legal_name: "Awadh Field Collections Pvt. Ltd.", covered_regions: [NCR] }),
    ];
    const markers = regionMarkersFromAgencies(rows);
    expect(markers).toHaveLength(1);
    expect(markers[0].agencyNames).toEqual(["Sarthak Recovery Services LLP", "Awadh Field Collections Pvt. Ltd."]);
  });

  it("distinct regions produce distinct markers", () => {
    const rows = [row({ covered_regions: [NCR, MUMBAI] })];
    const markers = regionMarkersFromAgencies(rows);
    expect(markers.map((m) => m.region_id).sort()).toEqual(["r-mum", "r-ncr"]);
  });
});

describe("summariseCoveredRegions — the table's covered-regions cell", () => {
  it("no regions", () => {
    expect(summariseCoveredRegions([])).toEqual({ shown: "", full: "", count: 0 });
  });

  it("at or under the limit shows every name, no '+N more'", () => {
    const regions = [{ name: "NCR" }, { name: "Mumbai" }];
    expect(summariseCoveredRegions(regions, 3)).toEqual({ shown: "NCR, Mumbai", full: "NCR, Mumbai", count: 2 });
  });

  it("over the limit truncates and appends the remaining count", () => {
    const regions = [{ name: "NCR" }, { name: "Mumbai" }, { name: "Pune" }, { name: "Nashik" }];
    const result = summariseCoveredRegions(regions, 2);
    expect(result.shown).toBe("NCR, Mumbai +2 more");
    expect(result.full).toBe("NCR, Mumbai, Pune, Nashik");
    expect(result.count).toBe(4);
  });
});

describe("contractSummary", () => {
  const today = new Date(2026, 8, 30);      // 30 Sep 2026, local

  it("formats the end date and counts whole days to it", () => {
    expect(contractSummary("2027-03-31", today)).toEqual({ ends: "Ends 31 Mar 2027", daysLeft: 182, tone: "normal" });
  });

  it("calls out a contract ending inside the warning window, boundary included", () => {
    const edge = new Date(2026, 8, 30 + CONTRACT_EXPIRY_WARN_DAYS);
    const iso = `${edge.getFullYear()}-${String(edge.getMonth() + 1).padStart(2, "0")}-${String(edge.getDate()).padStart(2, "0")}`;
    expect(contractSummary(iso, today)).toMatchObject({ daysLeft: CONTRACT_EXPIRY_WARN_DAYS, tone: "soon" });
    expect(contractSummary("2026-09-30", today)).toMatchObject({ daysLeft: 0, tone: "soon", ends: "Ends 30 Sep 2026" });
  });

  it("says Ended once the date has passed", () => {
    expect(contractSummary("2026-09-29", today)).toEqual({ ends: "Ended 29 Sep 2026", daysLeft: -1, tone: "past" });
  });

  it("does not shift the day for a viewer west of UTC (the date is a calendar date, not an instant)", () => {
    expect(contractSummary("2027-01-01", new Date(2026, 11, 31, 23, 59)).daysLeft).toBe(1);
  });

  it("shows a non-date value as it came", () => {
    expect(contractSummary("open-ended", today)).toEqual({ ends: "Ends open-ended", daysLeft: null, tone: "normal" });
  });
});

describe("productLabels", () => {
  it("reads codes as labels and keeps an unknown code as itself", () => {
    expect(productLabels(["PERSONAL", "CREDIT_CARD", "LEASE"])).toBe("Personal loan, Credit card, LEASE");
    expect(productLabels([])).toBe("");
  });
});

describe("performanceCell", () => {
  const idx = (agency_id: string, region_id: string | null, index: number | null, n = 12): PerformanceIndex => ({
    agency_id, region_id, month_start: "2026-08-01", months: 1, n, months_unread: 0, raw_rate: null, peer_rate: null,
    shrunk_rate: null, index, multiplier: 1, version: "agency-effect-1",
  });
  const rows = [idx("a1", "delhi", 71.6, 14), idx("a1", "noida", null, 2), idx("a1", "gurugram", 64, 9), idx("a2", "delhi", 55)];

  it("with a region picked: that region's index and its evidence", () => {
    expect(performanceCell(rows, "a1", "delhi")).toEqual({ kind: "scored", index: 71.6, n: 14 });
  });

  it("with a region picked: 'not enough data' is kept apart from 'no score here'", () => {
    expect(performanceCell(rows, "a1", "noida")).toEqual({ kind: "insufficient" });
    expect(performanceCell(rows, "a1", "jaipur")).toEqual({ kind: "not_scored_here" });
  });

  it("without a region: counts scored regions, never averages them", () => {
    expect(performanceCell(rows, "a1", null)).toEqual({ kind: "regions", scored: 2, total: 3 });
    expect(performanceCell(rows, "a3", null)).toEqual({ kind: "regions", scored: 0, total: 0 });
  });

  it("never reads another agency's row", () => {
    expect(performanceCell(rows, "a2", "noida")).toEqual({ kind: "not_scored_here" });
  });
});
