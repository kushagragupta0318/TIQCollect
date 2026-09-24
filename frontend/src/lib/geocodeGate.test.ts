import { describe, expect, it } from "vitest";
import { GEOCODE_MIN_INTERVAL_MS, GEOCODE_MOVE_M, shouldGeocode } from "./geocodeGate";

const T0 = 1_750_000_000_000;
const HERE = { lat: 28.4595, lon: 77.0266, at: T0 };
// ~1.1 km north of HERE (0.01° of latitude is ~1,112 m).
const FAR = { lat: 28.4695, lon: 77.0266 };
// ~111 m north.
const NEAR = { lat: 28.4605, lon: 77.0266 };

describe("shouldGeocode", () => {
  it("always looks up with no anchor: first fix, or a refresh", () => {
    expect(shouldGeocode(null, HERE.lat, HERE.lon, T0)).toBe(true);
  });

  it("does not look up for a short move, however long it has been", () => {
    expect(shouldGeocode(HERE, NEAR.lat, NEAR.lon, T0 + 3 * GEOCODE_MIN_INTERVAL_MS)).toBe(false);
  });

  it("does not look up for a long move made too soon", () => {
    expect(shouldGeocode(HERE, FAR.lat, FAR.lon, T0 + GEOCODE_MIN_INTERVAL_MS - 1)).toBe(false);
  });

  it("looks up once the agent has moved far enough and enough time has passed", () => {
    expect(shouldGeocode(HERE, FAR.lat, FAR.lon, T0 + GEOCODE_MIN_INTERVAL_MS)).toBe(true);
  });

  it("bounds a full day on a 50 km beat to about 50 lookups", () => {
    // Drive north in 50 m steps (the reporter's movement trigger) over 9 hours.
    const stepM = 50, beatM = 50_000, shiftMs = 9 * 3_600_000;
    const steps = beatM / stepM;
    let anchor = null as null | { lat: number; lon: number; at: number };
    let lookups = 0;
    for (let i = 0; i <= steps; i++) {
      const lat = HERE.lat + (i * stepM) / 111_195;
      const now = T0 + Math.round((i * shiftMs) / steps);
      if (shouldGeocode(anchor, lat, HERE.lon, now)) {
        lookups++;
        anchor = { lat, lon: HERE.lon, at: now };
      }
    }
    expect(lookups).toBeLessThanOrEqual(beatM / GEOCODE_MOVE_M + 1);
    expect(lookups).toBeGreaterThan(1);
  });
});
