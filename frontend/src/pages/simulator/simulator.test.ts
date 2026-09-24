import { describe, expect, it } from "vitest";
import { haversineM } from "@/lib/geo";
import { approach, bearing, buildTrack, positionAt, type LatLng } from "./routePlayback";
import { frameEntry, readSlotAuth } from "./simSession";

// Two points ~1.1 km apart along a meridian in Gurugram.
const A: LatLng = [28.45, 77.07];
const B: LatLng = [28.46, 77.07];
const C: LatLng = [28.46, 77.08];

describe("route playback", () => {
  const track = buildTrack([A, B, C]);

  it("measures the track", () => {
    expect(track.cum[0]).toBe(0);
    expect(track.total).toBeCloseTo(haversineM(...A, ...B) + haversineM(...B, ...C), 3);
  });

  it("starts at the start, ends at the end, and clamps past both", () => {
    expect(positionAt(track, -50)).toMatchObject({ lat: A[0], lon: A[1] });
    const end = positionAt(track, track.total + 1_000);
    expect(end.lat).toBeCloseTo(C[0], 9);
    expect(end.lon).toBeCloseTo(C[1], 9);
  });

  it("interpolates inside a segment and reports the heading", () => {
    const half = positionAt(track, track.cum[1] / 2);
    expect(half.lat).toBeCloseTo((A[0] + B[0]) / 2, 6);
    expect(half.heading).toBeCloseTo(0, 0); // due north
    const onSecond = positionAt(track, track.cum[1] + 10);
    expect(onSecond.heading).toBeCloseTo(bearing(B, C), 6);
    expect(onSecond.heading).toBeGreaterThan(80); // roughly east
  });

  it("drops non-finite points instead of poisoning the distance", () => {
    expect(buildTrack([A, [Number.NaN, 77], B]).points).toHaveLength(2);
  });

  it("lands just inside the geofence when approaching a stop", () => {
    const p = approach(A, B, 20);
    expect(haversineM(p[0], p[1], ...B)).toBeCloseTo(20, 0);
    // Already closer than asked: stay on the target.
    expect(approach(B, B, 20)).toEqual(B);
  });
});

describe("slot sessions", () => {
  const store = (v: Record<string, string>) => ({ getItem: (k: string) => v[k] ?? null });

  it("reads a frame's persisted session", () => {
    const s = store({
      "tiq_auth:agent": JSON.stringify({
        state: { accessToken: "tok", user: { role: "FIELD_AGENT", full_name: "Piyush Sharma" } },
        version: 0,
      }),
    });
    expect(readSlotAuth("agent", s)).toEqual({ accessToken: "tok", role: "FIELD_AGENT", name: "Piyush Sharma" });
    // A different slot is a different session.
    expect(readSlotAuth("manager", s).accessToken).toBeNull();
  });

  it("survives garbage in storage", () => {
    expect(readSlotAuth("agent", store({ "tiq_auth:agent": "{nope" })).accessToken).toBeNull();
  });

  it("sends a logged-out frame to login and a logged-in one home", () => {
    expect(frameEntry("agent", { accessToken: null, role: null, name: null })).toBe("/login?slot=agent");
    expect(frameEntry("agent", { accessToken: "t", role: "FIELD_AGENT", name: "x" })).toBe("/?slot=agent");
  });
});
