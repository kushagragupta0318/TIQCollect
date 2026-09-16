/**
 * The overview's case-pipeline grouping, kept apart from the card that draws
 * it so it can be tested without rendering a chart (and so the component file
 * exports only a component, which is what Fast Refresh needs).
 *
 * "Resolved" mirrors models/case.py RESOLVED_STATUSES exactly — PAID, CLOSED,
 * WRITTEN_OFF — the product's one definition of a finished case. ESCALATED is
 * in progress, not resolved, for the reason that file gives: it is open,
 * visitable, and the work a manager most wants surfaced.
 */

/** Mirrors models/case.py RESOLVED_STATUSES. */
const RESOLVED = ["PAID", "CLOSED", "WRITTEN_OFF"] as const;
const IN_PROGRESS = ["PARTIALLY_PAID", "IN_PROGRESS", "PTP_SET", "ESCALATED"] as const;
const NOT_STARTED = ["ASSIGNED"] as const;

export interface PipelineSlice {
  key: "resolved" | "in_progress" | "not_started";
  label: string;
  colour: string;
  statuses: readonly string[];
  count: number;
  /** Per-status counts inside the slice, only those > 0, largest first. */
  parts: Array<{ status: string; count: number }>;
}

export const statusWords = (s: string) =>
  s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

/**
 * Fold the per-status counts into the three pipeline slices. Exported so the
 * grouping can be tested without rendering a chart.
 */
export function pipelineSlices(counts: Record<string, number> | undefined | null): PipelineSlice[] {
  const c = counts ?? {};
  const build = (key: PipelineSlice["key"], label: string, colour: string, statuses: readonly string[]): PipelineSlice => {
    const parts = statuses
      .map((s) => ({ status: s, count: Number(c[s] ?? 0) }))
      .filter((p) => Number.isFinite(p.count) && p.count > 0)
      .sort((a, b) => b.count - a.count);
    return { key, label, colour, statuses, count: parts.reduce((t, p) => t + p.count, 0), parts };
  };
  return [
    build("resolved", "Resolved & closed", "#059669", RESOLVED),
    build("in_progress", "In progress", "#2563EB", IN_PROGRESS),
    build("not_started", "Assigned, not started", "#D97706", NOT_STARTED),
  ];
}
