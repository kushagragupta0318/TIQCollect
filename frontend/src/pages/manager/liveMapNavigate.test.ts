/**
 * The Navigate button on the Live Map goes to WHEREVER THE MARKER IS, and
 * says how old that position is.
 */
import { describe, expect, it } from "vitest";
import { STALE_AFTER_S } from "./liveMapConstants";
import { directionsUrl, navigateAction } from "./liveMapNavigate";

const words = (s: number | null) => (s == null ? "no fix yet" : `${s}s`);

describe("directionsUrl", () => {
  it("is the same free Google Maps directions scheme the agent app uses", () => {
    expect(directionsUrl(28.4595, 77.0266))
      .toBe("https://www.google.com/maps/dir/?api=1&destination=28.4595,77.0266&travelmode=driving");
  });

  it("carries NO origin, so Google Maps uses the device's own location and the page never asks for geolocation", () => {
    expect(directionsUrl(1, 2)).not.toContain("origin=");
  });
});

describe("navigateAction", () => {
  const at = (lat: number | null, lng: number | null, age: number | null) =>
    ({ latitude: lat, longitude: lng, age_seconds: age });

  it("points at the marker's coordinates, whatever they are", () => {
    const nav = navigateAction(at(28.4595, 77.0266, 120), words)!;
    expect(nav.url).toContain("destination=28.4595,77.0266");
    expect(nav.label).toBe("Navigate");
  });

  it("is null when there is no position — nothing to navigate to", () => {
    expect(navigateAction(at(null, null, 10), words)).toBeNull();
    expect(navigateAction(at(28.4, null, 10), words)).toBeNull();
    expect(navigateAction(at(NaN, 77, 10), words)).toBeNull();
  });

  it("says 'agent' for a fresh fix and 'last known position' for a stale one, on the shared threshold", () => {
    const fresh = navigateAction(at(28.4, 77.0, STALE_AFTER_S), words)!;
    const stale = navigateAction(at(28.4, 77.0, STALE_AFTER_S + 1), words)!;
    expect(fresh.stale).toBe(false);
    expect(fresh.note).toBe(`to agent · ${STALE_AFTER_S}s`);
    expect(stale.stale).toBe(true);
    expect(stale.note).toBe(`to last known position · ${STALE_AFTER_S + 1}s`);
  });

  it("is never withheld for staleness — a 14-day-old fix still navigates", () => {
    const nav = navigateAction(at(28.4, 77.0, 14 * 86400), words)!;
    expect(nav).not.toBeNull();
    expect(nav.stale).toBe(true);
    expect(nav.url).toContain("destination=28.4,77");
  });

  it("treats an unknown age as stale rather than fresh", () => {
    expect(navigateAction(at(28.4, 77.0, null), words)!.stale).toBe(true);
  });
});
