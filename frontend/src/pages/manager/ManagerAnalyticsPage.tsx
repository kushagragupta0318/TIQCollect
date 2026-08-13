// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-31 — Responsive pass. Densest page in the manager flow: the agent
//   pill row (up to 18 wrapping pills, ~180px tall before the chart is even
//   visible) becomes a select below lg; the 6-month selector grids become
//   scroll rails rather than squeezing "Jul '26" into 49px; chart heights and
//   calendar cells scale down on touch. See docs/frontend-guide.md.
// ─────────────────────────────────────────────────────────────────────────
import { useEffect, useState } from "react";
import { useIsBelowLg, useMediaQuery } from "@/hooks/useMediaQuery";
import { TrendingUp, BarChart2, IndianRupee, Users, Calendar, X, Brain, Loader2 } from "lucide-react";
import { toast } from "react-hot-toast";
import {
  AreaChart, Area, LineChart as RLineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import {
  getAnalytics, getAgentsPerformance,
  getManagerAgentCalendar, getAgentDPDBreakdown, getTeamDPDBreakdown, getTeamAttendance, getMonthlyReport,
} from "@/api/manager";
import type {
  AnalyticsData, AgentsPerformanceData, AgentPerfEntry, AgentMonthlyPerf,
  AgentAvailabilityCalendar, AgentCalendarDay, AgentDPDRow, TeamAttendance,
} from "@/api/manager";
import { TierBadge } from "@/components/ui/Badge";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

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

function yFmt(v: number) {
  if (v >= 100000) return `${(v / 100000).toFixed(1)}L`;
  if (v >= 1000)   return `${(v / 1000).toFixed(0)}K`;
  return String(v);
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
          { label: "collected", value: collected >= 100000 ? `₹${(collected/100000).toFixed(1)}L` : `₹${(collected/1000).toFixed(0)}K`, colorVal: "#1C1C1F", delta: collectDelta },
          { label: "visits",    value: String(visits),           colorVal: "#1C1C1F",        delta: visitsDelta  },
          { label: "PTP conv.", value: `${ptpRate}%`,            colorVal: rateCol(ptpRate), delta: ptpDelta     },
        ] as { label: string; value: string; colorVal: string; delta: number | null }[]).map(({ label, value, colorVal, delta }) => (
          <div key={label} className="flex flex-col items-center rounded-xl px-2.5 sm:px-3 py-1.5 flex-1 sm:flex-none"
            style={{ background: "#fff", border: "1px solid #EAEBEF", minWidth: 60 }}>
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

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ManagerAnalyticsPage() {
  const [analytics, setAnalytics]   = useState<AnalyticsData | null>(null);
  const [agentPerf, setAgentPerf]   = useState<AgentsPerformanceData | null>(null);
  const [loading, setLoading]       = useState(true);
  const [chartMode, setChartMode]   = useState<"team" | "individual">("team");
  const [barReady, setBarReady]     = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<AgentPerfEntry | null>(null);
  // Behavioural switches only — pill row becomes a select, copy changes from
  // "Click"/"Hover" to "Tap"/"Pick". Layout itself is done with Tailwind.
  const isBelowLg = useIsBelowLg();
  const [selTeamMonth, setSelTeamMonth]   = useState<string | null>(null);
  const [selAgentMonth, setSelAgentMonth] = useState<string | null>(null);  // month label e.g. "Apr"
  const [agentCalendar, setAgentCalendar]   = useState<AgentAvailabilityCalendar | null>(null);
  const [agentDpdRows, setAgentDpdRows]     = useState<AgentDPDRow[] | null>(null);
  const [teamDpdRows, setTeamDpdRows]       = useState<AgentDPDRow[] | null>(null);
  const [agentDataLoading, setAgentDataLoading] = useState(false);

  useEffect(() => {
    Promise.all([getAnalytics(), getAgentsPerformance(6)])
      .then(([a, p]) => { setAnalytics(a); setAgentPerf(p); })
      .catch(() => toast.error("Failed to load analytics"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setBarReady(false);
    if (!loading && analytics) {
      const t = setTimeout(() => setBarReady(true), 160);
      return () => clearTimeout(t);
    }
  }, [loading, analytics]);

  useEffect(() => {
    if (!selectedAgent) { setAgentCalendar(null); setAgentDpdRows(null); return; }
    setAgentDataLoading(true);
    // Resolve selAgentMonth label (e.g. "Apr") back to YYYY-MM for API call
    const agentMonths = agentPerf?.months ?? [];
    const apiMonth = selAgentMonth
      ? agentMonths.find((m) => monthLabel(m) === selAgentMonth)
      : undefined;
    Promise.all([
      getManagerAgentCalendar(selectedAgent.agent_id),
      getAgentDPDBreakdown(selectedAgent.agent_id, apiMonth),
    ])
      .then(([cal, dpd]) => { setAgentCalendar(cal); setAgentDpdRows(dpd); })
      .catch(() => toast.error("Could not load agent details"))
      .finally(() => setAgentDataLoading(false));
  }, [selectedAgent?.agent_id, selAgentMonth]);

  useEffect(() => {
    if (selectedAgent || !selTeamMonth || !analytics) { setTeamDpdRows(null); return; }
    // reverse-lookup YYYY-MM from the same monthly_trend used to set selTeamMonth
    const apiMonth = analytics.monthly_trend.find((m) => monthLabel(m.month) === selTeamMonth)?.month;
    getTeamDPDBreakdown(apiMonth)
      .then(setTeamDpdRows)
      .catch(() => toast.error("Could not load team DPD breakdown"));
  }, [selTeamMonth, selectedAgent, analytics]);

  if (loading) return <LoadingSkeleton />;
  if (!analytics || !agentPerf) return <p className="text-sm p-6" style={{ color: "#6B6D76" }}>No data.</p>;

  const { kpis, monthly_trend, dpd_breakdown } = analytics;
  const { months, agents } = agentPerf;

  // Window totals — the sum of every month on this page, so the unselected
  // KPI cards agree with the trend chart beneath them.
  //
  // Deliberately NOT kpis.total_collected_lakhs / total_target_lakhs /
  // overall_collection_rate_pct: that block is a portfolio snapshot (current
  // collected vs target across cases) and measures something different, so it
  // read far below the months on show — 214.1L against 1572.0L summed, and
  // 19.1% against 43.9%. Scoped to this page; the backend is untouched and
  // every other consumer of `kpis` keeps its existing figures.
  const windowCollectedLakhs = monthly_trend.reduce((t, m) => t + m.collected_lakhs, 0);
  const windowTargetLakhs    = monthly_trend.reduce((t, m) => t + m.target_lakhs, 0);
  const windowRatePct        = windowTargetLakhs > 0 ? (windowCollectedLakhs / windowTargetLakhs) * 100 : 0;

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

  // PTP per month — derived from per-agent monthly data since monthly_trend has no PTP fields
  function teamPTPRate(monthYM: string | null | undefined) {
    if (!monthYM) return null;
    const set     = agents.reduce((s, a) => s + (a.monthly.find((m) => m.month === monthYM)?.ptps_set     ?? 0), 0);
    const honored = agents.reduce((s, a) => s + (a.monthly.find((m) => m.month === monthYM)?.ptps_honored ?? 0), 0);
    return set > 0 ? +(honored / set * 100).toFixed(1) : 0;
  }
  const teamPTPSel  = selTeamTrend  ? teamPTPRate(selTeamTrend.month)  : null;
  const teamPTPPrev = prevTeamTrend ? teamPTPRate(prevTeamTrend.month) : null;
  const teamPTPDelta = teamPTPSel !== null && teamPTPPrev !== null ? +(teamPTPSel - teamPTPPrev).toFixed(1) : null;

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
      <div style={{ animation: `enter 420ms ${EASE} 0ms both` }}>
        <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>ABC Collections</h1>
        <p className="text-[13px] sm:text-sm mt-0.5" style={{ color: "#6B6D76" }}>6-month collection performance, DPD breakdown, and agent rankings</p>
      </div>

      {/* KPI row — updates dynamically when a team month is selected */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {[
          {
            label: "Collection Rate",
            value: selTeamTrend ? `${selTeamTrend.collection_rate_pct.toFixed(1)}%` : `${windowRatePct.toFixed(1)}%`,
            sub: selTeamTrend ? "" : "across all months",
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
            label: "PTP Conversion Rate",
            value: teamPTPSel !== null ? `${teamPTPSel}%` : `${kpis.ptp_conversion_rate_pct.toFixed(1)}%`,
            sub: teamPTPSel !== null ? "" : "PTPs honored",
            delta: teamPTPDelta, deltaSuffix: "%",
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
      <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 240ms both` }}>
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
      </div>

      {/* DPD + Duty Calendar row — context-aware */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5" style={{ animation: `enter 420ms ${EASE} 300ms both` }}>
        <DPDBreakdownCard
          rows={selectedAgent ? (agentDpdRows ?? dpd_breakdown) : (teamDpdRows ?? dpd_breakdown)}
          loading={agentDataLoading}
          barReady={barReady}
          agentName={selectedAgent?.agent_name}
          selMonth={!selectedAgent ? selTeamMonth : selAgentMonth}
        />
        {selectedAgent && agentCalendar ? (
          <DutyCalendarCard
            cal={agentCalendar}
            loading={agentDataLoading}
            jumpToMonth={selAgentMonth ? months.find((m) => monthLabel(m) === selAgentMonth) : undefined}
          />
        ) : (
          <div className="space-y-4">
            <AgencyDutyOverview months={months} />
            <TeamLeaveSummaryCard
              months={months}
              selTeamMonth={selTeamMonth}
              todayOnDuty={analytics.leave_summary.by_type["ON_DUTY"] ?? 0}
              totalAgents={(analytics.leave_summary.by_type["ON_DUTY"] ?? 0) + (analytics.leave_summary.by_type["OFF_DUTY"] ?? 0)}
            />
          </div>
        )}
      </div>

      {/* AI Monthly Report */}
      <MonthlyReportSection
        months={months}
        selectedAgent={selectedAgent}
        preSelectedMonth={
          selectedAgent
            ? (selAgentMonth ? months.find((m) => monthLabel(m) === selAgentMonth) : undefined)
            : (selTeamMonth  ? months.find((m) => monthLabel(m) === selTeamMonth)  : undefined)
        }
      />
    </div>
  );
}

// ── DPD Breakdown Card (team or agent) ────────────────────────────────────────

type DPDEntry = { bucket: string; case_count: number; target_lakhs: number; collected_lakhs: number; collection_rate_pct: number };

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
              <div key={d.bucket} style={{ animation: `enter 380ms ${EASE} ${di * 80}ms both` }}>
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

// ── Agent Duty Calendar — proper month calendar with navigation ────────────────

function DutyCalendarCard({ cal, loading, jumpToMonth }: { cal: AgentAvailabilityCalendar; loading: boolean; jumpToMonth?: string }) {
  const monthsAvailable = [...new Set(cal.calendar.map((d) => d.date.slice(0, 7)))].sort();
  const [visibleMonth, setVisibleMonth] = useState(
    monthsAvailable[monthsAvailable.length - 1] ?? ""
  );

  useEffect(() => {
    if (!jumpToMonth) return;
    const available = [...new Set(cal.calendar.map((d) => d.date.slice(0, 7)))].sort();
    if (available.includes(jumpToMonth)) setVisibleMonth(jumpToMonth);
  }, [jumpToMonth, cal]);

  const monthIdx   = monthsAvailable.indexOf(visibleMonth);
  const canPrev    = monthIdx > 0;
  const canNext    = monthIdx < monthsAvailable.length - 1;
  const isOn       = cal.current_status === "ON_DUTY";

  const dayMap = new Map(
    cal.calendar.filter((d) => d.date.startsWith(visibleMonth)).map((d) => [d.date, d])
  );

  const today = new Date().toISOString().split("T")[0];
  const [year, month] = visibleMonth ? visibleMonth.split("-").map(Number) : [0, 0];
  const lastDay = year ? new Date(year, month, 0).getDate() : 0;
  const firstDow = year ? new Date(year, month - 1, 1).getDay() : 1; // 0=Sun
  const padCols = firstDow === 0 ? 0 : firstDow - 1;

  type Cell = null | { dayNum: number; dateStr: string; data: AgentCalendarDay | null; isFuture: boolean };

  const allCells: Cell[] = Array(padCols).fill(null);
  for (let d = 1; d <= lastDay; d++) {
    const dow = new Date(year, month - 1, d).getDay();
    if (dow === 0) continue;
    const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    allCells.push({ dayNum: d, dateStr, data: dayMap.get(dateStr) ?? null, isFuture: dateStr > today });
  }
  while (allCells.length % 6 !== 0) allCells.push(null);
  const calRows: Cell[][] = [];
  for (let i = 0; i < allCells.length; i += 6) calRows.push(allCells.slice(i, i + 6));

  // Attendance: count from actual rendered cells — on = has beat data, off = past working day with no data
  const pastCells = allCells.filter((c): c is NonNullable<Cell> => c !== null && !c.isFuture);
  const onDutyDays    = pastCells.filter((c) => c.data !== null).length;
  const offDutyDays   = pastCells.filter((c) => c.data === null).length;
  const attendancePct = pastCells.length > 0 ? Math.round(onDutyDays / pastCells.length * 100) : 0;

  const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const visibleLabel = visibleMonth
    ? new Date(visibleMonth + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric" })
    : "";

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-brand-600" />
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Duty Calendar</h2>
        </div>
        <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{ background: isOn ? "rgba(22,163,74,0.10)" : "rgba(220,38,38,0.10)", color: isOn ? "#16a34a" : "#dc2626" }}>
          {isOn ? "● On Duty" : "○ Off Duty"}
        </span>
      </div>
      <p className="text-xs mb-4" style={{ color: "#6B6D76" }}>
        <span className="font-semibold" style={{ color: "#1677FF" }}>{cal.agent_name}</span>
        {" · "}{attendancePct}% attendance · {onDutyDays} on / {offDutyDays} off
      </p>

      {loading ? (
        <div className="h-48 rounded-xl animate-pulse" style={{ background: "#EFF0F4" }} />
      ) : (
        <>
          {/* Month navigator */}
          <div className="flex items-center justify-between mb-3">
            <button
              onClick={() => canPrev && setVisibleMonth(monthsAvailable[monthIdx - 1])}
              disabled={!canPrev}
              style={{
                width: 28, height: 28, borderRadius: 8, border: "1px solid #EAEBEF",
                background: canPrev ? "#F5F6F9" : "transparent",
                color: canPrev ? "#1C1C1F" : "#D1D5DB",
                cursor: canPrev ? "pointer" : "default",
                fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center",
                fontWeight: 600,
              }}
            >‹</button>
            <p className="text-sm font-semibold" style={{ color: "#1C1C1F" }}>{visibleLabel}</p>
            <button
              onClick={() => canNext && setVisibleMonth(monthsAvailable[monthIdx + 1])}
              disabled={!canNext}
              style={{
                width: 28, height: 28, borderRadius: 8, border: "1px solid #EAEBEF",
                background: canNext ? "#F5F6F9" : "transparent",
                color: canNext ? "#1C1C1F" : "#D1D5DB",
                cursor: canNext ? "pointer" : "default",
                fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center",
                fontWeight: 600,
              }}
            >›</button>
          </div>

          {/* Calendar grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "2px 0" }}>
            {DOW_LABELS.map((lbl) => (
              <div key={lbl} style={{ textAlign: "center", fontSize: "var(--cal-dow)", fontWeight: 600, color: "#CBD5E1", paddingBottom: 3 }}>
                {lbl}
              </div>
            ))}
            {calRows.flatMap((row, ri) =>
              row.map((cell, ci) => {
                if (!cell) return <div key={`${ri}-${ci}`} style={{ height: "var(--cal-cell-agent)" }} />;
                const hasData = !!cell.data;
                const numColor = cell.isFuture ? "#E2E8F0" : hasData ? "#16a34a" : "#ef4444";

                return (
                  <div
                    key={`${ri}-${ci}`}
                    title={
                      cell.isFuture ? cell.dateStr
                      : hasData     ? `${cell.dateStr} · On Duty · ${cell.data!.cases} cases`
                      :               `${cell.dateStr} · Off Duty`
                    }
                    style={{ textAlign: "center", height: "var(--cal-cell-agent)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}
                  >
                    <p style={{ fontSize: 11, fontWeight: 600, color: numColor, lineHeight: 1 }}>
                      {cell.dayNum}
                    </p>
                  </div>
                );
              })
            )}
          </div>

          {/* Legend */}
          <div className="flex items-center gap-3 mt-2 pt-2" style={{ borderTop: "1px solid #F3F4F6" }}>
            {[
              { label: "On Duty",  color: "#16a34a" },
              { label: "Off Duty", color: "#ef4444" },
              { label: "Upcoming", color: "#CBD5E1" },
            ].map(({ label, color: c }) => (
              <div key={label} className="flex items-center gap-1">
                <span style={{ fontSize: 11, fontWeight: 600, color: c }}>●</span>
                <span className="text-xs" style={{ color: "#94a3b8" }}>{label}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

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
    if (isFuture || total === 0) return "#E2E8F0";
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
          <div key={lbl} style={{ textAlign: "center", fontSize: "var(--cal-dow)", fontWeight: 600, color: "#CBD5E1", paddingBottom: 3 }}>{lbl}</div>
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
          { label: "Future", color: "#CBD5E1" },
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
  const [attendance, setAttendance] = useState<TeamAttendance | null>(null);
  const [loadingLeave, setLoadingLeave] = useState(false);

  useEffect(() => {
    if (!selTeamMonth) { setAttendance(null); return; }
    const apiMonth = months.find((m) => monthLabel(m) === selTeamMonth);
    if (!apiMonth) return;
    setLoadingLeave(true);
    getTeamAttendance(apiMonth)
      .then(setAttendance)
      .catch(() => {})
      .finally(() => setLoadingLeave(false));
  }, [selTeamMonth]);

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
              <div key={type}>
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

// ── AI Monthly Report Section ─────────────────────────────────────────────────

function MonthlyReportSection({ months, selectedAgent, preSelectedMonth }: { months: string[]; selectedAgent: AgentPerfEntry | null; preSelectedMonth?: string }) {
  const defaultMonth = months[months.length - 2] ?? months[months.length - 1] ?? "";
  const [month, setMonth] = useState(defaultMonth);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<{ text: string; month: string; scope: string } | null>(null);

  useEffect(() => { setReport(null); }, [selectedAgent?.agent_id]);
  useEffect(() => {
    if (preSelectedMonth && months.includes(preSelectedMonth)) {
      setMonth(preSelectedMonth);
      setReport(null);
    }
  }, [preSelectedMonth]);

  async function generate() {
    setLoading(true);
    try {
      const data = await getMonthlyReport(month, selectedAgent?.agent_id);
      setReport({ text: data.report_text, month: data.month, scope: data.scope });
    } catch {
      toast.error("Could not generate report");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 360ms both` }}>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: "#EFF6FF" }}>
          <Brain className="w-4 h-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>AI Monthly Performance Report</h2>
          <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>
            {selectedAgent ? `Scoped to ${selectedAgent.agent_name}` : "Agency-level summary"}
            {" · "}60–100 word AI performance brief
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0 w-full sm:w-auto">
          <select
            value={month}
            onChange={(e) => { setMonth(e.target.value); setReport(null); }}
            aria-label="Report month"
            className="tap-target-h text-xs rounded-xl px-3 py-1.5 font-semibold flex-1 sm:flex-none min-w-0"
            style={{ border: "1px solid #EAEBEF", color: "#1C1C1F", background: "#F5F6F9", outline: "none" }}
          >
            {months.map((m) => (
              <option key={m} value={m}>
                {new Date(m + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric" })}
              </option>
            ))}
          </select>
          <button
            onClick={generate}
            disabled={loading}
            className="tap-target flex flex-shrink-0 items-center justify-center gap-1.5 rounded-control border border-primary bg-white px-4 py-1.5 text-xs font-semibold text-primary transition-opacity hover:bg-brand-100"
            style={{ opacity: loading ? 0.7 : 1 }}
          >
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Brain className="w-3 h-3" />}
            Generate Report
          </button>
        </div>
      </div>

      {!report && !loading && (
        <div className="rounded-xl py-10 text-center" style={{ background: "#F5F6F9", border: "1.5px dashed #DDDFE8" }}>
          <Brain className="w-6 h-6 mx-auto mb-2" style={{ color: "#C4C6CF" }} />
          <p className="text-sm" style={{ color: "#94a3b8" }}>Choose a month and click Generate to get an AI summary</p>
        </div>
      )}

      {loading && (
        <div className="rounded-xl py-10 text-center"
          style={{ background: "#F7F8FA", border: "1px solid #ECEDF1" }}>
          <Loader2 className="w-5 h-5 mx-auto mb-2 animate-spin" style={{ color: "#7c3aed" }} />
          <p className="text-sm font-medium" style={{ color: "#7c3aed" }}>Analysing performance data…</p>
        </div>
      )}

      {report && !loading && (
        <div className="rounded-xl p-5" style={{ background: "#F7F8FA", border: "1px solid #ECEDF1" }}>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
              style={{ background: "rgba(124,58,237,0.10)", color: "#7c3aed" }}>
              {new Date(report.month + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric" })}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
              style={{ background: "#F5F6F9", color: "#6B6D76" }}>
              {report.scope}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full font-semibold ml-auto"
              style={{ background: "rgba(124,58,237,0.06)", color: "#9333ea" }}>
              GPT-4o-mini
            </span>
          </div>
          <p className="text-sm leading-loose" style={{ color: "#1f2937", whiteSpace: "pre-line" }}>
            {report.text}
          </p>
          <p className="text-xs mt-3 pt-3" style={{ color: "#9ca3af", borderTop: "1px solid rgba(124,58,237,0.08)" }}>
            Eagle-view AI brief · Based on live performance data · For internal use only
          </p>
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
    <div className="card group">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: "#6B6D76" }}>{label}</p>
          <p className={`text-2xl font-semibold mt-1.5 leading-none tracking-tight ${color}`}>{value}</p>
          {/* Delta and sub-line together, not either/or. The old ternary meant
              any month with a month-over-month delta dropped its sub-line, so
              "of ₹NNNL target" showed up only on the first month of the window
              — the one with no previous month to compare against.
              flex-wrap + items-baseline: they sit on one baseline while they
              fit, and the sub-line drops to its own line on a narrow card
              instead of being clipped. */}
          {(deltaText || sub) && (
            <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 mt-1">
              {deltaText && (
                <span className="text-xs font-semibold" style={{ color: deltaPositive ? "#16a34a" : "#dc2626" }}>
                  {deltaText}
                </span>
              )}
              {sub && <span className="text-xs" style={{ color: "#6B6D76" }}>{sub}</span>}
            </div>
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
