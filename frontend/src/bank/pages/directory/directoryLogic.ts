// Pure helpers for the agency directory page (D05) — kept out of
// AgencyDirectoryPage.tsx so they run without React or Leaflet, same split
// as onboardingLogic.ts next door.
import type { AgencyDirectoryRow } from "@/api/bank";
import { LOAN_TYPE_LABELS, labelFor } from "../../lib/labels";
import { pyDate } from "../../theme/format";

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

/** "2027-03-31" → a local calendar date, never shifted by the UTC parse of Date("…"). */
function calendarDate(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

/** Contracts ending within this many days are called out in the table. */
export const CONTRACT_EXPIRY_WARN_DAYS = 90;

export interface ContractSummary {
  /** "Ends 31 Mar 2027", or the raw value when it is not a date. */
  ends: string;
  /** Whole days from `today` to the end date; negative once it has passed. */
  daysLeft: number | null;
  tone: "normal" | "soon" | "past";
}

/** The contract cell's second line. `today` is the viewer's calendar day. */
export function contractSummary(endDate: string, today: Date): ContractSummary {
  const end = calendarDate(endDate);
  if (!end) return { ends: `Ends ${endDate}`, daysLeft: null, tone: "normal" };
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const daysLeft = Math.round((end.getTime() - start.getTime()) / 86_400_000);
  const tone = daysLeft < 0 ? "past" : daysLeft <= CONTRACT_EXPIRY_WARN_DAYS ? "soon" : "normal";
  return { ends: `${daysLeft < 0 ? "Ended" : "Ends"} ${pyDate(end)}`, daysLeft, tone };
}

/** Product codes as a bank user reads them; an unknown code shows as itself. */
export function productLabels(codes: string[]): string {
  return codes.map((c) => labelFor(LOAN_TYPE_LABELS, c)).join(", ");
}
