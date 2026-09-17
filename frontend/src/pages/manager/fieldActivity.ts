/**
 * Field Activity — the overview funnel's pure half: stage definitions, the
 * words for each outcome, the Cases-page links, and the drop-off arithmetic
 * the card prints. Kept apart from the card so it can be tested without a
 * DOM. The SEMANTICS (what counts as visited / met / paid or promised, and
 * which visits count) live server-side in services/field_activity_service.py
 * and arrive computed; nothing here re-derives a stage from case state.
 *
 * TODAY MEANS TODAY'S VISITS ONLY — the card only ever shows what the server
 * counted for the selected window, and every link it builds carries that
 * same window, so the rows behind a number are the number.
 */

export type ActivityWindow = "today" | "7d" | "30d";
export const WINDOWS: readonly ActivityWindow[] = ["today", "7d", "30d"];
export const DEFAULT_WINDOW: ActivityWindow = "today";

export type Stage = "planned" | "visited" | "met" | "paid_or_promised";
export type ReasonStage = "not_met" | "met_no_money";

export interface FieldActivity {
  window: ActivityWindow;
  window_start: string;   // yyyy-mm-dd
  window_end: string;     // yyyy-mm-dd — the dashboard's effective date
  visits_from: string;    // ISO datetime, the exact bound the server used
  visits_to: string;
  planned: number;
  visited: number;
  met: number;
  paid_or_promised: number;
  not_met: number;
  met_no_money: number;
  drop_offs: {
    planned_to_visited: number | null;
    visited_to_met: number | null;
    met_to_paid_or_promised: number | null;
  };
  not_met_reasons: Record<string, number>;
  met_no_money_reasons: Record<string, number>;
  definitions: { not_met_outcomes: string[]; paid_or_promised_outcomes: string[] };
}

export const WINDOW_LABEL: Record<ActivityWindow, string> = {
  today: "Today",
  "7d": "7d",
  "30d": "30d",
};
export const WINDOW_WORDS: Record<ActivityWindow, string> = {
  today: "today",
  "7d": "last 7 days",
  "30d": "last 30 days",
};

/** What each stage means, in the words the tooltip shows. */
export const STAGE_DEFS: ReadonlyArray<{ key: Stage; label: string; hint: string }> = [
  { key: "planned", label: "Planned",
    hint: "Distinct cases on your agents' routes for the window." },
  { key: "visited", label: "Visited",
    hint: "Planned cases with at least one visit recorded inside the window. A visit before the window does not count." },
  { key: "met", label: "Met",
    hint: "Visited cases where the agent reached someone — any outcome except Not available or Address issue. A revisit counts as met." },
  { key: "paid_or_promised", label: "Paid or promised",
    hint: "Met cases with a payment or a promise to pay: paid in full, part paid, part paid + promise, or promise." },
];

/** Visit outcomes as a manager says them. */
export const OUTCOME_WORDS: Record<string, string> = {
  NOT_AVAILABLE: "Not available",
  ADDRESS_ISSUE: "Address issue",
  REVISIT: "Revisit needed",
  RTP: "Refused to pay",
  DISPUTE: "Dispute",
  BROKEN_PTP: "Broken promise",
  DECEASED: "Deceased",
  PTP: "Promise to pay",
  PAID_FULL: "Paid in full",
  PART_PAID: "Part paid",
  PART_PAID_PTP: "Part paid + promise",
};
export const outcomeWords = (o: string) =>
  OUTCOME_WORDS[o] ?? o.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

/** The four stage counts in order, so a bar can be sized against the first. */
export function stageCounts(d: Pick<FieldActivity, Stage>): Array<{ key: Stage; count: number }> {
  return STAGE_DEFS.map((s) => ({ key: s.key, count: d[s.key] }));
}

/**
 * Bar width for a stage as a share of PLANNED (the funnel's mouth). A stage
 * of 0 still draws a 2px hairline so the axis reads; that is the card's job.
 */
export function shareOfPlanned(count: number, planned: number): number {
  if (!(planned > 0) || !(count > 0)) return 0;
  return Math.min(1, count / planned);
}

/** "−30%" between two stages, or null when the preceding stage is zero. */
export function dropOffLabel(pct: number | null | undefined): string | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  return `−${Math.round(pct)}%`;
}

/**
 * The Cases-page URL for a stage or a reason, carrying the SAME window the
 * funnel was computed for. Never a status or a date filter on the case — the
 * server resolves `activity` through the same service that counted it.
 */
export function casesLink(window: ActivityWindow, stage: Stage | ReasonStage, outcome?: string): string {
  const p = new URLSearchParams({ activity: stage, activity_window: window });
  if (outcome) p.set("visit_outcome", outcome);
  return `/manager/cases?${p.toString()}`;
}

/** Reasons as ordered rows, largest first, each with its link. */
export function reasonRows(
  window: ActivityWindow,
  stage: ReasonStage,
  reasons: Record<string, number> | undefined | null,
): Array<{ outcome: string; label: string; count: number; href: string }> {
  return Object.entries(reasons ?? {})
    .filter(([, n]) => Number.isFinite(n) && n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([outcome, count]) => ({ outcome, label: outcomeWords(outcome), count, href: casesLink(window, stage, outcome) }));
}

/** The empty-state sentence for a window with no visits, or null. */
export function emptyVisitsMessage(d: Pick<FieldActivity, "window" | "visited" | "planned">): string | null {
  if (d.visited > 0) return null;
  if (d.planned === 0) return d.window === "today" ? "No routes planned for today." : `No routes planned in the ${WINDOW_WORDS[d.window]}.`;
  return d.window === "today" ? "No visits recorded yet today." : `No visits recorded in the ${WINDOW_WORDS[d.window]}.`;
}
