// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-18 — NEW. Pure helpers for the "Promise outcomes by month" card, so
//   the wording and the arithmetic are tested without mounting a chart.
//
//   The kept rate here is the endpoint's — honoured ÷ (honoured + broken) —
//   and is never recomputed on the client; what this module owns is the
//   trend summary ("54% → 28%") and the honesty note about the current
//   month, whose open promises are exactly why its column reads lower.
// ─────────────────────────────────────────────────────────────────────────────

export interface PtpOutcomeMonth {
  month: string;              // YYYY-MM, the month the promises fell DUE
  total: number;
  honored: number;
  partly: number;
  broken: number;
  rescheduled: number;
  open: number;               // still ACTIVE — the current month's, or dated ahead
  promised_amount: number;
  paid_amount: number;
  kept_rate_pct: number | null;
  is_current: boolean;
  is_future: boolean;
}

export interface PtpOutcomes {
  months: PtpOutcomeMonth[];
  window: string[];
  agent_id: string | null;
  effective_month: string;
  definition: string;
}

/** Colour per bucket — the DPD donut's validated palette, reused so the
 *  same outcome reads the same on every card. Open is drawn hatched. */
export const OUTCOME_COLOURS = {
  honored: "#059669",
  partly: "#D97706",
  broken: "#B91C1C",
  rescheduled: "#6D28D9",
  open: "#94A3B8",
} as const;

export const OUTCOME_WORDS: Record<keyof typeof OUTCOME_COLOURS, string> = {
  honored: "Kept",
  partly: "Partly kept",
  broken: "Broken",
  rescheduled: "Rescheduled",
  open: "Still open",
};

/** The months to draw: the endpoint's window plus any later month that
 *  holds promises (next month's open ones), oldest first. */
export function chartMonths(d: PtpOutcomes | null | undefined): PtpOutcomeMonth[] {
  if (!d) return [];
  return [...d.months].sort((a, b) => a.month.localeCompare(b.month));
}

/** First and last month with a decided rate, for the "54% → 28%" summary.
 *  Skips months with no decided promise, and skips the current month when an
 *  earlier decided month exists — an in-progress month is not a trend point. */
export function keptTrend(months: PtpOutcomeMonth[]): { from: PtpOutcomeMonth; to: PtpOutcomeMonth } | null {
  const decided = months.filter((m) => m.kept_rate_pct != null && !m.is_future);
  if (decided.length < 2) return null;
  const closed = decided.filter((m) => !m.is_current);
  const pool = closed.length >= 2 ? closed : decided;
  return { from: pool[0], to: pool[pool.length - 1] };
}

/** The note under the chart: what the current month's open promises mean. */
export function openNote(months: PtpOutcomeMonth[]): string | null {
  const cur = months.find((m) => m.is_current);
  const ahead = months.filter((m) => m.is_future).reduce((s, m) => s + m.open, 0);
  const parts: string[] = [];
  if (cur && cur.open > 0) parts.push(`${cur.open} of this month's promises are still open and not in its rate`);
  if (ahead > 0) parts.push(`${ahead} more fall due next month`);
  return parts.length ? parts.join(" · ") : null;
}

export function monthWords(ym: string): string {
  return new Date(`${ym}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" }).replace(" ", " '");
}
