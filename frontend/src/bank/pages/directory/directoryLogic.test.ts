import { describe, expect, it } from "vitest";
import { regionMarkersFromAgencies, summariseCoveredRegions } from "./directoryLogic";
import type { AgencyDirectoryRow } from "@/api/bank";

function row(overrides: Partial<AgencyDirectoryRow>): AgencyDirectoryRow {
  return {
    agency_id: "a1", code: "AG-001", legal_name: "Konkan Recovery Services LLP", trade_name: null,
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
    const rows = [row({ legal_name: "Konkan Recovery Services LLP", covered_regions: [NCR] })];
    const markers = regionMarkersFromAgencies(rows);
    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ region_id: "r-ncr", latitude: 28.5, longitude: 77.1, agencyNames: ["Konkan Recovery Services LLP"] });
  });

  it("prefers trade_name over legal_name for the marker label", () => {
    const rows = [row({ legal_name: "Konkan Recovery Services LLP", trade_name: "Konkan Recovery", covered_regions: [NCR] })];
    expect(regionMarkersFromAgencies(rows)[0].agencyNames).toEqual(["Konkan Recovery"]);
  });

  it("two agencies covering the same region collapse into one marker with both names", () => {
    const rows = [
      row({ agency_id: "a1", legal_name: "Konkan Recovery Services LLP", covered_regions: [NCR] }),
      row({ agency_id: "a2", legal_name: "Meridian Debt Solutions Pvt Ltd", covered_regions: [NCR] }),
    ];
    const markers = regionMarkersFromAgencies(rows);
    expect(markers).toHaveLength(1);
    expect(markers[0].agencyNames).toEqual(["Konkan Recovery Services LLP", "Meridian Debt Solutions Pvt Ltd"]);
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
