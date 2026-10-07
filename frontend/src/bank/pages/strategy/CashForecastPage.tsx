// AI Strategy › Cash Forecast (plan §7, task E06). A 13-week fan chart of
// weekly collection inflow, reconciled from the book's own payment history
// (top-down ETS) and its known commitments (bottom-up: PTP schedule x honor
// rate, plus a recovery_risk-informed term for loans with no active PTP).
//
// Two things this page refuses to do, same discipline as MonteCarloPage:
//   · call the figure more certain than it is. The caveat is permanent
//     furniture here, printed from the response's own stamp — this file
//     writes no caption of its own.
//   · narrate the backtest in its own words beyond what the run measured;
//     backtestCaption() in cashForecastModel.ts reads straight off
//     run.backtest, nothing is invented here.
import { Info, TrendingUp, TriangleAlert } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getCashForecast, type CashForecastRun } from "@/api/bankStrategy";
import { errorCode, errorDetail } from "@/lib/apiError";
import { AnalyticsLoading, Panel, Tile } from "../../components/analytics";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";
import { BRAND, chartMargin, gridProps } from "../../theme/chartTheme";
import { fmtINR } from "../../theme/format";
import { backtestCaption, chartRows, headlineTiles, honorRateLabel } from "./cashForecastModel";

/** The run's own caveat, rendered verbatim — identical framing to
 *  MonteCarloPage's HonestyNote, so the two tools read as one product. */
function HonestyNote({ run }: { run: CashForecastRun }) {
  return (
    <div
      role="note"
      className="flex gap-3 rounded-card border border-[#F79009]/40 bg-[#FDF0DC] px-5 py-4 text-[12.5px] leading-relaxed text-[#7A3E05]"
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-[#B54708]" aria-hidden="true" />
      <p>{run.text}</p>
    </div>
  );
}

function FanChart({ run }: { run: CashForecastRun }) {
  const rows = chartRows(run.weeks);
  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart data={rows} margin={chartMargin}>
        <CartesianGrid {...gridProps} />
        <XAxis dataKey="week" tick={{ fontSize: 10, fill: BRAND.muted }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 10, fill: BRAND.muted }} axisLine={false} tickLine={false}
              tickFormatter={(v: number) => fmtINR(v)} width={64} />
        <Tooltip
          contentStyle={{ fontSize: "11px", borderRadius: "10px", border: `1px solid ${BRAND.grid}` }}
          formatter={(value, name) => [fmtINR(Number(value) || 0), String(name)]}
          labelFormatter={(label) => `Week of ${String(label)}`}
        />
        {/* The band: a transparent area up to p10, then the p10-p90 width
            stacked on top of it, so the visible fill sits exactly between
            the two percentiles. */}
        <Area type="monotone" dataKey="p10" stackId="band" stroke="none" fill="transparent"
             isAnimationActive={false} name="p10" legendType="none" />
        <Area type="monotone" dataKey="bandWidth" stackId="band" stroke="none"
             fill={BRAND.primary} fillOpacity={0.16} isAnimationActive={false}
             name="p10–p90" legendType="none" />
        <Line type="monotone" dataKey="p50" stroke={BRAND.primary} strokeWidth={2.5} dot={false} name="p50" />
        <Line type="monotone" dataKey="top_down" stroke={BRAND.muted} strokeWidth={1.5}
             strokeDasharray="4 4" dot={false} name="ETS (top-down)" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function WeeklyTable({ run }: { run: CashForecastRun }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="border-b border-border/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <th className="py-2 pr-4 font-semibold">Week of</th>
            <th className="py-2 pr-4 text-right font-semibold">p10</th>
            <th className="py-2 pr-4 text-right font-semibold">p50</th>
            <th className="py-2 pr-4 text-right font-semibold">p90</th>
            <th className="py-2 pr-4 text-right font-semibold">PTP scheduled</th>
            <th className="py-2 pr-4 text-right font-semibold">Bottom-up</th>
            <th className="py-2 pr-4 text-right font-semibold">ETS (top-down)</th>
          </tr>
        </thead>
        <tbody>
          {run.weeks.map((w) => (
            <tr key={w.week_start} className="border-b border-border/40 last:border-0">
              <td className="py-2.5 pr-4 text-foreground">{w.week_start}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{fmtINR(w.p10)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums font-semibold">{fmtINR(w.p50)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{fmtINR(w.p90)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">{fmtINR(w.ptp_scheduled)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">{fmtINR(w.bottom_up)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">{fmtINR(w.top_down)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="text-[12.5px] font-semibold">
            <td className="py-2.5 pr-4">13-week total</td>
            <td className="py-2.5 pr-4 text-right tabular-nums">{fmtINR(run.totals.p10)}</td>
            <td className="py-2.5 pr-4 text-right tabular-nums">{fmtINR(run.totals.p50)}</td>
            <td className="py-2.5 pr-4 text-right tabular-nums">{fmtINR(run.totals.p90)}</td>
            <td colSpan={3} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function Results({ run }: { run: CashForecastRun }) {
  const tiles = headlineTiles(run);
  return (
    <>
      <HonestyNote run={run} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => <Tile key={t.key} label={t.label} value={t.value} sub={t.sub} />)}
      </div>

      <Panel title="Weekly collection inflow" hint="p10–p90 band, median, and the ETS leg alone">
        <FanChart run={run} />
        <p className="mt-3 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
          <Info className="size-3.5 shrink-0" aria-hidden="true" />
          The shaded band is where 8 in 10 of the method's own historical one-step errors would have
          landed, widened for how many weeks out the week is.
        </p>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Forecast-vs-actual tracker" hint="Rolling-origin backtest on this book's own history">
          <p className="text-[12.5px] leading-relaxed text-foreground">{backtestCaption(run)}</p>
        </Panel>
        <Panel title="What this reconciles" hint={`${run.history_weeks} week(s) of VERIFIED payment history`}>
          <ul className="space-y-2 text-[12.5px] text-foreground">
            <li>Top-down: a Holt ETS fit on weekly VERIFIED collections.</li>
            <li>Bottom-up: ACTIVE PTPs due in the next 13 weeks, de-rated by the bank's own honor
              rate ({honorRateLabel(run)}).</li>
            <li>+ recovery_risk's next-cycle estimate for {run.recovery_informed_loans} loan(s) with no
              active PTP ({fmtINR(run.recovery_informed_total)}).</li>
            <li>Reconciled 50/50 against the top-down leg once the bottom-up leg has any signal at all;
              the ETS leg alone otherwise.</li>
          </ul>
        </Panel>
      </div>

      <Panel title="Week by week">
        <WeeklyTable run={run} />
      </Panel>
    </>
  );
}

export default function CashForecastPage() {
  const q = useQuery({
    queryKey: ["bank", "strategy", "cash-forecast"],
    queryFn: () => getCashForecast(),
    retry: false,
  });

  return (
    <PageRoot>
      <ToolHeader
        title="Cash Forecast"
        icon={TrendingUp}
        description="The next 13 weeks of collection inflow, reconciled from the book's known commitments and its
          own collection history, with bands instead of a single number."
      />

      {q.isError && (
        <div role="alert" className="rounded-card border border-destructive/40 bg-destructive/5 px-5 py-4 text-[12.5px] text-destructive">
          {errorCode(q.error) === "INSUFFICIENT_HISTORY"
            ? "Not enough VERIFIED payment history to forecast cash flow yet. The method needs several "
              + "weeks of collections to fit a trend from, and refuses rather than inventing them."
            : errorDetail(q.error, "The cash forecast could not be loaded.")}
        </div>
      )}

      {q.isLoading && <AnalyticsLoading label="Building the 13-week forecast…" />}

      {q.data && <Results run={q.data} />}
    </PageRoot>
  );
}
