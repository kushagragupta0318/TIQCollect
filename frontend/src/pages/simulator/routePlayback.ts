/**
 * Route playback for the mobile simulator (P0-04): walk a simulated agent
 * along their beat's road geometry so check-in, the live map and the visit
 * geofence are exercised by movement rather than by teleporting.
 *
 * Pure functions only — the page owns the clock.
 */
import { haversineM } from "@/lib/geo";

export type LatLng = [number, number];

/** A two-wheeler in city traffic. ×1 playback moves at this speed. */
export const FIELD_SPEED_MPS = 25_000 / 3600; // 25 km/h ≈ 6.94 m/s

export interface Track {
  points: LatLng[];
  /** cumulative metres at each point; cum[0] = 0 */
  cum: number[];
  total: number;
}

export function buildTrack(points: LatLng[]): Track {
  const clean = points.filter(
    ([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon),
  );
  const cum: number[] = [];
  let total = 0;
  clean.forEach((p, i) => {
    if (i > 0) total += haversineM(clean[i - 1][0], clean[i - 1][1], p[0], p[1]);
    cum.push(total);
  });
  return { points: clean, cum, total };
}

/** Initial bearing from a to b, degrees clockwise from north. */
export function bearing(a: LatLng, b: LatLng): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const φ1 = toRad(a[0]);
  const φ2 = toRad(b[0]);
  const Δλ = toRad(b[1] - a[1]);
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Position `metres` along the track, linearly interpolated inside a segment.
 * Clamped to the ends, so a playback that overshoots parks at the last stop.
 */
export function positionAt(track: Track, metres: number): { lat: number; lon: number; heading: number | null } {
  const { points, cum, total } = track;
  if (points.length === 0) throw new Error("empty track");
  if (points.length === 1) return { lat: points[0][0], lon: points[0][1], heading: null };
  const m = Math.max(0, Math.min(metres, total));
  // Last segment whose start is at or before m (linear scan: beats are a few
  // hundred points; a binary search would not be measurable here).
  let i = 0;
  while (i < cum.length - 2 && cum[i + 1] <= m) i++;
  const a = points[i];
  const b = points[i + 1];
  const seg = cum[i + 1] - cum[i];
  const f = seg > 0 ? (m - cum[i]) / seg : 0;
  return {
    lat: a[0] + (b[0] - a[0]) * f,
    lon: a[1] + (b[1] - a[1]) * f,
    heading: seg > 0 ? bearing(a, b) : null,
  };
}

/**
 * A point `metres` from `target` toward `from` — used by "go to stop" so the
 * simulated agent lands just inside the 100 m visit geofence, as a real agent
 * parked outside the gate would, instead of exactly on the borrower's pin.
 */
export function approach(from: LatLng, target: LatLng, metres: number): LatLng {
  const d = haversineM(from[0], from[1], target[0], target[1]);
  if (d <= metres || d === 0) return target;
  const f = metres / d;
  return [target[0] + (from[0] - target[0]) * f, target[1] + (from[1] - target[1]) * f];
}
