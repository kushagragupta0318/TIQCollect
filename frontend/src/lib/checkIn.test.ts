import { describe, expect, it } from "vitest";
import { describeFix, fixFromPosition } from "./checkIn";

describe("describeFix", () => {
  it("says it is waiting while there is no fix", () => {
    expect(describeFix(null)).toBe("Waiting for GPS…");
  });

  it("prints the fix the phone returned, with its accuracy", () => {
    expect(describeFix({ lat: 28.63149, lon: 77.21672, accuracy: 11.6 })).toBe("28.63149, 77.21672 (±12 m)");
  });

  it("prints no accuracy rather than an invented one", () => {
    expect(describeFix({ lat: 12.97, lon: 77.59, accuracy: null })).toBe("12.97000, 77.59000");
  });

  it("never names a place: only the coordinates it was given", () => {
    const text = describeFix({ lat: 19.076, lon: 72.8777, accuracy: 5 });
    expect(text).not.toMatch(/[A-Za-z]{3,}/);
  });
});

describe("fixFromPosition", () => {
  it("keeps latitude, longitude and accuracy", () => {
    expect(fixFromPosition({ coords: { latitude: 1, longitude: 2, accuracy: 30 } })).toEqual({ lat: 1, lon: 2, accuracy: 30 });
  });

  it("turns a missing or non-finite accuracy into null", () => {
    expect(fixFromPosition({ coords: { latitude: 1, longitude: 2 } }).accuracy).toBeNull();
    expect(fixFromPosition({ coords: { latitude: 1, longitude: 2, accuracy: Number.NaN } }).accuracy).toBeNull();
  });
});
