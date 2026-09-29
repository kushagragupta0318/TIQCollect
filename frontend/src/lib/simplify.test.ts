import { describe, expect, it } from "vitest";
import { SIMPLIFY_TOLERANCE_M, simplifyPath } from "./simplify";

type P = [number, number];

// Points given in metres north / east of a Gurugram base.
const BASE: P = [28.4595, 77.0266];
const north = (m: number, east = 0): P => [BASE[0] + m / 110_540, BASE[1] + east / (111_320 * Math.cos((BASE[0] * Math.PI) / 180))];

/** Metres from p to the nearest segment of path, in the same flat projection. */
function distanceToPathM(p: P, path: P[]): number {
  const kx = 111_320 * Math.cos((BASE[0] * Math.PI) / 180);
  const xy = (q: P) => [q[1] * kx, q[0] * 110_540];
  const [px, py] = xy(p);
  let best = Infinity;
  for (let i = 1; i < path.length; i++) {
    const [ax, ay] = xy(path[i - 1]);
    const [bx, by] = xy(path[i]);
    const dx = bx - ax, dy = by - ay;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
    best = Math.min(best, Math.hypot(ax + t * dx - px, ay + t * dy - py));
  }
  return best;
}

describe("simplifyPath", () => {
  it("returns short lines unchanged, as a copy", () => {
    const two: P[] = [north(0), north(100)];
    const out = simplifyPath(two);
    expect(out).toEqual(two);
    expect(out).not.toBe(two);
    expect(simplifyPath([])).toEqual([]);
  });

  it("drops points that lie on the line and keeps both ends", () => {
    const line = Array.from({ length: 11 }, (_, i) => north(i * 100));
    expect(simplifyPath(line)).toEqual([line[0], line[10]]);
  });

  it("keeps a bend wider than the tolerance and drops one narrower", () => {
    const wide: P[] = [north(0), north(500, 10), north(1000)];
    const narrow: P[] = [north(0), north(500, 1), north(1000)];
    expect(simplifyPath(wide)).toEqual(wide);
    expect(simplifyPath(narrow)).toEqual([narrow[0], narrow[2]]);
    expect(simplifyPath(wide, 20)).toEqual([wide[0], wide[2]]);
  });

  it("keeps the corners of a closed loop whose ends coincide", () => {
    const square: P[] = [north(0), north(0, 200), north(200, 200), north(200), north(0)];
    expect(simplifyPath(square)).toEqual(square);
  });

  it("reduces a jittery GPS day to a handful of points, every fix within tolerance of the result", () => {
    // 2 km due north, one fix every metre, with up to 1 m of sideways jitter.
    let seed = 7;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
    const track = Array.from({ length: 2001 }, (_, i) => north(i, rand()));
    const out = simplifyPath(track);
    expect(out.length).toBeLessThan(20);
    expect(out[0]).toBe(track[0]);
    expect(out[out.length - 1]).toBe(track[2000]);
    for (const p of track) expect(distanceToPathM(p, out)).toBeLessThanOrEqual(SIMPLIFY_TOLERANCE_M + 0.01);
  });

  it("returns a subset of the input, in order", () => {
    const zig = Array.from({ length: 50 }, (_, i) => north(i * 20, i % 2 ? 8 : -8));
    const out = simplifyPath(zig);
    let last = -1;
    for (const p of out) {
      const idx = zig.indexOf(p);
      expect(idx).toBeGreaterThan(last);
      last = idx;
    }
  });
});
