// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-18 — NEW. "Promise outcomes by month" on Analytics, full width under
//   the Collection Trend: the second six-month series on the page.
//
//   WHY IT EXISTS NOW. Until 2026-09-17 a promise never ended unless a payment
//   honoured it, so a chart of outcomes would have been one green sliver over
//   a sea of "open". The lifecycle job resolves promises a day after their
//   grace day, and the overview's Promises card shows the LIFETIME kept rate
//   (39%) — a stock. This is the flow: how the promises that fell due each
//   month ended, so the manager can see whether promise quality is moving.
//
//   Stacked columns kept / partly kept / broken / rescheduled, drawn in the
//   DPD donut's validated palette. Promises still open are the top segment,
//   HATCHED and grey, because the current month is always incomplete and a
//   solid column would read as a collapse. The kept-rate line sits on a right
//   axis and is the endpoint's number — honoured ÷ (honoured + broken), the
//   same definition as the overview card, so the two never disagree.
//
//   Per agent when one is selected on the page (the endpoint takes agent_id
//   and 404s on another manager's agent), team-wide otherwise. Hover shows
//   counts and the promised / paid rupees for the month.
// ─────────────────────────────────────────────────────────────────────────────

import { useQuery } from "@tanstack/react-query";
import { Handshake } from "lucide-react";
import { Bar, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getPtpOutcomes } from "@/api/manager";
import { shortMoney } from "@/lib/money";
import { LIVE } from "@/lib/liveQuery";
import {
  OUTCOME_COLOURS, OUTCOME_WORDS, chartMonths, keptTrend, monthWords, openNote, type PtpOutcomeMonth,
} from "./ptpOutcomes";

const TOOLTIP_STYLE = {
  fontSize: "11px", borderRadius: "8px",
  border: "1px solid #111827", backgroundColor: "#111827",
  color: "#F9FAFB", boxShadow: "0 4px 12px rgba(0,0,0,0.18)", padding: "7px 11px",
};
const RATE = "#1C1C1F";
const KEYS = ["honored", "partly", "broken", "rescheduled", "open"] as const;

export function PtpOutcomesCard({ agentId, agentName, months = 6 }: { agentId?: string | null; agentName?: string | null; months?: number }) {
  const q = useQuery({
    queryKey: ["manager", "analytics", "ptp-outcomes", months, agentId ?? null],
    queryFn: () => getPtpOutcomes(months, agentId ?? undefined),
    retry: false, staleTime: 0, ...LIVE,
  });
  const rows = chartMonths(q.data);
  const trend = keptTrend(rows);
  const note = openNote(rows);
  const nothing = rows.every((r) => r.total === 0);

  return (
    <div className="card p-4 sm:p-6 flex flex-col">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-1">
        <div className="flex items-center gap-2">
          <span className="flex-shrink-0" style={{ color: "#6B6D76" }} aria-hidden="true"><Handshake className="w-4 h-4" /></span>
          <h2 className="text-[15px] sm:text-base font-bold" style={{ color: "#1C1C1F" }}>Promise Outcomes by Month</h2>
        </div>
        {trend && (
          <p className="text-xs tabular-nums" style={{ color: "#6B6D76" }} title="Kept rate, first to last completed month in the window">
            Kept rate {monthWords(trend.from.month)} <strong style={{ color: "#1C1C1F" }}>{Math.round(trend.from.kept_rate_pct ?? 0)}%</strong>
            {" → "}{monthWords(trend.to.month)}{" "}
            <strong style={{ color: (trend.to.kept_rate_pct ?? 0) >= (trend.from.kept_rate_pct ?? 0) ? OUTCOME_COLOURS.honored : OUTCOME_COLOURS.broken }}>
              {Math.round(trend.to.kept_rate_pct ?? 0)}%
            </strong>
          </p>
        )}
      </div>
      <p className="text-xs mb-3" style={{ color: "#6B6D76" }}>
        {agentName ? `${agentName}'s promises` : "All promises"} by the month they fell due · kept rate = kept ÷ (kept + broken), open promises excluded
      </p>

      {q.isError ? (
        <p className="text-sm text-center py-8" style={{ color: "#B45309" }}>Could not load promise outcomes.</p>
      ) : !q.data ? (
        <div className="h-52 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />
      ) : nothing ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>No promises fell due in the last {months} months.</p>
      ) : (
        <div className="flex flex-col flex-1">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="28%">
                <defs>
                  <pattern id="ptp-open-hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
                    <rect width="6" height="6" fill="#F1F5F9" />
                    <line x1="0" y1="0" x2="0" y2="6" stroke={OUTCOME_COLOURS.open} strokeWidth="2" />
                  </pattern>
                </defs>
                <XAxis dataKey="month" tickFormatter={monthWords} tick={{ fontSize: 10, fill: "#94a3b8", fontWeight: 500 }} axisLine={false} tickLine={false} />
                <YAxis yAxisId="n" tick={{ fontSize: 10, fill: "#94a3b8", fontWeight: 500 }} axisLine={false} tickLine={false} width={34} tickCount={4} allowDecimals={false} />
                <YAxis yAxisId="pct" orientation="right" domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} tick={{ fontSize: 10, fill: "#94a3b8", fontWeight: 500 }} axisLine={false} tickLine={false} width={36} tickCount={5} />
                <Tooltip
                  cursor={{ fill: "rgba(148,163,184,0.12)" }}
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: "#CBD5E1", marginBottom: 2 }}
                  itemStyle={{ color: "#F9FAFB", padding: 0 }}
                  labelFormatter={(ym, payload) => {
                    const m = payload?.[0]?.payload as PtpOutcomeMonth | undefined;
                    if (!m) return monthWords(String(ym));
                    return `${monthWords(String(ym))} · ${m.total} due · promised ${shortMoney(m.promised_amount)} · paid ${shortMoney(m.paid_amount)}${m.is_current ? " · in progress" : ""}`;
                  }}
                  formatter={(value, name) => {
                    if (name === "kept_rate_pct") return [value == null ? "—" : `${value}%`, "Kept rate"];
                    return [String(value), OUTCOME_WORDS[name as keyof typeof OUTCOME_WORDS] ?? String(name)];
                  }}
                />
                {KEYS.map((k, i) => (
                  <Bar key={k} yAxisId="n" dataKey={k} name={k} stackId="p"
                       fill={k === "open" ? "url(#ptp-open-hatch)" : OUTCOME_COLOURS[k]}
                       stroke={k === "open" ? OUTCOME_COLOURS.open : undefined} strokeWidth={k === "open" ? 1 : 0}
                       radius={i === KEYS.length - 1 ? [3, 3, 0, 0] : undefined}
                       isAnimationActive animationDuration={500} />
                ))}
                <Line yAxisId="pct" type="monotone" dataKey="kept_rate_pct" name="kept_rate_pct" stroke={RATE} strokeWidth={2}
                      dot={{ r: 3, fill: RATE, strokeWidth: 0 }} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive animationDuration={600} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] mt-auto pt-3" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
            {KEYS.map((k) => (
              <span key={k} className="inline-flex items-center gap-1.5">
                <i aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 2, display: "inline-block",
                   background: k === "open" ? `repeating-linear-gradient(45deg, ${OUTCOME_COLOURS.open} 0 2px, #F1F5F9 2px 4px)` : OUTCOME_COLOURS[k] }} />
                {OUTCOME_WORDS[k]}
              </span>
            ))}
            <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" style={{ width: 14, height: 2, background: RATE, display: "inline-block" }} />Kept rate</span>
            {note && <span className="basis-full sm:basis-auto sm:ml-auto" style={{ color: "#94A3B8" }}>{note}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
