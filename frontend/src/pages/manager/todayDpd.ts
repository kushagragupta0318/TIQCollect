/**
 * Today's beat cases by DPD bucket — the grouping behind the overview's
 * "Today's Cases by DPD" donut, kept pure so it can be tested without a chart.
 *
 * Input is GET /manager/dashboard `today_dpd_breakdown`: the cases on the
 * effective day's beats (the same set as cases_today), by the loan's current
 * bucket. Output is in DPD order with a validated colour per bucket.
 */

export interface TodayDpdRow {
  bucket: string;
  case_count: number;
  target_amount: number;
  collectable_amount: number;
}

export interface TodayDpdSlice extends TodayDpdRow {
  label: string;
  shortLabel: string;
  colour: string;
}

/** DPD is an ordered scale; the ring and the legend both follow it. */
export const DPD_ORDER = ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"] as const;

/**
 * One hue per bucket, in DPD order. Validated with the dataviz palette checker
 * on 2026-09-16 — every adjacent pair clears the CVD and normal-vision floors
 * on both light and dark surfaces — with the share printed on every slice so
 * identity never rests on hue alone. (The overview's old bar colours, amber /
 * orange / red, fail the same checker as touching wedges: orange↔red ΔE 10.4
 * under normal vision against a floor of 15.)
 */
export const DPD_COLOUR: Record<string, string> = {
  CURRENT:  "#059669",
  BUCKET_1: "#0284C7",
  BUCKET_2: "#D97706",
  BUCKET_3: "#B91C1C",
  NPA:      "#6D28D9",
};
const FALLBACK_COLOUR = "#64748B";

export const DPD_LABEL: Record<string, string> = {
  CURRENT:  "Current (0 DPD)",
  BUCKET_1: "1–30 DPD",
  BUCKET_2: "31–60 DPD",
  BUCKET_3: "61–90 DPD",
  NPA:      "NPA (90+)",
};
const DPD_SHORT: Record<string, string> = {
  CURRENT:  "current",
  BUCKET_1: "1–30 dpd",
  BUCKET_2: "31–60 dpd",
  BUCKET_3: "61–90 dpd",
  NPA:      "npa 90+",
};

export function todayDpdSlices(rows: readonly TodayDpdRow[] | undefined | null): TodayDpdSlice[] {
  return [...(rows ?? [])]
    .filter((r) => Number.isFinite(r.case_count) && r.case_count > 0)
    .sort((a, b) => {
      const ia = DPD_ORDER.indexOf(a.bucket as typeof DPD_ORDER[number]);
      const ib = DPD_ORDER.indexOf(b.bucket as typeof DPD_ORDER[number]);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    })
    .map((r) => ({
      ...r,
      target_amount: Number(r.target_amount) || 0,
      collectable_amount: Number(r.collectable_amount) || 0,
      label: DPD_LABEL[r.bucket] ?? r.bucket.replace(/_/g, " "),
      shortLabel: DPD_SHORT[r.bucket] ?? r.bucket.replace(/_/g, " ").toLowerCase(),
      colour: DPD_COLOUR[r.bucket] ?? FALLBACK_COLOUR,
    }));
}
