/**
 * Distance helpers for the field-agent views.
 *
 * `haversineM` was copy-pasted in RecordVisitPage and AgentCaseDetailPage;
 * this is the same maths, extracted so the case list could use it too rather
 * than adding a third copy.
 */

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance in metres. Mirrors backend/app/core/geo.py. */
export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Short human distance: metres up close, kilometres beyond 1 km.
 * Kept terse because it sits inside a dense list row.
 */
export function formatDistance(metres: number): string {
  if (!Number.isFinite(metres)) return "";
  if (metres < 1000) return `${Math.round(metres)} m`;
  if (metres < 10_000) return `${(metres / 1000).toFixed(1)} km`;
  return `${Math.round(metres / 1000)} km`;
}

/** Matches the backend's GEO_FENCE_METRES — a visit can only be recorded inside this. */
export const GEO_FENCE_METRES = 100;
