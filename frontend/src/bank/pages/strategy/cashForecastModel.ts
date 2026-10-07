// Pure display rules for the Cash Forecast page (E06). No React, no axios —
// mirrors simulatorModel.ts's split so the figures-and-words logic is
// testable without mounting a chart.
import type { CashForecastRun, CashForecastWeek } from "@/api/bankStrategy";
import { fmtINR } from "../../theme/format";

export interface ChartRow {
  week: string;        // short label for the x-axis, e.g. "13 Oct"
  p10: number;
  /** p90 - p10: recharts draws the band as a stacked area on top of p10. */
  bandWidth: number;
  p50: number;
  bottom_up: number;
  top_down: number;
}

const SHORT_DATE = (iso: string): string => {
  const d = new Date(iso + "T00:00:00");
  return `${d.getDate()} ${d.toLocaleDateString("en-GB", { month: "short" })}`;
};

export function chartRows(weeks: CashForecastWeek[]): ChartRow[] {
  return weeks.map((w) => ({
    week: SHORT_DATE(w.week_start),
    p10: w.p10,
    bandWidth: Math.max(w.p90 - w.p10, 0),
    p50: w.p50,
    bottom_up: w.bottom_up,
    top_down: w.top_down,
  }));
}

export interface Tile {
  key: string;
  label: string;
  value: string;
  sub: string;
}

/** The figures a bank reader looks at first. No figure here is invented:
 *  every one is read straight off the run. */
export function headlineTiles(run: CashForecastRun): Tile[] {
  return [
    { key: "p50", label: "13-week collections (p50)", value: fmtINR(run.totals.p50),
      sub: `${fmtINR(run.totals.p10)} to ${fmtINR(run.totals.p90)} (p10–p90)` },
    { key: "week1", label: "Next week (p50)", value: fmtINR(run.weeks[0]?.p50 ?? 0),
      sub: `${fmtINR(run.weeks[0]?.p10 ?? 0)} to ${fmtINR(run.weeks[0]?.p90 ?? 0)}` },
    { key: "honor", label: "PTP honor rate", value: honorRateLabel(run),
      sub: `${run.ptp_resolved_count} resolved PTP(s) in the lookback window` },
    { key: "recovery", label: "recovery_risk-informed (next cycle)", value: fmtINR(run.recovery_informed_total),
      sub: `${run.recovery_informed_loans} loan(s) with no active PTP` },
  ];
}

export function honorRateLabel(run: CashForecastRun): string {
  return run.ptp_honor_rate === null ? "No PTP history yet" : `${(run.ptp_honor_rate * 100).toFixed(0)}%`;
}

/** Read off the run, never guessed: whether and how many trailing weeks were
 *  treated as not-yet-reported rather than as a real drop to zero. Empty
 *  when there is no lag — this is furniture only when it applies. */
export function reportingLagNote(run: CashForecastRun): string | null {
  if (!run.reporting_lag_weeks) return null;
  const weeks = run.reporting_lag_weeks === 1 ? "1 week" : `${run.reporting_lag_weeks} weeks`;
  return `The most recent ${weeks} of this book carried no VERIFIED payment yet. Treated as not-yet-reported ` +
    `(an ingest lag), not as collections dropping to zero, and excluded from the fit.`;
}

/** The forecast-vs-actual tracker's own words — never a second copy of the
 *  calibrated/MAPE wording elsewhere, since BAND_NOTE-style duplication is
 *  exactly what strategy/honesty.py exists to prevent for the headline
 *  caveat. This caption is about the BACKTEST specifically, which the stamp
 *  does not narrate in words, only in its `calibrated` flag. */
export function backtestCaption(run: CashForecastRun): string {
  const bt = run.backtest;
  if (bt.n_folds === 0) return `Not enough history for a backtest fold yet (${bt.reason}).`;
  const mape = bt.mape === null ? "undefined" : `${(bt.mape * 100).toFixed(0)}%`;
  return `${bt.n_folds} rolling-origin fold(s) against this book's own history, mean absolute ` +
    `percentage error ${mape}${bt.calibrated ? "" : ` — ${bt.reason}`}.`;
}
