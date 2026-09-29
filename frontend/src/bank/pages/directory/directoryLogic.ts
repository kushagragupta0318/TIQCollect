// Pure helpers for the agency directory page (D05) — kept out of
// AgencyDirectoryPage.tsx so they run without React or Leaflet, same split
// as onboardingLogic.ts next door.
import type { AgencyDirectoryRow } from "@/api/bank";

export interface DirectoryRegionMarker {
  region_id: string;
  name: string;
  level: string;
  latitude: number;
  longitude: number;
  /** Legal or trade names of every visible agency covering this region. */
  agencyNames: string[];
}

/**
 * One marker per distinct region (that carries a lat/lng) covered by any of
 * the given rows, each tagged with which agencies cover it.
 *
 * The map plots by REGION, not by agency: two agencies covering the same
 * region collapse into one pin with two names in its tooltip, instead of two
 * stacked, indistinguishable markers at the same point.
 */
export function regionMarkersFromAgencies(rows: AgencyDirectoryRow[]): DirectoryRegionMarker[] {
  const byRegion = new Map<string, DirectoryRegionMarker>();
  for (const row of rows) {
    const label = row.trade_name?.trim() || row.legal_name;
    for (const region of row.covered_regions) {
      if (region.latitude == null || region.longitude == null) continue;
      const existing = byRegion.get(region.region_id);
      if (existing) {
        if (!existing.agencyNames.includes(label)) existing.agencyNames.push(label);
      } else {
        byRegion.set(region.region_id, {
          region_id: region.region_id,
          name: region.name,
          level: region.level,
          latitude: region.latitude,
          longitude: region.longitude,
          agencyNames: [label],
        });
      }
    }
  }
  return Array.from(byRegion.values());
}

/**
 * The table's "covered regions" cell: the first `limit` region names, plus a
 * "+N more" count when there are more than that. `full` is every name,
 * joined, for the caller to put on a `title` attribute — nothing here
 * truncates data, only how much of it renders inline.
 */
export function summariseCoveredRegions(
  regions: { name: string }[],
  limit = 3,
): { shown: string; full: string; count: number } {
  const names = regions.map((r) => r.name);
  const shown = names.length <= limit ? names.join(", ") : `${names.slice(0, limit).join(", ")} +${names.length - limit} more`;
  return { shown, full: names.join(", "), count: names.length };
}
