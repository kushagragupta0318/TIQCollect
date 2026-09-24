import { haversineM } from "@/lib/geo";

/** Where and when the agent's address was last looked up. */
export interface GeocodeAnchor {
  lat: number;
  lon: number;
  at: number;          // epoch ms
}

/**
 * Look the address up again only after the agent has moved this far AND this
 * long has passed. Nominatim's usage policy caps a whole application at one
 * request a second and forbids heavy use. The old rule (every 40 m) made about
 * 1,250 lookups on a 50 km beat; this caps it near 50.
 */
export const GEOCODE_MOVE_M = 1_000;
export const GEOCODE_MIN_INTERVAL_MS = 10 * 60_000;

/** No anchor (the first fix, or the agent tapped refresh) always looks up. */
export function shouldGeocode(anchor: GeocodeAnchor | null, lat: number, lon: number, now: number): boolean {
  if (!anchor) return true;
  return haversineM(anchor.lat, anchor.lon, lat, lon) >= GEOCODE_MOVE_M
    && now - anchor.at >= GEOCODE_MIN_INTERVAL_MS;
}
