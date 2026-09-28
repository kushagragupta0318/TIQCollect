// Ported verbatim from Command Center `src/lib/colors.js` (task UI02, spec §1.3–1.5).
// Only the TypeScript annotations are new; every value is CC's.

// Single source of truth for chart/SVG colors. Recharts and inline SVG can't
// read the HSL CSS variables in bank.css, so hex values were pasted all over
// the app and drifted (#1677ff vs #1677FF, three different greens, etc.). Import
// from here instead of hardcoding, so every chart matches the design tokens.

export const BRAND = {
  primary: "#4F46E5",
  secondary: "#7A5AF8",
  success: "#12B76A",
  warning: "#F79009",
  destructive: "#F04438",
  slate: "#667085",
  ink: "#101828",
  muted: "#98A2B3",
  grid: "#ECEDF1",
  axis: "#98A2B3",
} as const;

export type BrandColor = keyof typeof BRAND;

// DPD lifecycle bucket colors — used by every DPD chart so buckets read the
// same everywhere (blue → amber → orange → red → dark).
export const DPD_COLORS: Record<string, string> = {
  Current: "#16A34A",
  "0-30": "#1677FF",
  "1-30": "#1677FF",
  "31-60": "#F59E0B",
  "30-60": "#F59E0B",
  "61-90": "#F97316",
  "60-90": "#F97316",
  "90-180": "#EF4444",
  "180+": "#B91C1C",
  NPA: "#1F2937",
};

// Ordered categorical palette for multi-series charts.
export const CHART_SERIES: readonly string[] = [
  "#2563EB",
  "#F59E0B",
  "#06B6D4",
  "#DB2777",
  "#059669",
  "#7C3AED",
  "#475569",
];

// Categorical pie/donut ramp: light enough for the soft-card surfaces while
// keeping adjacent slices visibly different in hue and luminance.
export const PIE_COLORS: readonly string[] = [
  "#2563EB",
  "#F59E0B",
  "#06B6D4",
  "#DB2777",
  "#059669",
  "#7C3AED",
  "#475569",
];

// Risk-tier palette (matches RiskScoringEngine tiers).
export const RISK_COLORS: Record<string, string> = {
  Low: "#53B1FD",
  "Low Risk": "#53B1FD",
  Medium: "#8098F9",
  "Medium Risk": "#8098F9",
  High: "#FEC84B",
  "High Risk": "#FEC84B",
  Critical: "#F97066",
};

export const RISK_SOFT_COLORS: Record<string, string> = {
  Low: "#EFF8FF",
  "Low Risk": "#EFF8FF",
  Medium: "#F0F3FF",
  "Medium Risk": "#F0F3FF",
  High: "#FFFAEB",
  "High Risk": "#FFFAEB",
  Critical: "#FFF1F0",
};
