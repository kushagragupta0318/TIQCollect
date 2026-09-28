// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-31 — Responsive pass. Densest page in the manager flow: the agent
//   pill row (up to 18 wrapping pills, ~180px tall before the chart is even
//   visible) becomes a select below lg; the 6-month selector grids become
//   scroll rails rather than squeezing "Jul '26" into 49px; chart heights and
//   calendar cells scale down on touch. See docs/frontend-guide.md.
// ─────────────────────────────────────────────────────────────────────────
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useIsBelowLg, useMediaQuery } from "@/hooks/useMediaQuery";
import { TrendingUp, BarChart2, IndianRupee, Users, Calendar, X } from "lucide-react";
import { toast } from "react-hot-toast";
import { shortAmount, shortMoney } from "@/lib/money";
import {
  AreaChart, Area, LineChart as RLineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import {
  getAnalytics, getAgentsPerformance, getDashboard,
  getManagerAgentCalendar, getAgentDPDBreakdown, getTeamDPDBreakdown, getTeamAttendance,
} from "@/api/manager";
import { CasePipelineCard } from "./CasePipelineCard";
import { CashTrendCard, PaymentMixCard } from "./PaymentModesCard";
import { PtpOutcomesCard } from "./PtpOutcomesCard";
import { DutyCalendarCard } from "./DutyCalendarCard";
import { MonthlyReportSection } from "./MonthlyReportSection";
import { CAL_MUTED } from "./calendarTheme";
import { EASE } from "@/lib/motion";
import { Reveal } from "@/components/ui/Reveal";
import type {
  AnalyticsData, AgentsPerformanceData, AgentPerfEntry, AgentMonthlyPerf,
  TeamAttendance,
  RecoveryBreakdown,
} from "@/api/manager";
import { TierBadge } from "@/components/ui/Badge";
import { LIVE, useLiveRefresh } from "@/lib/liveQuery";


const LINE_COLORS = [
  "#1677FF", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
  "#0891b2", "#be185d", "#65a30d", "#ea580c", "#0284c7",
  "#9333ea", "#b91c1c", "#059669", "#c2410c", "#0C4DB3",
];

const MONTH_SHORT: Record<string, string> = {
  "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
  "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
  "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
};

function monthLabel(ym: string) {
  const [, m] = ym.split("-");
  return MONTH_SHORT[m] ?? ym;
}

const BUCKET_LABELS: Record<string, string> = {
  BUCKET_2: "31–60 DPD", BUCKET_3: "61–90 DPD",
  NPA: "NPA 90+", BUCKET_1: "1–30 DPD",
};

const TOOLTIP_STYLE = {
  fontSize: "11px", borderRadius: "8px",
  border: "1px solid #111827", backgroundColor: "#111827",
  color: "#F9FAFB", boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
  padding: "7px 11px",
};
const TICK_STYLE = { fontSize: 10, fill: "#94a3b8", fontWeight: 500 };

// Axis ticks. Delegates to lib/money so the tick and the tooltip beside it
// cannot disagree about the unit.
function yFmt(v: number) {
  return shortAmount(v);
}

// ── Dual-line chart for agent spotlight (collected + target) ──────────────────


function AgentDualChart({ months, monthlyData, color, animate, onMonthClick }: {
  months: string[];
  monthlyData: AgentMonthlyPerf[];
  color: string;
  animate: boolean;
  onMonthClick: (label: string) => void;
}) {
  const isMobile = useMediaQuery("(max-width: 639px)");
  const gradId = `grad-dual-${color.replace("#", "")}`;
  const data = months.map((m) => {
    const e = monthlyData.find((x) => x.month === m);
    return { month: monthLabel(m), collected: e?.collected ?? 0, target: e?.target ?? 0 };
  });
  return (
    // Recharts handles width via ResponsiveContainer; height is a fixed number
    // and needs stepping down so the chart does not dominate a short viewport.
    <ResponsiveContainer width="100%" height={isMobile ? 128 : 150}>
      <RLineChart
        data={data}
        margin={{ top: 10, right: 12, left: 0, bottom: 0 }}
        style={{ cursor: "pointer" }}
        onClick={(d) => { if (d?.activeLabel) onMonthClick(String(d.activeLabel)); }}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor={color} stopOpacity={0.18} />
            <stop offset="95%" stopColor={color} stopOpacity={0.0}  />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#EAEBEF" vertical={false} />
        <XAxis dataKey="month" tick={TICK_STYLE} axisLine={false} tickLine={false} />
        <YAxis tick={TICK_STYLE} axisLine={false} tickLine={false} width={44} tickFormatter={yFmt} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          cursor={{ stroke: "#E2E8F0", strokeWidth: 1 }}
          formatter={(v: unknown, name: unknown) => [
            yFmt(Number(v ?? 0)),
            String(name) === "collected" ? "Collected" : "Target",
          ]}
        />
        <Line type="monotone" dataKey="target" stroke="#C4C6CF" strokeWidth={1.5}
          strokeDasharray="4 3" dot={false} activeDot={{ r: 4, strokeWidth: 0, fill: "#C4C6CF" }}
          isAnimationActive={animate} animationDuration={900} animationEasing="ease-out" />
        <Line type="monotone" dataKey="collected" stroke={color} strokeWidth={2.5}
          dot={false} activeDot={{ r: 5, fill: color, strokeWidth: 2, stroke: "#fff" }}
          isAnimationActive={animate} animationDuration={900} animationEasing="ease-out" />
      </RLineChart>
    </ResponsiveContainer>
  );
}

// ── Multi-series line chart (team overview) ───────────────────────────────────

function TeamLineChart({ months, series, animate, onMonthClick }: {
  months: string[];
  series: { label: string; color: string; values: number[] }[];
  animate: boolean;
  onMonthClick?: (label: string) => void;
}) {
  const isMobile = useMediaQuery("(max-width: 639px)");
  const chartHeight = isMobile ? 170 : 200;
  const data = months.map((m, i) => {
    const row: Record<string, string | number> = { month: monthLabel(m) };
    series.forEach((s) => { row[s.label] = s.values[i] ?? 0; });
    return row;
  });
  const gradId = `grad-team-${series[0]?.color.replace("#", "") ?? "blue"}`;
  if (series.length === 1) {
    return (
      <ResponsiveContainer width="100%" height={chartHeight}>
        <AreaChart data={data} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}
          style={onMonthClick ? { cursor: "pointer" } : undefined}
          onClick={(d) => { if (onMonthClick && d?.activeLabel) onMonthClick(String(d.activeLabel)); }}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={series[0].color} stopOpacity={0.22} />
              <stop offset="95%" stopColor={series[0].color} stopOpacity={0.0}  />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#EAEBEF" vertical={false} />
          <XAxis dataKey="month" tick={TICK_STYLE} axisLine={false} tickLine={false} />
          <YAxis tick={TICK_STYLE} axisLine={false} tickLine={false} width={44} tickFormatter={yFmt} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ stroke: "#E2E8F0", strokeWidth: 1 }} formatter={(v: unknown) => [yFmt(Number(v ?? 0)), series[0].label]} />
          <Area type="monotone" dataKey={series[0].label} stroke={series[0].color} strokeWidth={2}
            fill={`url(#${gradId})`} dot={false} activeDot={{ r: 4, fill: series[0].color, strokeWidth: 0 }}
            isAnimationActive={animate} animationDuration={800} animationEasing="ease-out" />
        </AreaChart>
      </ResponsiveContainer>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <RLineChart
        data={data}
        margin={{ top: 10, right: 12, left: 0, bottom: 0 }}
        style={onMonthClick ? { cursor: "pointer" } : undefined}
        onClick={(d) => { if (onMonthClick && d?.activeLabel) onMonthClick(String(d.activeLabel)); }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#EAEBEF" vertical={false} />
        <XAxis dataKey="month" tick={TICK_STYLE} axisLine={false} tickLine={false} />
        <YAxis tick={TICK_STYLE} axisLine={false} tickLine={false} width={44} tickFormatter={yFmt} />
        <Tooltip cursor={{ stroke: "#E2E8F0", strokeWidth: 1 }}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            return (
              <div style={{ ...TOOLTIP_STYLE, maxHeight: 170, overflowY: "auto" }}>
                <p style={{ color: "#94a3b8", fontSize: 10, marginBottom: 6 }}>{String(label ?? "")}</p>
                {payload.slice().sort((a, b) => Number(b.value ?? 0) - Number(a.value ?? 0)).map((p) => (
                  <p key={String(p.name)} style={{ fontWeight: 600, display: "flex", gap: 8, alignItems: "center", marginBottom: 3 }}>
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: String(p.stroke ?? "#fff"), flexShrink: 0, display: "inline-block" }} />
                    <span style={{ color: "#94a3b8" }}>{String(p.name ?? "").split(" ")[0]}</span>
                    <span style={{ color: "#F9FAFB", marginLeft: "auto", paddingLeft: 12 }}>{yFmt(Number(p.value ?? 0))}</span>
                  </p>
                ))}
              </div>
            );
          }}
        />
        {series.map((s, i) => (
          <Line key={s.label} type="monotone" dataKey={s.label} stroke={s.color} strokeWidth={2}
            strokeOpacity={0.85} dot={false} activeDot={{ r: 4, strokeWidth: 0 }}
            isAnimationActive={animate} animationDuration={800} animationEasing="ease-out" animationBegin={i * 100} />
        ))}
      </RLineChart>
    </ResponsiveContainer>
  );
}

// ── Individual agent spotlight card ──────────────────────────────────────────

function rateCol(pct: number) {
  return pct >= 60 ? "#16a34a" : pct >= 35 ? "#d97706" : "#dc2626";
}

function AgentSpotlight({ entry, months, color, animate, onClose, selMonth, onMonthClick }: {
  entry: AgentPerfEntry; months: string[]; color: string; animate: boolean; onClose: () => void;
  selMonth: string | null; onMonthClick: (label: string) => void;
}) {
  // Pick data source: selected month or all-time aggregate
  const monthData = selMonth
    ? entry.monthly.find((m) => monthLabel(m.month) === selMonth) ?? null
    : null;

  const rate    = monthData ? monthData.collection_rate_pct : (entry.total_target > 0 ? Math.min(Math.round((entry.total_collected / entry.total_target) * 100), 100) : 0);
  const collected = monthData ? monthData.collected : entry.total_collected;
  const visits  = monthData ? monthData.visits : entry.monthly.reduce((s, m) => s + m.visits, 0);
  const ptpSet  = monthData ? monthData.ptps_set    : entry.monthly.reduce((s, m) => s + m.ptps_set, 0);
  const ptpHon  = monthData ? monthData.ptps_honored : entry.monthly.reduce((s, m) => s + m.ptps_honored, 0);
  const ptpRate = ptpSet > 0 ? Math.round((ptpHon / ptpSet) * 100) : 0;
  const rc      = rateCol(rate);

  // Previous month for MoM delta (only when a specific month is selected)
  const prevMonthData = (() => {
    if (!monthData) return null;
    const idx = entry.monthly.findIndex((m) => monthLabel(m.month) === selMonth);
    return idx > 0 ? entry.monthly[idx - 1] : null;
  })();
  const rateDelta   = prevMonthData ? +(rate - prevMonthData.collection_rate_pct).toFixed(1) : null;
  const visitsDelta = prevMonthData && prevMonthData.visits > 0
    ? +((visits - prevMonthData.visits) / prevMonthData.visits * 100).toFixed(1) : null;
  const collectDelta = prevMonthData && prevMonthData.collected > 0
    ? +((collected - prevMonthData.collected) / prevMonthData.collected * 100).toFixed(1) : null;
  const ptpPrevRate = prevMonthData && prevMonthData.ptps_set > 0
    ? Math.round(prevMonthData.ptps_honored / prevMonthData.ptps_set * 100) : null;
  const ptpDelta    = ptpPrevRate !== null ? +(ptpRate - ptpPrevRate).toFixed(1) : null;

  return (
    <div
      style={{
        borderRadius: 16,
        padding: "20px 20px 16px",
        background: "#FFFFFF",
        border: "1px solid #ECEDF1",
        boxShadow: "0 1px 2px rgba(16,24,40,0.04)",
        animation: `enter 340ms ${EASE} both`,
        position: "relative" as const,
      }}
    >
      {/* Close */}
      <button
        onClick={onClose}
        aria-label="Close agent spotlight"
        className="tap-target"
        style={{
          position: "absolute", top: 14, right: 14,
          width: 26, height: 26, borderRadius: 8, border: "none",
          background: "rgba(0,0,0,0.06)", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
        }}
      >
        <X className="w-3.5 h-3.5" style={{ color: "#6B6D76" }} />
      </button>

      {/* Agent header */}
      <div className="flex items-start gap-3 mb-3 pr-8">
        <div style={{
          width: 40, height: 40, borderRadius: 12, flexShrink: 0,
          background: `${color}18`, border: `1.5px solid ${color}33`,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 16, fontWeight: 600, color,
        }}>
          {entry.agent_name.charAt(0)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-bold text-sm" style={{ color: "#1C1C1F" }}>{entry.agent_name}</p>
            <TierBadge tier={entry.tier as "TIER_1" | "TIER_2" | "TIER_3"} />
          </div>
          <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>{entry.employee_code} · {entry.territory}</p>
        </div>
      </div>

      {/* Dynamic KPI strip — values update silently when a month is clicked */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        {([
          { label: "rate",      value: `${rate.toFixed(0)}%`,   colorVal: rc,               delta: rateDelta    },
          { label: "collected", value: shortMoney(collected), colorVal: "#1C1C1F", delta: collectDelta },
          { label: "visits",    value: String(visits),           colorVal: "#1C1C1F",        delta: visitsDelta  },
          { label: "PTP conv.", value: `${ptpRate}%`,            colorVal: rateCol(ptpRate), delta: ptpDelta     },
        ] as { label: string; value: string; colorVal: string; delta: number | null }[]).map(({ label, value, colorVal, delta }) => (
          // Border moved out of the inline style into .kpi-chip: inline wins
          // over class rules, so the hover border-color could never paint.
          <div key={label} className="kpi-chip flex flex-col items-center rounded-xl px-2.5 sm:px-3 py-1.5 flex-1 sm:flex-none"
            style={{ background: "#fff", minWidth: 60 }}>
            <p className="text-sm font-bold leading-none whitespace-nowrap" style={{ color: colorVal }}>{value}</p>
            {delta !== null ? (
              <p className="text-xs mt-0.5 font-semibold" style={{ color: delta >= 0 ? "#16a34a" : "#dc2626" }}>
                {delta >= 0 ? "↑" : "↓"}{Math.abs(delta)}%
              </p>
            ) : (
              <p className="text-xs mt-0.5" style={{ color: "#94a3b8" }}>{label}</p>
            )}
          </div>
        ))}
        {!selMonth && (
          <p className="text-xs ml-1" style={{ color: "#94a3b8" }}>Click a month below to drill in</p>
        )}
      </div>

      {/* Dual chart: collected + target */}
      <div style={{ borderRadius: 12, overflow: "hidden", background: "#fff", padding: "12px 8px 4px", boxShadow: `inset 0 0 0 1px ${color}1A` }}>
        <AgentDualChart months={months} monthlyData={entry.monthly} color={color} animate={animate} onMonthClick={onMonthClick} />
      </div>

      {/* Chart legend */}
      <div className="flex gap-4 mt-1 mb-2 justify-center">
        <span className="flex items-center gap-1.5 text-xs" style={{ color: "#6B6D76" }}>
          <span className="w-3 h-0.5 inline-block rounded" style={{ background: color }} />Collected
        </span>
        <span className="flex items-center gap-1.5 text-xs" style={{ color: "#6B6D76" }}>
          <span className="w-3 h-0.5 inline-block rounded" style={{ background: "#C4C6CF", borderTop: "1px dashed #C4C6CF" }} />Target
        </span>
      </div>

      {/* Monthly selector — same scroll-rail treatment as the team selector */}
      <div
        className="grid mt-1 gap-1 overflow-x-auto scrollbar-hide"
        style={{ gridTemplateColumns: `repeat(${months.length}, minmax(60px, 1fr))` }}
      >
        {entry.monthly.map((m) => {
          const isActive = selMonth === monthLabel(m.month);
          const [y] = m.month.split("-");
          const label = `${monthLabel(m.month)} '${y.slice(2)}`;
          return (
            <button
              key={m.month}
              onClick={() => onMonthClick(monthLabel(m.month))}
              aria-pressed={isActive}
              className="tap-target-h text-center w-full"
              style={{
                borderRadius: 8, padding: "5px 2px",
                background: isActive ? `${color}15` : "transparent",
                border: `1.5px solid ${isActive ? color : "transparent"}`,
                cursor: "pointer",
                transition: "all 150ms ease",
              }}
            >
              <p className="text-xs font-semibold whitespace-nowrap" style={{ color: isActive ? color : "#6B6D76" }}>{label}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── React Query migration, 2026-09-10 ──────────────────────────────────────
// The QueryClient in App.tsx sets `staleTime: 30_000, retry: 1` globally. Both
// would change what this page puts on the wire, so both are overridden: the
// hand-rolled effects retried nothing and refetched on every dependency change.
// `refetchOnWindowFocus` / `refetchOnReconnect` are off because this page had no
// focus listener — React Query's defaults would ADD requests that never existed.
const AS_BEFORE = {
  retry: false,
  staleTime: 0,
  // 2026-09-18: every query on this page is live — a minute's poll while the
  // tab is visible, plus a refetch on focus/reconnect (lib/liveQuery.ts).
  // The sentence above about "no focus listener" described the old code; the
  // charts had been frozen from load until the next navigation.
  ...LIVE,
} as const;

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ManagerAnalyticsPage() {
  const [analytics, setAnalytics]   = useState<AnalyticsData | null>(null);
  const [agentPerf, setAgentPerf]   = useState<AgentsPerformanceData | null>(null);
  const [loading, setLoading]       = useState(true);
  const [chartMode, setChartMode]   = useState<"team" | "individual">("team");
  // `barReady` is DERIVED, not stored. 2026-09-10.
  //
  // It used to be a boolean that an effect reset to false and then set true on a
  // 160ms timer, so the effect wrote state synchronously on every render where
  // `loading` or `analytics` changed — `react-hooks/set-state-in-effect`, one of
  // the 18 errors keeping CI red, and a genuine cascading-render risk on a page
  // this size.
  //
  // Storing WHICH analytics payload the bars have finished staggering for makes
  // the reset unnecessary: when `analytics` is replaced the identity comparison
  // is false again by itself, which is exactly what the removed line did by
  // hand. Behaviour is unchanged, including the re-animation on reload — the
  // thing a plain `setBarReady(false)` deletion would have quietly broken.
  const [readyFor, setReadyFor] = useState<unknown>(null);
  const barReady = !loading && !!analytics && readyFor === analytics;
  const [selectedAgent, setSelectedAgent] = useState<AgentPerfEntry | null>(null);
  // Behavioural switches only — pill row becomes a select, copy changes from
  // "Click"/"Hover" to "Tap"/"Pick". Layout itself is done with Tailwind.
  const isBelowLg = useIsBelowLg();
  const [selTeamMonth, setSelTeamMonth]   = useState<string | null>(null);
  const [selAgentMonth, setSelAgentMonth] = useState<string | null>(null);  // month label e.g. "Apr"

  useEffect(() => {
    Promise.all([getAnalytics(), getAgentsPerformance(6)])
      .then(([a, p]) => { setAnalytics(a); setAgentPerf(p); })
      .catch(() => toast.error("Failed to load analytics"))
      .finally(() => setLoading(false));
  }, []);
  // 2026-09-18 — the trend, DPD, recovery and leaderboard payload above is
  // effect-loaded, not a query, so it gets the same cadence by hand: a quiet
  // re-read every minute while visible and on focus. Silent on failure — a
  // stale chart beats a toast every minute on a flaky link.
  useLiveRefresh(() => {
    Promise.all([getAnalytics(), getAgentsPerformance(6)])
      .then(([a, p]) => { setAnalytics(a); setAgentPerf(p); })
      .catch(() => {});
  });

  useEffect(() => {
    if (!loading && analytics) {
      const t = setTimeout(() => setReadyFor(analytics), 160);
      return () => clearTimeout(t);
    }
  }, [loading, analytics]);

  // ─── React Query, 2026-09-10 ────────────────────────────────────────────
  //
  // The two effects these replace each began by writing state synchronously —
  // `setAgentDataLoading(true)`, `setAgentCalendar(null)`, `setTeamDpdRows(null)`
  // — which is what `react-hooks/set-state-in-effect` was flagging. The resets
  // are now derivations: when no agent is selected there is nothing to null out,
  // because the value is simply not read.
  //
  // Query keys carry only primitives (agent id, "YYYY-MM" or null), so they are
  // structurally stable and two renders with the same selection hash the same.
  //
  // Resolve the month LABEL the pills show ("Apr") back to the "YYYY-MM" the API
  // takes — the same reverse lookup the effects did, hoisted so it can key the
  // queries.
  const apiAgentMonth = selAgentMonth
    ? (agentPerf?.months ?? []).find((m) => monthLabel(m) === selAgentMonth) ?? null
    : null;
  const apiTeamMonth = selTeamMonth && analytics
    ? analytics.monthly_trend.find((m) => monthLabel(m.month) === selTeamMonth)?.month ?? null
    : null;

  // Keyed on the AGENT alone, because `getManagerAgentCalendar` takes no month.
  //
  // I expected that to remove a redundant request — the old effect depended on
  // `selAgentMonth`, so changing the month refetched the calendar too. IT DOES
  // NOT, and the comment that claimed it did was wrong. Measured in a browser on
  // 2026-09-10: selecting an agent fires one `availability-calendar` and one
  // `dpd-breakdown`, and changing the month fires BOTH again. `staleTime: 0`
  // marks the query stale the moment it settles, so the agent-detail subtree
  // re-mounting on a month change refetches it through `refetchOnMount` — the
  // key never has to change.
  //
  // That is the RIGHT outcome for this migration, which is meant to preserve the
  // request profile exactly, and the caching win is available later by raising
  // `staleTime` on this one query. Recorded rather than quietly deleted, because
  // an optimisation that a measurement says did not happen is worth knowing
  // about the next time somebody reaches for it.
  const agentCalendarQ = useQuery({
    queryKey: ["manager", "agent", selectedAgent?.agent_id ?? null, "calendar"],
    queryFn: () => getManagerAgentCalendar(selectedAgent!.agent_id),
    enabled: !!selectedAgent,
    ...AS_BEFORE,
  });
  const agentDpdQ = useQuery({
    queryKey: ["manager", "agent", selectedAgent?.agent_id ?? null, "dpd", apiAgentMonth],
    queryFn: () => getAgentDPDBreakdown(selectedAgent!.agent_id, apiAgentMonth ?? undefined),
    enabled: !!selectedAgent,
    ...AS_BEFORE,
  });
  const agentCalendar = selectedAgent ? agentCalendarQ.data ?? null : null;
  const agentDpdRows  = selectedAgent ? agentDpdQ.data ?? null : null;
  const agentDataLoading = agentCalendarQ.isFetching || agentDpdQ.isFetching;

  // The case-pipeline donut's counts. Same key as the overview's dashboard
  // query, so a manager arriving from the overview gets a cache hit and the
  // two pages can never show different totals. Added 2026-09-16.
  const dashboardQ = useQuery({
    queryKey: ["manager", "dashboard"],
    queryFn: getDashboard,
    ...AS_BEFORE,
  });
  const navigate = useNavigate();

  const teamDpdQ = useQuery({
    queryKey: ["manager", "team", "dpd", apiTeamMonth],
    queryFn: () => getTeamDPDBreakdown(apiTeamMonth ?? undefined),
    enabled: !selectedAgent && !!apiTeamMonth,
    ...AS_BEFORE,
  });
  const teamDpdRows = selectedAgent ? null : teamDpdQ.data ?? null;

  // The toasts the old `.catch` arms fired. Watching `isError` rather than
  // wrapping each queryFn keeps the two request paths untouched; these set no
  // React state, so they are not what the lint rule is about.
  useEffect(() => {
    if (agentCalendarQ.isError || agentDpdQ.isError) toast.error("Could not load agent details");
  }, [agentCalendarQ.isError, agentDpdQ.isError]);
  useEffect(() => {
    if (teamDpdQ.isError) toast.error("Could not load team DPD breakdown");
  }, [teamDpdQ.isError]);

  if (loading) return <LoadingSkeleton />;
  if (!analytics || !agentPerf) return <p className="text-sm p-6" style={{ color: "#6B6D76" }}>No data.</p>;

  const { kpis, monthly_trend, dpd_breakdown, recovery_breakdown, recovery_summary } = analytics;
  const { months, agents } = agentPerf;

  // Headline totals — THE PORTFOLIO SNAPSHOT, the same figure the Overview's
  // "Portfolio by DPD Bucket" card shows, so the two pages print one number.
  //
  // 2026-09-16, reversing the choice below. `kpis.total_collected_lakhs` /
  // `total_target_lakhs` are SUM(Case.collected_amount) / SUM(target_amount)
  // over the cases this manager's agents hold — lifetime, current cases —
  // and are the exact query behind the Overview card and the DPD bucket
  // card lower on this page. The product asked for the header to agree with
  // those rather than with the trend chart.
  //
  // *(This used to read: "Deliberately NOT kpis.total_collected_lakhs …
  // that block is a portfolio snapshot and measures something different, so
  // it read far below the months on show — 214.1L against 1572.0L summed."
  // The 1572.0L was the monthly TARGET summed across months — a case visited
  // in five months counted five times — which is why the window figure was
  // never a total anyone could tie to the book. Reconciled on 2026-09-16:
  // window 72.9L = portfolio 86.6L − 6.4L collected before the window − 7.6L
  // collected by other teams' agents on cases since reassigned here. Both
  // were right; they answered different questions, and the header now
  // answers the portfolio one. Corrected rather than deleted so the earlier
  // reasoning stays visible.)*
  //
  // The per-month figures are untouched: the trend chart still plots each
  // month's collected against that month's target, and selecting a month
  // still swaps these cards to that month (`selTeamTrend` below).
  const windowCollectedLakhs = kpis.total_collected_lakhs;
  const windowTargetLakhs    = kpis.total_target_lakhs;
  const windowRatePct        = kpis.overall_collection_rate_pct;

  // Team-month dynamic KPI computation
  const selTeamTrend = selTeamMonth ? monthly_trend.find((m) => monthLabel(m.month) === selTeamMonth) ?? null : null;
  const prevTeamTrend = selTeamTrend ? (() => {
    const idx = monthly_trend.findIndex((m) => monthLabel(m.month) === selTeamMonth);
    return idx > 0 ? monthly_trend[idx - 1] : null;
  })() : null;
  const teamRateDelta    = selTeamTrend && prevTeamTrend ? +(selTeamTrend.collection_rate_pct - prevTeamTrend.collection_rate_pct).toFixed(1) : null;
  const teamCollectDelta = selTeamTrend && prevTeamTrend && prevTeamTrend.collected_lakhs > 0
    ? +((selTeamTrend.collected_lakhs - prevTeamTrend.collected_lakhs) / prevTeamTrend.collected_lakhs * 100).toFixed(1) : null;
  const teamVisitsDelta  = selTeamTrend && prevTeamTrend && prevTeamTrend.total_visits > 0
    ? +((selTeamTrend.total_visits - prevTeamTrend.total_visits) / prevTeamTrend.total_visits * 100).toFixed(1) : null;

  // ── The two PTP numbers, kept apart ──────────────────────────────────────
  // CAPTURE grades the agent at the door: of the visits where a commitment was
  // the right outcome, how many secured one. Known the same day, so it is the
  // figure a manager can act on now — which is why it leads the card.
  // CONVERSION grades the borrower's follow-through and is not knowable for up
  // to a month. Both are shown, because high capture with low conversion means
  // soft promises taken to close visits, and reporting one number would hide it.
  //
  // Capture comes straight off monthly_trend now that the backend carries it
  // there, summed as counts and divided once. The old per-agent reduce is gone:
  // averaging per-agent rates let an agent with three visits move the team line
  // as far as one with ninety.
  const teamCapSel = selTeamTrend ? selTeamTrend.ptp_capture_pct : null;
  const teamCapPrev = prevTeamTrend ? prevTeamTrend.ptp_capture_pct : null;
  const teamCapDelta = teamCapSel !== null && teamCapPrev !== null
    ? +(teamCapSel - teamCapPrev).toFixed(1) : null;



  const teamSeries = [
    { label: "Collected", color: "#1677FF", values: monthly_trend.map((m) => m.collected_lakhs * 100000) },
    { label: "Target",    color: "#C4C6CF", values: monthly_trend.map((m) => m.target_lakhs * 100000)   },
  ];

  const agentSeries = agents.slice(0, 15).map((a, i) => ({
    label: a.agent_name,
    color: LINE_COLORS[i % LINE_COLORS.length],
    agent: a,
  }));

  const selectedAgentSeries = selectedAgent
    ? agentSeries.find((s) => s.agent.agent_id === selectedAgent.agent_id)
    : null;

  return (
    <div className="space-y-5">
      {/* KPI row — updates dynamically when a team month is selected */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {[
          {
            label: "Collection Rate",
            value: selTeamTrend ? `${selTeamTrend.collection_rate_pct.toFixed(1)}%` : `${windowRatePct.toFixed(1)}%`,
            sub: selTeamTrend ? "" : "on current portfolio",
            delta: teamRateDelta, deltaSuffix: "%",
            icon: <TrendingUp className="w-5 h-5" />, color: "text-brand-600",
          },
          {
            label: "Total Collected",
            value: selTeamTrend ? `₹${selTeamTrend.collected_lakhs.toFixed(1)}L` : `₹${windowCollectedLakhs.toFixed(1)}L`,
            sub: selTeamTrend ? `of ₹${selTeamTrend.target_lakhs.toFixed(1)}L target` : `of ₹${windowTargetLakhs.toFixed(1)}L target`,
            delta: teamCollectDelta, deltaSuffix: "%",
            icon: <IndianRupee className="w-5 h-5" />, color: "text-success-600",
          },
          {
            // ONE NUMBER. This card briefly carried the kept rate as a
            // qualifier — "23.2% / 63.2% then kept" — and it read as a riddle:
            // two percentages of two different denominators, three words of
            // explanation, in a box 274px wide. The kept rate is still on this
            // same page in the agent spotlight's "PTP conv." tile and in the
            // Agents table, both of which have room to label it properly.
            //
            // Capture earns the card because it is the LEADING indicator: known
            // the same day, and about something the team controls. Kept is not
            // knowable for up to a month.
            label: "PTP Capture Rate",
            value: teamCapSel !== null
              ? `${teamCapSel.toFixed(1)}%`
              : `${kpis.ptp_capture_rate_pct.toFixed(1)}%`,
            // The denominator, named — capture is a share of the visits where a
            // promise was the right outcome, not of all visits. Blank when a
            // month is selected, matching the other three cards.
            //
            // Kept SHORT on purpose: "of visits needing a PTP" wrapped inside a
            // 274px card, and because the cards are height-matched that one
            // wrap grew all four from 80px to 94px. A balance survives every
            // visit except a paid-in-full one, so "unpaid" is the accurate
            // short form — part-paid visits are still in the denominator.
            sub: teamCapSel !== null ? "" : "of unpaid visits",
            delta: teamCapDelta, deltaSuffix: "%",
            icon: <BarChart2 className="w-5 h-5" />, color: "text-warning-600",
          },
          {
            label: selTeamTrend ? "Total Visits" : "Avg Visits / Agent",
            value: selTeamTrend ? String(selTeamTrend.total_visits) : kpis.avg_visits_per_agent_current_month.toFixed(1),
            sub: selTeamTrend ? "" : "this month",
            delta: teamVisitsDelta, deltaSuffix: "%",
            icon: <Users className="w-5 h-5" />, color: "text-slate-700",
          },
        ].map((item, i) => (
          <div key={item.label} style={{ animation: `enter 420ms ${EASE} ${i * 60}ms both` }}>
            <KPICard
              label={item.label} value={item.value} sub={item.sub}
              icon={item.icon} color={item.color}
              deltaText={item.delta !== null ? `${item.delta >= 0 ? "↑" : "↓"}${Math.abs(item.delta)}${item.deltaSuffix}` : undefined}
              deltaPositive={item.delta !== null ? item.delta >= 0 : undefined}
            />
          </div>
        ))}
      </div>

      {/* Collection trend chart */}
      <Reveal className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 240ms both` }}>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
          <div className="min-w-0">
            <h2 className="text-[15px] sm:text-base font-bold" style={{ color: "#1C1C1F" }}>Collection Trend — Last 6 Months</h2>
            <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>
              {chartMode === "individual"
                ? (isBelowLg ? "Pick an agent to see their spotlight and update the panels below" : "Click an agent pill to see their spotlight and update the panels below")
                : (isBelowLg ? "Tap a month to drill in" : "Hover for values")}
            </p>
          </div>
          <div className="flex rounded-xl overflow-hidden text-xs self-start sm:self-auto flex-shrink-0" style={{ border: "1px solid #EAEBEF" }}>
            {(["team", "individual"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => { setChartMode(mode); setSelectedAgent(null); }}
                aria-pressed={chartMode === mode}
                className="tap-target-h px-3 py-1.5 font-semibold transition-colors whitespace-nowrap"
                style={{ background: chartMode === mode ? "#1677FF" : "#fff", color: chartMode === mode ? "#fff" : "#6B6D76" }}
              >
                {mode === "team" ? "Team Total" : "Per Agent"}
              </button>
            ))}
          </div>
        </div>

        {chartMode === "team" ? (
          <>
            <TeamLineChart
              key={`team-${barReady}`}
              months={months}
              series={teamSeries}
              animate={barReady}
              onMonthClick={(lbl) => setSelTeamMonth((prev) => prev === lbl ? null : lbl)}
            />
            <div className="flex gap-4 mt-2 justify-center">
              <span className="flex items-center gap-1.5 text-xs" style={{ color: "#6B6D76" }}>
                <span className="w-3 h-3 rounded-full inline-block bg-brand-600" />Collected
              </span>
              <span className="flex items-center gap-1.5 text-xs" style={{ color: "#6B6D76" }}>
                <span className="w-3 h-3 rounded-full inline-block" style={{ background: "#C4C6CF" }} />Target
              </span>
            </div>

            {!selTeamMonth && <p className="text-xs mt-2 text-center" style={{ color: "#94a3b8" }}>{isBelowLg ? "Tap" : "Click"} a month below to drill in</p>}

            {/* Monthly breakdown — a 6-across grid gives each button 49px at
                360px, which cannot hold "Jul '26". Minimum 64px in a scroll
                rail instead: every month stays reachable, nothing is clipped. */}
            <div
              className="grid mt-3 gap-2 overflow-x-auto scrollbar-hide -mx-1 px-1"
              style={{ gridTemplateColumns: `repeat(${months.length}, minmax(64px, 1fr))` }}
            >
              {monthly_trend.map((m) => {
                const isActive = selTeamMonth === monthLabel(m.month);
                const [y] = m.month.split("-");
                const label = `${monthLabel(m.month)} '${y.slice(2)}`;
                return (
                  <button
                    key={m.month}
                    onClick={() => setSelTeamMonth((prev) => prev === monthLabel(m.month) ? null : monthLabel(m.month))}
                    aria-pressed={isActive}
                    className="tap-target-h text-center w-full"
                    style={{
                      borderRadius: 10, padding: "8px 4px",
                      background: isActive ? "#EFF3FF" : "transparent",
                      border: `1.5px solid ${isActive ? "#1677FF" : "transparent"}`,
                      cursor: "pointer", transition: "all 150ms ease",
                    }}
                  >
                    <p className="text-xs font-semibold whitespace-nowrap" style={{ color: isActive ? "#1677FF" : "#6B6D76" }}>{label}</p>
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <>
            {/* Agent selector.
                Desktop keeps the pill row — scanning 18 colour-coded names at
                once is the point of it. On a phone those same pills wrap to
                about six rows and push the chart itself below the fold, so the
                same choice is offered as a native select instead. */}
            <div className="lg:hidden mb-4 flex gap-2">
              <select
                className="input flex-1 min-w-0 tap-target-h"
                aria-label="Select an agent"
                value={selectedAgent?.agent_id ?? ""}
                onChange={(e) => {
                  const found = agentSeries.find((s) => s.agent.agent_id === e.target.value);
                  setSelectedAgent(found ? found.agent : null);
                }}
              >
                <option value="">All agents</option>
                {agentSeries.map((s) => (
                  <option key={s.agent.agent_id} value={s.agent.agent_id}>{s.label}</option>
                ))}
              </select>
              {selectedAgent && (
                <button
                  onClick={() => setSelectedAgent(null)}
                  className="tap-target px-4 rounded-xl text-xs font-semibold flex-shrink-0"
                  style={{ background: "#1677FF", color: "white", border: "1px solid #1677FF" }}
                >
                  Clear
                </button>
              )}
            </div>

            <div className="hidden lg:flex flex-wrap gap-1.5 mb-4">
              {agentSeries.map((s) => {
                const isSelected = selectedAgent?.agent_id === s.agent.agent_id;
                return (
                  <button
                    key={s.agent.agent_id}
                    onClick={() => setSelectedAgent(isSelected ? null : s.agent)}
                    aria-pressed={isSelected}
                    className="flex items-center gap-1.5 rounded-full text-xs font-semibold transition-all"
                    style={{
                      padding: "5px 12px",
                      background: isSelected ? `${s.color}18` : "#F5F6F9",
                      border: `1.5px solid ${isSelected ? s.color : "#EAEBEF"}`,
                      color: isSelected ? s.color : "#6B6D76",
                      boxShadow: isSelected ? `0 2px 8px ${s.color}30` : "none",
                      transform: isSelected ? "scale(1.05)" : "scale(1)",
                    }}
                  >
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: s.color }} />
                    {s.label.split(" ")[0]}
                  </button>
                );
              })}
              {selectedAgent && (
                <button
                  onClick={() => setSelectedAgent(null)}
                  className="flex items-center gap-1 rounded-full text-xs font-semibold"
                  style={{ padding: "5px 12px", background: "#1677FF", color: "white", border: "1px solid #1677FF" }}
                >
                  Clear
                </button>
              )}
            </div>

            {selectedAgent && selectedAgentSeries ? (
              <AgentSpotlight
                key={selectedAgent.agent_id}
                entry={selectedAgent}
                months={months}
                color={selectedAgentSeries.color}
                animate={barReady}
                onClose={() => { setSelectedAgent(null); setSelAgentMonth(null); }}
                selMonth={selAgentMonth}
                onMonthClick={(lbl) => setSelAgentMonth((prev) => prev === lbl ? null : lbl)}
              />
            ) : (
              <TeamLineChart
                key={`individual-${barReady}`}
                months={months}
                series={agentSeries.map((s) => ({ label: s.label, color: s.color, values: s.agent.monthly.map((m) => m.collected) }))}
                animate={barReady}
              />
            )}
          </>
        )}
      </Reveal>

      {/* Promise outcomes — the second six-month series, full width like the
          trend above it. Per agent when one is selected. 2026-09-18. */}
      <Reveal style={{ animation: `enter 420ms ${EASE} 270ms both` }}>
        <PtpOutcomesCard agentId={selectedAgent?.agent_id ?? null} agentName={selectedAgent?.agent_name ?? null} />
      </Reveal>

      {/* Recovery + DPD, then the duty pair — context-aware.
          Every card below is a DIRECT grid child, deliberately. Wrapping two of
          them in a stacked <div> made the grid hold three items instead of four:
          the pair landed in one cell, the fourth cell stayed empty, and the row
          heights stopped matching. Flat children give two clean rows of two, and
          grid's default stretch keeps each row's cards the same height. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 items-stretch" style={{ animation: `enter 420ms ${EASE} 300ms both` }}>
        <Reveal className="flex flex-col [&>*]:flex-1">
          <RecoveryBreakdownCard
            rows={recovery_breakdown ?? []}
            summary={recovery_summary}
            loading={loading}
            barReady={barReady}
          />
        </Reveal>
        {/* Right column: the same 883 cases two ways — by DPD, then by
            state. Stacked in one cell so the pipeline sits under the bucket
            card and the pair matches the Recovery outlook's height. */}
        <Reveal className="flex flex-col [&>*]:flex-1">
        <div className="flex flex-col gap-5">
          <DPDBreakdownCard
            rows={selectedAgent ? (agentDpdRows ?? dpd_breakdown) : (teamDpdRows ?? dpd_breakdown)}
            loading={agentDataLoading}
            barReady={barReady}
            agentName={selectedAgent?.agent_name}
            selMonth={!selectedAgent ? selTeamMonth : selAgentMonth}
          />
          <CasePipelineCard
            counts={dashboardQ.data?.case_status_counts}
            onOpen={(statuses) => navigate(`/manager/cases?status=${statuses.join(",")}`)}
            style={{ flex: 1 }}
          />
        </div>
        </Reveal>
        {/* Second row: how the money came in — the mix on the left, the
            six-month cash trend on the right. Same month filter as the DPD
            card (apiTeamMonth); a column click on the trend selects the
            month exactly as the trend chart's rail does. Team-wide only — an
            agent selection hides both rather than showing a team figure
            under an agent's name. */}
        {!selectedAgent && (
          <>
            <Reveal className="flex flex-col [&>*]:flex-1">
              <PaymentMixCard apiMonth={apiTeamMonth} selMonth={selTeamMonth} />
            </Reveal>
            <Reveal className="flex flex-col [&>*]:flex-1">
              <CashTrendCard
                apiMonth={apiTeamMonth}
                onMonthClick={(ym) => {
                  const lbl = monthLabel(ym);
                  setSelTeamMonth((prev) => (prev === lbl ? null : lbl));
                }}
              />
            </Reveal>
          </>
        )}
        {/* Duty / leave — the roster, LAST in the grid (2026-09-18: moved from
            the second row to sit below the money rows and just above the AI
            report, so the page reads money first, people second). */}
        {selectedAgent && agentCalendar ? (
          // One card for the third slot, so it spans the row rather than
          // leaving the cell beside it empty.
          <Reveal className="lg:col-span-2 flex flex-col [&>*]:flex-1">
            <DutyCalendarCard
              cal={agentCalendar}
              loading={agentDataLoading}
              jumpToMonth={selAgentMonth ? months.find((m) => monthLabel(m) === selAgentMonth) : undefined}
            />
          </Reveal>
        ) : (
          // Two cards, so they fill the second row: duty on the left, leave
          // summary on the right.
          <>
            <Reveal className="flex flex-col [&>*]:flex-1"><AgencyDutyOverview months={months} /></Reveal>
            <Reveal className="flex flex-col [&>*]:flex-1">
              <TeamLeaveSummaryCard
                months={months}
                selTeamMonth={selTeamMonth}
                todayOnDuty={analytics.leave_summary.by_type["ON_DUTY"] ?? 0}
                totalAgents={(analytics.leave_summary.by_type["ON_DUTY"] ?? 0) + (analytics.leave_summary.by_type["OFF_DUTY"] ?? 0)}
              />
            </Reveal>
          </>
        )}
      </div>

      {/* AI Monthly Report */}
      <Reveal>
      <MonthlyReportSection
        months={months}
        selectedAgent={selectedAgent}
        preSelectedMonth={
          selectedAgent
            ? (selAgentMonth ? months.find((m) => monthLabel(m) === selAgentMonth) : undefined)
            : (selTeamMonth  ? months.find((m) => monthLabel(m) === selTeamMonth)  : undefined)
        }
      />
      </Reveal>
    </div>
  );
}

// ── DPD Breakdown Card (team or agent) ────────────────────────────────────────

type DPDEntry = { bucket: string; case_count: number; target_lakhs: number; collected_lakhs: number; collection_rate_pct: number };

// ── Recovery: what is collectable now, and what the scorecard estimates ──────
//
// TWO figures per band, side by side, because they rank the bands differently
// and only showing one points a team at the wrong pile.
//
//   Current expected (legend label since 2026-09-16; was "Arrears + penalties")
//   = overdue_amount + penal_charges. A LEDGER FACT: the part of the balance
//   already missed, and therefore collectable now.
//   90-day recovery estimate = rate_90 x TOTAL OUTSTANDING. A scorecard output
//   that includes principal not yet due — on the 2026-08-24 book it came to 229%
//   of the arrears.
//
// This pair was labelled "Due now" until 2026-08-27, when that phrase was
// retired product-wide for overstating what the number licenses. The FIGURE
// stayed here, unlike on the case list where it was removed outright: on a case
// row a large rupee total is read as an instruction to collect, whereas on a
// summary card beside its own estimate it is read as what it is. Removing it
// here would have left the estimate standing alone with no fact to scale it,
// which is the worse failure.
//
// On that same book HIGH led the estimate (Rs 10.36 Cr vs Rs 8.98 Cr) while
// MEDIUM led on collectable money (Rs 3.72 Cr vs Rs 2.16 Cr): HIGH loans are
// secured, long-tenor and barely in arrears, so their recovery is real but slow
// and mostly not yet askable. That inversion is the reason both bars are here.
//
// Colour is a deliberate hierarchy rather than two equal series: the fact is
// solid and blue, the estimate is recessive grey, because the estimate must not
// be read as a collections target. Both bars carry a direct value label, so
// identity never rests on colour alone.
//
// No derived figure is computed here — no rate x overdue, no min(estimate, due).
// A fact plus a graded likelihood, and nothing invented in between.

function RecoveryBreakdownCard({ rows, summary, loading, barReady }: {
  rows: RecoveryBreakdown[];
  summary: AnalyticsData["recovery_summary"] | undefined;
  loading: boolean;
  barReady: boolean;
}) {
  const BAND_LABEL: Record<string, string> = {
    HIGH: "High recovery",
    MEDIUM: "Medium recovery",
    LOW: "Low recovery",
  };
  const ARREARS_COLOUR = "#1677FF";
  const EST_COLOUR = "#94A3B8";

  const scale = Math.max(
    1,
    ...rows.map((r) => Math.max(r.arrears_and_penal || 0, r.expected_recoverable_amount || 0)),
  );
  const money = (n: number) =>
    shortMoney(n);

  const arrearsTotal = rows.reduce((a, r) => a + (r.arrears_and_penal || 0), 0);
  const estTotal = rows.reduce((a, r) => a + (r.expected_recoverable_amount || 0), 0);

  return (
    <div className="card p-4">
      <h2 className="text-sm font-bold mb-1" style={{ color: "#1C1C1F" }}>Recovery outlook</h2>
      {/* Says "not filtered by month" out loud, because the card next to it IS.
          Recovery is a CURRENT POSITION — what is owed today and what the
          scorecard expects back over the next 90 days — so a month selector has
          nothing to filter. Sitting beside the DPD card, which relabels itself
          "May payments" when a month is picked, silence here reads as a bug. */}
      <p className="text-xs mb-3" style={{ color: "#6B6D76" }}>
        Open cases · position today
        {summary && summary.unscored_cases > 0 && (
          <> · <span style={{ color: "#94a3b8" }}>{summary.unscored_cases} not scored yet</span></>
        )}
        <br />
        <span style={{ color: "#94a3b8" }}>Not filtered by the month above</span>
      </p>

      <div className="flex flex-wrap gap-x-4 gap-y-1 mb-4 text-[11px]" style={{ color: "#6B6D76" }}>
        <span className="inline-flex items-center gap-1.5">
          <i style={{ width: 10, height: 10, borderRadius: 2, background: ARREARS_COLOUR, display: "inline-block" }} />
          Current expected
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i style={{ width: 10, height: 10, borderRadius: 2, background: EST_COLOUR, display: "inline-block" }} />
          {summary?.label_horizon_days ?? 90}-day recovery estimate
        </span>
      </div>

      {loading ? (
        <div className="space-y-4">
          {[0, 1, 2].map((i) => <div key={i} className="h-10 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />)}
        </div>
      ) : rows.length === 0 || summary?.scored_cases === 0 ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>No cases scored yet.</p>
      ) : (
        <>
          <div className="space-y-4">
            {rows.map((r, di) => (
              <div
                key={r.band}
                className="row-stat -mx-2 px-2 py-1.5 rounded-xl"
                style={{ animation: `enter 380ms ${EASE} ${di * 80}ms both` }}
              >
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="font-semibold" style={{ color: "#1C1C1F" }}>
                    {BAND_LABEL[r.band] ?? r.band}
                  </span>
                  <span style={{ color: "#6B6D76" }}>{r.cases} cases</span>
                </div>

                <div className="flex items-center gap-2 mb-1">
                  <div className="flex-1 rounded-full overflow-hidden" style={{ height: 9, background: "#EFF0F4" }}>
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: barReady && !loading ? `${Math.min((r.arrears_and_penal / scale) * 100, 100)}%` : "0%",
                        background: ARREARS_COLOUR,
                        transition: `width 900ms ${di * 80}ms ${EASE}`,
                      }}
                    />
                  </div>
                  <span className="text-xs font-bold tabular-nums w-16 text-right" style={{ color: "#1C1C1F" }}>
                    {money(r.arrears_and_penal)}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <div className="flex-1 rounded-full overflow-hidden" style={{ height: 9, background: "#EFF0F4" }}>
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: barReady && !loading
                          ? `${Math.min((r.expected_recoverable_amount / scale) * 100, 100)}%` : "0%",
                        background: EST_COLOUR,
                        transition: `width 900ms ${di * 80 + 120}ms ${EASE}`,
                      }}
                    />
                  </div>
                  <span className="text-xs tabular-nums w-16 text-right" style={{ color: "#6B6D76" }}>
                    {money(r.expected_recoverable_amount)}
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-4 pt-3 text-xs" style={{ borderTop: "1px dashed #EAEBEF", color: "#6B6D76" }}>
            <p className="mb-1">
              <span className="font-bold" style={{ color: "#1C1C1F" }}>{money(summary?.arrears_and_penal ?? arrearsTotal)}</span>
              {" "}currently expected across the open book — arrears and penalties on instalments already missed.
            </p>
            <p className="mb-0">
              <span className="font-semibold">{money(estTotal)}</span>
              {" "}is the {summary?.label_horizon_days ?? 90}-day recovery estimate
              {" "}<span style={{ color: "#94a3b8" }}>
                — of total outstanding, includes principal not yet due. Scorecard
                estimate, not a model prediction, and not a collections target.
              </span>
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function DPDBreakdownCard({ rows, loading, barReady, agentName, selMonth }: {
  rows: DPDEntry[];
  loading: boolean;
  barReady: boolean;
  agentName?: string;
  selMonth?: string | null;
}) {
  const BUCKET_ORDER = ["BUCKET_2", "BUCKET_3", "NPA", "BUCKET_1"];
  const sorted = [...rows].sort((a, b) => BUCKET_ORDER.indexOf(a.bucket) - BUCKET_ORDER.indexOf(b.bucket));

  return (
    <div className="card p-4">
      <h2 className="text-sm font-bold mb-1" style={{ color: "#1C1C1F" }}>Collection by DPD Bucket</h2>
      {agentName
        ? <p className="text-xs mb-4" style={{ color: "#6B6D76" }}>
            <span className="font-semibold text-brand-600">{agentName}</span>
            {selMonth ? <> · <span className="font-semibold" style={{ color: "#1677FF" }}>{selMonth}</span></> : " · all months"}
          </p>
        : <p className="text-xs mb-4" style={{ color: "#6B6D76" }}>
            Agency-wide
            {selMonth ? <> · <span className="font-semibold" style={{ color: "#1677FF" }}>{selMonth}</span> payments</> : " · click a month above to filter"}
          </p>
      }
      {loading ? (
        <div className="space-y-4">
          {[0,1,2,3].map((i) => <div key={i} className="h-8 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />)}
        </div>
      ) : sorted.length === 0 ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>No case data available.</p>
      ) : (
        <div className="space-y-4">
          {sorted.map((d, di) => {
            const barCol = d.collection_rate_pct >= 60 ? "#16a34a" : d.collection_rate_pct >= 35 ? "#d97706" : "#dc2626";
            return (
              // The negative margin pays for the hover padding, so the bar
              // spans the same width it did before the row became hoverable.
              <div
                key={d.bucket}
                className="row-stat -mx-2 px-2 py-1.5 rounded-xl"
                style={{ animation: `enter 380ms ${EASE} ${di * 80}ms both` }}
              >
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="font-semibold" style={{ color: "#1C1C1F" }}>{BUCKET_LABELS[d.bucket] ?? d.bucket}</span>
                  <div className="flex gap-3 text-right">
                    <span style={{ color: "#6B6D76" }}>{d.case_count} cases</span>
                    <span className="font-semibold" style={{ color: "#1C1C1F" }}>₹{d.collected_lakhs.toFixed(1)}L</span>
                    <span className={`font-bold w-10 ${d.collection_rate_pct >= 60 ? "text-success-600" : d.collection_rate_pct >= 35 ? "text-warning-600" : "text-danger-600"}`}>
                      {d.collection_rate_pct.toFixed(0)}%
                    </span>
                  </div>
                </div>
                <div className="w-full rounded-full overflow-hidden" style={{ height: 9, background: "#EFF0F4" }}>
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: barReady && !loading ? `${Math.min(d.collection_rate_pct, 100)}%` : "0%",
                      background: barCol,
                      transition: `width 900ms ${di * 80}ms ${EASE}`,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Calendar grey — future days, no-data days, weekday headers and the legend
// dots that stand for them.
//
// 2026-09-02 — was #E2E8F0 for the day numbers and #CBD5E1 for the weekday
// headers: about 1.3:1 and 1.7:1 against the card. At the 9-11px these render
// at that is not "subtle", it is invisible — the whole month past today read as
// blank on screen. Slate-500 clears 4.5:1 while staying desaturated, so future
// days still recede next to the saturated on/off-duty green and red instead of
// competing with them. Both calendars read it from here so they cannot drift.

// ── Agency Duty Overview (no agent selected) ──────────────────────────────────

const DOW_TEAM = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function AgencyDutyOverview({ months }: { months: string[] }) {
  const defaultMonth = months[months.length - 1] ?? new Date().toISOString().slice(0, 7);
  const [visibleMonth, setVisibleMonth] = useState(defaultMonth);
  const [attendance, setAttendance] = useState<TeamAttendance | null>(null);

  useEffect(() => {
    if (!visibleMonth) return;
    getTeamAttendance(visibleMonth).then(setAttendance).catch(() => {});
  }, [visibleMonth]);

  const monthIdx = months.indexOf(visibleMonth);
  const canPrev  = monthIdx > 0;
  const canNext  = monthIdx < months.length - 1;

  const today = new Date().toISOString().split("T")[0];
  const [year, mo] = visibleMonth ? visibleMonth.split("-").map(Number) : [0, 0];
  const lastDay    = year ? new Date(year, mo, 0).getDate() : 0;
  const firstDow   = year ? new Date(year, mo - 1, 1).getDay() : 1;
  const padCols    = firstDow === 0 ? 0 : firstDow - 1;

  type Cell = null | { dayNum: number; dateStr: string; count: number; isFuture: boolean };
  const allCells: Cell[] = Array(padCols).fill(null);
  for (let d = 1; d <= lastDay; d++) {
    const dow = new Date(year, mo - 1, d).getDay();
    if (dow === 0) continue;
    const dateStr = `${year}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    allCells.push({ dayNum: d, dateStr, count: attendance?.by_date[dateStr] ?? 0, isFuture: dateStr > today });
  }
  while (allCells.length % 6 !== 0) allCells.push(null);
  const calRows: Cell[][] = [];
  for (let i = 0; i < allCells.length; i += 6) calRows.push(allCells.slice(i, i + 6));

  const total = attendance?.total_agents ?? 0;
  const pastCells = allCells.filter((c): c is NonNullable<Cell> => c !== null && !c.isFuture);
  const avgPct = pastCells.length > 0 && total > 0
    ? Math.round(pastCells.reduce((s, c) => s + c.count, 0) / pastCells.length / total * 100)
    : 0;

  const [y] = visibleMonth.split("-");
  const hdr = `${monthLabel(visibleMonth)} '${y.slice(2)}`;

  function countColor(count: number, isFuture: boolean) {
    if (isFuture || total === 0) return CAL_MUTED;
    const pct = count / total;
    return pct >= 0.75 ? "#16a34a" : pct >= 0.4 ? "#d97706" : "#dc2626";
  }

  return (
    <div className="card p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-brand-600" />
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Team Duty — {hdr}</h2>
        </div>
        <span className="text-xs font-bold px-2 py-0.5 rounded-full"
          style={{ background: avgPct >= 70 ? "rgba(22,163,74,0.10)" : avgPct >= 40 ? "rgba(217,119,6,0.10)" : "rgba(220,38,38,0.08)", color: avgPct >= 70 ? "#16a34a" : avgPct >= 40 ? "#d97706" : "#dc2626" }}>
          {avgPct}% avg
        </span>
      </div>

      {/* Month nav */}
      <div className="flex items-center justify-between mb-3">
        <button onClick={() => canPrev && setVisibleMonth(months[monthIdx - 1])} disabled={!canPrev}
          style={{ opacity: canPrev ? 1 : 0.25, cursor: canPrev ? "pointer" : "default", fontSize: 16, fontWeight: 600, color: "#6B6D76", lineHeight: 1, background: "none", border: "none", padding: "0 10px" }} className="tap-target">‹</button>
        <p className="text-xs font-semibold" style={{ color: "#6B6D76" }}>{hdr} · {total} agents</p>
        <button onClick={() => canNext && setVisibleMonth(months[monthIdx + 1])} disabled={!canNext}
          style={{ opacity: canNext ? 1 : 0.25, cursor: canNext ? "pointer" : "default", fontSize: 16, fontWeight: 600, color: "#6B6D76", lineHeight: 1, background: "none", border: "none", padding: "0 10px" }} className="tap-target">›</button>
      </div>

      {/* Calendar */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "2px 0" }}>
        {DOW_TEAM.map((lbl) => (
          <div key={lbl} style={{ textAlign: "center", fontSize: "var(--cal-dow)", fontWeight: 600, color: CAL_MUTED, paddingBottom: 3 }}>{lbl}</div>
        ))}
        {calRows.flatMap((row, ri) =>
          row.map((cell, ci) => {
            if (!cell) return <div key={`${ri}-${ci}`} style={{ height: "var(--cal-cell-team)" }} />;
            const col = countColor(cell.count, cell.isFuture);
            return (
              <div key={`${ri}-${ci}`}
                title={cell.isFuture ? cell.dateStr : `${cell.dateStr} · ${cell.count}/${total} on duty`}
                style={{ textAlign: "center", height: "var(--cal-cell-team)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                <p style={{ fontSize: 10, fontWeight: 600, color: col, lineHeight: 1 }}>{cell.dayNum}</p>
                {!cell.isFuture && total > 0 && (
                  <p style={{ fontSize: 8, color: col, lineHeight: 1, marginTop: 1, opacity: 0.85 }}>{cell.count}/{total}</p>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 mt-2 pt-2" style={{ borderTop: "1px solid #F3F4F6" }}>
        {[
          { label: "≥75%", color: "#16a34a" },
          { label: "40–75%", color: "#d97706" },
          { label: "<40%", color: "#dc2626" },
          { label: "Future", color: CAL_MUTED },
        ].map(({ label, color: c }) => (
          <div key={label} className="flex items-center gap-1">
            <span style={{ fontSize: 11, fontWeight: 600, color: c }}>●</span>
            <span className="text-xs" style={{ color: "#94a3b8" }}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Team Leave Summary Card ───────────────────────────────────────────────────

const LEAVE_LABELS: Record<string, { label: string; color: string }> = {
  SICK_LEAVE:    { label: "Sick Leave",    color: "#dc2626" },
  CASUAL_LEAVE:  { label: "Casual Leave",  color: "#d97706" },
  EARNED_LEAVE:  { label: "Earned Leave",  color: "#1677FF" },
  ABSENT:        { label: "Absent",        color: "#6B6D76" },
};

function TeamLeaveSummaryCard({ months, selTeamMonth, todayOnDuty, totalAgents }: {
  months: string[]; selTeamMonth: string | null; todayOnDuty: number; totalAgents: number;
}) {
  // ─── React Query, 2026-09-10 ────────────────────────────────────────────
  // The effect began with `setAttendance(null)` / `setLoadingLeave(true)`, both
  // synchronous writes inside an effect. The null-out is now a derivation: with
  // no month selected the query is disabled and reads as null on its own.
  //
  // One faithful detail worth keeping: the old code returned WITHOUT clearing
  // when the label could not be resolved to a "YYYY-MM" (`if (!apiMonth) return`
  // came after the null-out), so the previous month's figures stayed on screen.
  // `enabled: !!apiMonth` reproduces that — the query simply does not run, and
  // React Query holds the last data for the last key that did.
  const apiLeaveMonth = selTeamMonth
    ? months.find((m) => monthLabel(m) === selTeamMonth) ?? null
    : null;
  const attendanceQ = useQuery({
    queryKey: ["manager", "team", "attendance", apiLeaveMonth],
    queryFn: () => getTeamAttendance(apiLeaveMonth!),
    enabled: !!apiLeaveMonth,
    ...AS_BEFORE,
  });
  const attendance = selTeamMonth ? attendanceQ.data ?? null : null;
  const loadingLeave = attendanceQ.isFetching;

  const [y] = (selTeamMonth && months.find((m) => monthLabel(m) === selTeamMonth) || "").split("-");
  const monthLabel_ = selTeamMonth ? `${selTeamMonth} '${(y ?? "").slice(2)}` : "";

  if (!selTeamMonth) {
    return (
      <div className="card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Calendar className="w-4 h-4 text-brand-600" />
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Leave Summary</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div className="rounded-xl p-3 text-center" style={{ background: "rgba(22,163,74,0.06)", border: "1px solid rgba(22,163,74,0.15)" }}>
            <p className="text-2xl font-bold" style={{ color: "#16a34a" }}>{todayOnDuty}</p>
            <p className="text-xs font-semibold mt-0.5" style={{ color: "#16a34a" }}>On Duty Today</p>
          </div>
          <div className="rounded-xl p-3 text-center" style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.15)" }}>
            <p className="text-2xl font-bold" style={{ color: "#dc2626" }}>{Math.max(0, totalAgents - todayOnDuty)}</p>
            <p className="text-xs font-semibold mt-0.5" style={{ color: "#dc2626" }}>Off Duty Today</p>
          </div>
        </div>
        <p className="text-xs text-center" style={{ color: "#94a3b8" }}>Click a month above for leave breakdown</p>
      </div>
    );
  }

  if (loadingLeave) {
    return (
      <div className="card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Calendar className="w-4 h-4 text-brand-600" />
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Leave — {monthLabel_}</h2>
        </div>
        <div className="space-y-2">{[0,1,2].map((i) => <div key={i} className="h-6 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />)}</div>
      </div>
    );
  }

  const total = attendance?.total_leave_agent_days ?? 0;
  const workingDays = attendance?.working_days ?? 0;
  const agentCount = attendance?.total_agents ?? totalAgents;
  const possibleDays = workingDays * agentCount;
  const onDutyDays = possibleDays - total;
  const attendancePct = possibleDays > 0 ? Math.round(onDutyDays / possibleDays * 100) : 0;
  const leaveByType = attendance?.leave_by_type ?? {};
  const leaveTypes = Object.entries(leaveByType).sort((a, b) => b[1] - a[1]);

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-brand-600" />
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Leave — {monthLabel_}</h2>
        </div>
        <span className="text-xs font-bold px-2 py-0.5 rounded-full"
          style={{ background: attendancePct >= 80 ? "rgba(22,163,74,0.10)" : attendancePct >= 60 ? "rgba(217,119,6,0.10)" : "rgba(220,38,38,0.08)", color: attendancePct >= 80 ? "#16a34a" : attendancePct >= 60 ? "#d97706" : "#dc2626" }}>
          {attendancePct}% attendance
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="rounded-xl p-3 text-center" style={{ background: "rgba(22,163,74,0.06)", border: "1px solid rgba(22,163,74,0.15)" }}>
          <p className="text-xl font-bold" style={{ color: "#16a34a" }}>{workingDays}</p>
          <p className="text-xs font-semibold mt-0.5" style={{ color: "#16a34a" }}>Working Days</p>
        </div>
        <div className="rounded-xl p-3 text-center" style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.15)" }}>
          <p className="text-xl font-bold" style={{ color: "#dc2626" }}>{total}</p>
          <p className="text-xs font-semibold mt-0.5" style={{ color: "#dc2626" }}>Leave Agent-Days</p>
        </div>
      </div>

      {leaveTypes.length === 0 ? (
        <p className="text-xs text-center py-3" style={{ color: "#94a3b8" }}>No leave records for this month</p>
      ) : (
        <div className="space-y-2.5">
          {leaveTypes.map(([type, count]) => {
            const meta = LEAVE_LABELS[type] ?? { label: type, color: "#6B6D76" };
            const pct = total > 0 ? Math.round(count / total * 100) : 0;
            return (
              <div key={type} className="row-stat -mx-2 px-2 py-1 rounded-xl">
                <div className="flex justify-between text-xs mb-1">
                  <span className="font-semibold" style={{ color: "#1C1C1F" }}>{meta.label}</span>
                  <span style={{ color: "#6B6D76" }}>{count} days · {pct}%</span>
                </div>
                <div className="rounded-full overflow-hidden" style={{ height: 4, background: "#F0F1F5" }}>
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: meta.color, transition: "width 500ms ease" }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function KPICard({ label, value, sub, icon, color, deltaText, deltaPositive }: {
  label: string; value: string; sub: string; icon: React.ReactNode; color: string;
  deltaText?: string; deltaPositive?: boolean;
}) {
  const iconBg = color.includes("success") ? "bg-success-600"
    : color.includes("warning") ? "bg-warning-600"
    : color.includes("brand")   ? "bg-brand-600"
    : "bg-slate-500";
  return (
    // h-full is load-bearing. The grid stretches the animation wrapper around
    // this card, but .card declares no height, so without h-full each card is
    // only as tall as its own content — and the four KPIs do not have the same
    // content. Total Collected carries a sub-line beside its value, which grows
    // that line box by the small text's descender, and it ended up visibly
    // taller than its neighbours. Heights are the grid's job; let it do it.
    <div className="card group h-full">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: "#6B6D76" }}>{label}</p>
          {/* The sub-line belongs to the VALUE, not to the delta, and it has to
              sit on the value's line to say so.
              It used to share a baseline row with the delta, which put
              "↑34.5%" immediately before "of ₹39.3L target" and read as one
              phrase: "34.5% of ₹39.3L target". That is not merely cramped, it
              is FALSE — ₹10.5L of ₹39.3L is 26.7%, and 34.5% is the
              month-over-month change, a different number about a different
              thing. Two figures that mean nothing to each other must not be
              allowed to form a sentence.
              So: value and its qualifier on one baseline, delta alone beneath.
              items-baseline rather than items-center, so the small text sits on
              the big number's baseline instead of floating at its middle. */}
          <div className={`flex flex-wrap items-baseline gap-x-2 mt-1.5 ${color}`}>
            <span className="text-2xl font-semibold leading-none tracking-tight">{value}</span>
            {/* text-sm, not text-2xl: these cards are a quarter of the row on
                desktop and half of it on a phone, and at the value's size
                "of ₹39.3L target" wraps to its own line on every one of them —
                which is the layout this change exists to get rid of. Same
                baseline, one step down in size, and it reads as one unit. */}
            {/* leading-none on this too, so a card WITH a sub-line has exactly
                the same value-row height as one without. Left at the default
                line-height it adds a few pixels of descender to the row, which
                is the difference that made this card look off in the first
                place — h-full then hides it, but the row is still the wrong
                height and the numbers no longer share a baseline cleanly. */}
            {sub && <span className="text-sm font-medium leading-none" style={{ color: "#6B6D76" }}>{sub}</span>}
          </div>
          {deltaText && (
            <p className="text-xs font-semibold mt-1" style={{ color: deltaPositive ? "#16a34a" : "#dc2626" }}>
              {deltaText}
            </p>
          )}
        </div>
        <div className={`icon-circle flex-shrink-0 text-white ${iconBg}`} style={{ transition: "transform 200ms cubic-bezier(0.16,1,0.3,1)" }}>
          <span className="text-white [&>svg]:stroke-white">{icon}</span>
        </div>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="card animate-pulse" style={{ height: 96, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
        ))}
      </div>
      <div className="card animate-pulse" style={{ height: 320, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="card animate-pulse" style={{ height: 192, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
        <div className="card animate-pulse" style={{ height: 192, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
      </div>
    </div>
  );
}
