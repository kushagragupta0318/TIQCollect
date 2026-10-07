// Pure math behind PercentileRangeChart (charts.tsx) — no React, so it can
// be tested directly against jsdom's recharts/SVG limits, and kept apart so
// charts.tsx stays a components-only file (react-refresh/only-export-components:
// a plain function export there breaks Fast Refresh boundary detection).
export interface PercentileRow {
  name: string;
  p5: number;
  p10: number;
  p50: number;
  p90: number;
  p95: number;
}

export function percentileSegments(r: PercentileRow) {
  const span = Math.max(r.p95 - r.p5, 0);
  // The marker tick: visible but never wide enough to eat the band it sits
  // in. A degenerate band (p95 === p5, e.g. a metric with no simulated
  // spread) collapses every segment to 0 — an honest "nothing to show",
  // not a divide-by-zero.
  const eps = Math.min(span * 0.015, (r.p90 - r.p10) / 4);
  const preMarker = Math.max(r.p50 - eps, r.p10);
  const postMarker = Math.max(r.p90 - preMarker - 2 * eps, 0);
  return {
    base: r.p5,
    lowOuter: Math.max(r.p10 - r.p5, 0),
    preMarker: Math.max(preMarker - r.p10, 0),
    marker: Math.max(Math.min(2 * eps, r.p90 - preMarker), 0),
    postMarker,
    highOuter: Math.max(r.p95 - r.p90, 0),
  };
}

/** Every percentile field, not just p5/p95: a real run isn't guaranteed to
 *  have p95 as a row's true max (or p5 as its true min) — found live, not
 *  assumed, after a GNPA chart rendered as a blank band because its domain
 *  was computed from p95 alone while p95 came back below p50 for that run.
 *  percentileSegments' stacked widths already clamp to >= 0 regardless of
 *  input ordering; this is the other half of that same defensiveness —
 *  span the actual extremes of what's drawn, whichever field they turn out
 *  to be. */
export function percentileDomain(rows: PercentileRow[], domainFrom0: boolean): [number, number] {
  const allValues = rows.flatMap((r) => [r.p5, r.p10, r.p50, r.p90, r.p95]);
  const trueMax = Math.max(...allValues);
  const trueMin = Math.min(...allValues);
  return domainFrom0 ? [0, trueMax * 1.05] : [trueMin * 0.95, trueMax * 1.05];
}
