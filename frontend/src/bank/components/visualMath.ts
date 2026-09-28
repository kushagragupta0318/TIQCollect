// The arithmetic inside CC's signature composites, lifted out of the JSX so it
// can be tested and so every port computes a width or a tint the same way.
// Each function cites the CC line it reproduces.
import { BRAND } from "../theme/colors";

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Two-digit hex alpha for an 8-bit value, as CC builds `${hex}${aa}`. */
export const hexByte = (value: number): string => Math.round(clamp(value, 0, 255)).toString(16).padStart(2, "0");

/** Bar100 fill width (PortfolioAnalytics.jsx:77-82): at least 1.5% so a sliver shows. */
export const bar100Width = (pct: number): number => Math.min(100, Math.max(1.5, pct));

/** DrillPanel / SplitList bar (DrillPanel.jsx:18-25): at least 2%. */
export const splitBarWidth = (pct: number): number => Math.min(100, Math.max(2, pct));

/** Funnel stage width (PortfolioAnalytics.jsx:108-133): at least 12% so the label fits. */
export const funnelWidth = (value: number, max: number): number => Math.max(max > 0 ? (value / max) * 100 : 0, 12);

/** Funnel colours by stage index: slate, primary, warning, destructive (then destructive). */
export const funnelColor = (i: number): string =>
  [BRAND.slate, BRAND.primary, BRAND.warning, BRAND.destructive][Math.min(i, 3)];

/**
 * Heat-grid cell tint (PortfolioAnalytics.jsx:208): `${color}${hex(round(intensity*40+8))}`
 * — an alpha of 0x08 at zero up to 0x30 at the row maximum.
 */
export const heatCellBackground = (color: string, intensity: number): string =>
  `${color}${hexByte(clamp(intensity, 0, 1) * 40 + 8)}`;

export interface MatrixCellStyle {
  background: string;
  boxShadow: string;
  textColor: string;
}

/**
 * Transition-matrix cell (PortfolioAnalytics.jsx:275-322). Above the diagonal
 * (rolling to a worse bucket) is destructive, below it success, on it slate;
 * alpha is round(min(0.85, v/100*0.9)*255); the diagonal is outlined; text
 * turns white past 45.
 */
export function transitionCellStyle(value: number, row: number, col: number): MatrixCellStyle {
  const base = col > row ? BRAND.destructive : col < row ? BRAND.success : BRAND.slate;
  const alpha = hexByte(Math.min(0.85, (value / 100) * 0.9) * 255);
  return {
    background: `${base}${alpha}`,
    boxShadow: row === col ? `inset 0 0 0 1.5px ${BRAND.ink}55` : "none",
    textColor: value > 45 ? "#fff" : BRAND.ink,
  };
}

/** A DPD bucket chip's fill: the bucket colour at 0x15 (≈8%) alpha (spec §1.4). */
export const chipBackground = (color: string): string => `${color}15`;
