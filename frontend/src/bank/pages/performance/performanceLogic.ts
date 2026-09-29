// Pure helpers for the agency scorecard + leaderboard page (D06) — kept out
// of AgencyPerformancePage.tsx so the null-vs-zero and not-clamped rules the
// backend documents (backend/app/services/bank/agency_scorecard.py's module
// docblock) are testable without React, same split as directoryLogic.ts and
// onboardingLogic.ts next door.
import type { PerformanceIndex } from "@/api/bank";

/**
 * A 0-1 fraction as a percentage string. `null` always renders as "—" — it
 * means "not knowable" (an unknown denominator, a row the estimator never
 * saw), never "zero". Never clamps: a fraction over 1 (recovery_vs_expected,
 * workforce_active_ratio, workforce_attrition_ratio) reads as "142%", not
 * "100%" — clamping the NUMBER would hide exactly the mismatch the backend
 * chose not to hide (see agency_scorecard.py's "COHORT CAVEAT" note).
 */
export function formatRatioPercent(value: number | null, digits = 1): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** The Performance Index itself: 0-100 already, whole number, "—" when the
 *  estimator found nothing for this window (never "0"). */
export function formatIndex(value: number | null): string {
  if (value == null) return "—";
  return Math.round(value).toString();
}

/** cost_per_100_inr: null means no cost rate is configured for this bank yet
 *  — "not configured", never "₹0.00". */
export function formatCostPer100(value: number | null): string {
  if (value == null) return "Not configured";
  return `₹${value.toFixed(2)} per ₹100`;
}

/** productivity_per_agent_per_day: a plain count, not a ratio. */
export function formatPerAgentPerDay(value: number | null): string {
  if (value == null) return "—";
  return `${value.toFixed(1)} visits/agent/day`;
}

/** evidence_integrity_per_100_visits: confirmed fraud findings per 100
 *  visits. LOWER is better; 0 is a real, good value and must not collapse
 *  into the same "—" as null (no visits recorded at all). */
export function formatFraudPer100(value: number | null): string {
  if (value == null) return "—";
  return `${value.toFixed(2)} per 100 visits`;
}

/**
 * A percentage's bar-chart fill width, visually capped at 100 even when the
 * underlying value legitimately exceeds it (recovery_vs_expected etc.) — the
 * BAR caps, the printed number (formatRatioPercent) never does. Mirrors
 * bar100Width's own floor/ceiling so this page's bars behave like every
 * other one in the app.
 */
export function percentBarWidth(value: number | null): number {
  if (value == null) return 0;
  return Math.max(0, Math.min(100, value * 100));
}

/** The Performance Index's evidence caption, e.g. "Based on 6 placement-months". */
export function indexEvidenceCaption(n: number): string {
  if (n <= 0) return "No matured evidence yet";
  return `Based on ${n} placement-month${n === 1 ? "" : "s"}`;
}

export interface RankedLeaderboardRow extends PerformanceIndex {
  /** 1-based rank among SCORED agencies; null for an agency the estimator
   *  could not score (index: null) — it is not silently mid-table, and it
   *  gets no rank number rather than a misleading one. */
  rank: number | null;
}

/**
 * Assigns a 1-based rank to every scored row, in the order given (the
 * backend already sorts index descending with unscored last — see
 * agency_scorecard.leaderboard's own docstring — this does not re-sort).
 * An unscored row (index: null) gets rank: null rather than being counted
 * into the sequence, so two unscored agencies never appear to "tie" at a
 * rank number that means something for the scored ones.
 */
export function rankLeaderboard(rows: PerformanceIndex[]): RankedLeaderboardRow[] {
  let rank = 0;
  return rows.map((row) => {
    if (row.index == null) return { ...row, rank: null };
    rank += 1;
    return { ...row, rank };
  });
}
