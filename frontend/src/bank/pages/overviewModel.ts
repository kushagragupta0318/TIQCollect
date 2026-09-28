// The Overview's data, as GET /api/v1/bank/overview returns it (backend
// app/api/v1/endpoints/bank.py), and the few pure decisions the page makes
// about it. Kept apart from the page so they are testable without mounting it.
import type { Kpi, KpiRow } from "../components/kpi";
import { pyDate } from "../theme/format";

export interface OverviewKpi extends Kpi {
  /** false: the figure cannot be computed yet; `reason` says why and `value` is "—". */
  available: boolean;
  reason: string | null;
}

export interface OverviewResponse {
  bank_name: string;
  /** ISO date of the reading, or null when the bank has no reading yet. */
  as_of: string | null;
  kpis: OverviewKpi[];
  rows: KpiRow[];
  totals: { label: string; value: string; basis: string }[];
  narrative: { sentences: string[]; generated_by: string };
  notes: string[];
}

/** "Reading of 22 Sep 2026", or an honest "no reading yet". */
export function frameLabel(asOf: string | null): string {
  if (!asOf) return "No reading yet";
  const [y, m, d] = asOf.split("-").map(Number);
  return `Reading of ${pyDate(new Date(y, m - 1, d))}`;
}

/** How many of the cards carry a real figure. The live dot is earned only if all do. */
export function coverage(kpis: OverviewKpi[]): { available: number; total: number; complete: boolean } {
  const available = kpis.filter((k) => k.available).length;
  return { available, total: kpis.length, complete: kpis.length > 0 && available === kpis.length };
}

/** The narrative's caption: it must never read as AI unless a model wrote it. */
export function narrativeCaption(generatedBy: string): string {
  return generatedBy === "rules"
    ? "Summary · written by fixed rules from the figures above, not by AI"
    : `Summary · ${generatedBy}`;
}
