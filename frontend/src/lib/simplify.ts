/** Points within this many metres of the simplified line are dropped: under a pixel at zoom 15. */
export const SIMPLIFY_TOLERANCE_M = 3;

/**
 * Douglas-Peucker over [lat, lon] points, keeping both ends. Run once when a line loads, so a
 * day of GPS or a long road route stops costing a projection per point on every zoom and pan.
 */
export function simplifyPath<T extends readonly [number, number]>(
  points: readonly T[],
  toleranceM = SIMPLIFY_TOLERANCE_M,
): T[] {
  const n = points.length;
  if (n < 3 || toleranceM <= 0) return points.slice();

  // Local equirectangular metres: exact enough across a city, and cheap.
  const kx = 111_320 * Math.cos((points[0][0] * Math.PI) / 180);
  const ky = 110_540;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    xs[i] = points[i][1] * kx;
    ys[i] = points[i][0] * ky;
  }

  const keep = new Uint8Array(n);
  keep[0] = 1;
  keep[n - 1] = 1;
  const tol2 = toleranceM * toleranceM;
  // An explicit stack: a recursive version overflows on a long, wiggly GPS day.
  const stack: number[] = [0, n - 1];
  while (stack.length) {
    const last = stack.pop() as number;
    const first = stack.pop() as number;
    let worst = -1;
    let worstD2 = tol2;
    for (let i = first + 1; i < last; i++) {
      const d2 = segmentDistance2(xs[i], ys[i], xs[first], ys[first], xs[last], ys[last]);
      if (d2 > worstD2) {
        worstD2 = d2;
        worst = i;
      }
    }
    if (worst !== -1) {
      keep[worst] = 1;
      stack.push(first, worst, worst, last);
    }
  }
  return points.filter((_, i) => keep[i] === 1);
}

function segmentDistance2(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  const ex = ax + t * dx - px;
  const ey = ay + t * dy - py;
  return ex * ex + ey * ey;
}
