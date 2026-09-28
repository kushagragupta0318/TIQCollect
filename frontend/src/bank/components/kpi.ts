// The KPI payload and the one rule that decides how a KPI's trend reads
// (PulseKpiFlow.jsx:29-38, spec §4.1). Kept apart from the component so the
// rule is testable and so the bank's KPI catalogue (task C01) has one type to
// emit against.

/** One KPI, as the server sends it — every display string pre-formatted (spec §4.12). */
export interface Kpi {
  id: string;
  label: string;
  /** e.g. "₹612.4 Cr", "14.8%". */
  value: string;
  /** Context line under the trend. */
  sub: string;
  /** e.g. "+2.1% MoM", "-0.8 pp vs last month". */
  trend: string;
  /** Which way the number moved. null = no direction. */
  trendUp: boolean | null;
  /** Whether that movement is good news. null = direction is meaningless. */
  good: boolean | null;
  /** How the figure is derived — the card's native title tooltip. */
  basis: string;
  /** What clicking the card drills into (an analytics tab id). */
  drill: string;
  /** Sent by the server and IGNORED by CC's frontend (spec §0.7) — kept for parity. */
  tone?: "critical" | "warning" | "success" | "neutral";
}

export interface KpiRow {
  id: string;
  caption: string;
  /** KPI ids, in display order. */
  kpis: string[];
}

export type TrendDirection = "up" | "down" | "flat";
export type TrendTone = "success" | "destructive" | "muted";

export interface TrendDisplay {
  direction: TrendDirection;
  tone: TrendTone;
  /** The class CC puts on the trend line. */
  className: "text-success" | "text-destructive" | "text-muted-foreground";
}

// A delta of exactly zero in points or percent reads as flat (":36").
const ZERO_DELTA = /^[+-]?0(\.0)?\s*(pp|%)/;

/**
 * Two independent signals, and they must not be conflated: the arrow shows
 * which way the number moved, the colour shows whether that is good news.
 * Rising NPA gets an up arrow in red. `good == null` (a target does not
 * "improve") and a zero delta both read as flat.
 */
export function trendDisplay(kpi: Pick<Kpi, "good" | "trendUp" | "trend">): TrendDisplay {
  const flat = kpi.good == null || ZERO_DELTA.test(kpi.trend || "");
  if (flat) return { direction: "flat", tone: "muted", className: "text-muted-foreground" };
  const direction: TrendDirection = kpi.trendUp ? "up" : "down";
  return kpi.good
    ? { direction, tone: "success", className: "text-success" }
    : { direction, tone: "destructive", className: "text-destructive" };
}
