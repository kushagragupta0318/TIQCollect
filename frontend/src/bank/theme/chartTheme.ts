/* Ported verbatim from Command Center `src/lib/chartTheme.js` (task UI02, spec
   §4.11). Only the TypeScript annotations are new; every value is CC's. The
   three local tooltip variants CC's signature surfaces actually render are
   added at the bottom, named, so a port picks the one its source used instead
   of re-deriving it.

   One chart theme for the whole platform.

   Recharts cannot read the HSL custom properties in bank.css, so every page
   pasted its own axis, grid and tooltip styling. They drifted: three different
   muted greys for tick labels, two tooltip fonts, the old #1677ff brand blue
   still hardcoded in half the series, and — the visible bug — a #334155 grid
   stroke, a dark slate line drawn across a white card in every chart that used
   the shared Charts.jsx components.

   Import from here instead of redefining locally. Colours still come from
   theme/colors.ts; this module only fixes how they are applied. */

import type { CSSProperties } from "react";
import { BRAND, CHART_SERIES, DPD_COLORS } from "./colors";

export const FONT =
  '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

// Axis tick labels. Small, muted, never competing with the data.
export const axisTick = {
  fontSize: 10,
  fill: BRAND.muted,
  fontWeight: 500,
  fontFamily: FONT,
};

// Grid: horizontal only by default, hairline, in the border tint — not slate.
export const gridProps = {
  strokeDasharray: "3 3",
  stroke: BRAND.grid,
  vertical: false,
};

export const axisProps = {
  tick: axisTick,
  axisLine: false,
  tickLine: false,
};

export const tooltipStyle: CSSProperties = {
  fontSize: "11px",
  borderRadius: "10px",
  border: "none",
  backgroundColor: BRAND.ink,
  color: "#F9FAFB",
  boxShadow: "0 8px 24px rgba(16,24,40,0.18)",
  padding: "8px 12px",
  fontFamily: FONT,
};

export const tooltipCursor = { stroke: BRAND.grid, strokeWidth: 1 };
export const tooltipBarCursor = { fill: BRAND.grid, opacity: 0.45 };

export const legendStyle: CSSProperties = { fontSize: 10, fontWeight: 600, paddingTop: 8, fontFamily: FONT };

// Left margin is negative on purpose: Recharts reserves width for a Y axis label
// that none of these charts use, and the gap reads as broken padding.
export const chartMargin = { top: 8, right: 8, left: -18, bottom: 0 };

export { BRAND, CHART_SERIES, DPD_COLORS };

// Series colour by index, for charts with no domain meaning to their series.
export const seriesColor = (i: number): string => CHART_SERIES[i % CHART_SERIES.length];

// Value-based colouring used by efficiency/coverage style metrics, so "good"
// looks the same on every page.
export const gradeColor = (pct: number, { good = 40, fair = 15 }: { good?: number; fair?: number } = {}): string =>
  pct >= good ? BRAND.success : pct >= fair ? BRAND.warning : BRAND.destructive;

/* ── The three local variants the signature surfaces render (spec §4.11) ── */

// PortfolioAnalytics.jsx:33-38 — dark tooltip, 8px radius, no font set.
export const analyticsTooltipStyle: CSSProperties = {
  fontSize: "11px",
  borderRadius: "8px",
  border: "1px solid #111827",
  backgroundColor: "#111827",
  color: "#F9FAFB",
  boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
  padding: "8px 12px",
};
// PortfolioAnalytics.jsx:39 — its tick (chartTheme's minus the font family).
export const analyticsTick = { fontSize: 10, fill: BRAND.muted, fontWeight: 500 };
// PortfolioAnalytics legends: `iconSize={8} iconType="circle" wrapperStyle={…}`.
export const analyticsLegendStyle: CSSProperties = { fontSize: 10, paddingTop: 6 };

// RiskSimulator.jsx:174-175 — the LIGHT tooltip, with visible axis lines.
export const simulatorTooltipStyle: CSSProperties = {
  borderRadius: "12px",
  fontSize: "12px",
  border: `1px solid ${BRAND.grid}`,
  boxShadow: "0 8px 24px rgba(0,0,0,.08)",
};
export const simulatorAxisTick = { fill: BRAND.axis, fontSize: 11 };

// FieldRecovery.jsx:62-71 — the field island's dark tooltip.
export const fieldTooltipStyle: CSSProperties = {
  backgroundColor: "#111827",
  borderRadius: "8px",
  padding: "7px 11px",
  boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
};
