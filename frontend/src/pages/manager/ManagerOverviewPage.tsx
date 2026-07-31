// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-15 — SOS banner "Respond" button previously called toast.success()
//   with no `toast` import in this file (a real bug — clicking it threw a
//   ReferenceError, doing nothing) and claimed "emergency services notified"
//   regardless. Now navigates to /manager/agents, where each SOS'd agent has
//   a real acknowledge action (sends them a real SMS/WhatsApp). Full detail:
//   /changelog.md.
// ─────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Users, Briefcase, IndianRupee, CheckCircle, Clock, AlertTriangle, Sparkles, RefreshCw, TrendingUp, TrendingDown } from "lucide-react";
import { getDashboard, getAgents, getBriefing } from "@/api/manager";
import type { BriefingData } from "@/api/manager";
import { StatCard } from "@/components/ui/Card";
import { TierBadge } from "@/components/ui/Badge";
import type { DashboardSummary, Agent } from "@/types";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

const DPD_BUCKET_CONFIG: Record<string, { bar: string; shadow: string; label: string }> = {
  CURRENT:  { bar: "#22c55e", shadow: "rgba(34,197,94,0.25)",   label: "CURRENT (0 DPD)"  },
  BUCKET_1: { bar: "#f59e0b", shadow: "rgba(245,158,11,0.25)",  label: "BUCKET 1 (1-30)"  },
  BUCKET_2: { bar: "#f97316", shadow: "rgba(249,115,22,0.25)",  label: "BUCKET 2 (31-60)" },
  BUCKET_3: { bar: "#ef4444", shadow: "rgba(239,68,68,0.25)",   label: "BUCKET 3 (61-90)" },
  NPA:      { bar: "#7c3aed", shadow: "rgba(124,58,237,0.25)",  label: "NPA (90+)"         },
};

export default function ManagerOverviewPage() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [animated, setAnimated]     = useState(false);
  const [barReady, setBarReady]     = useState(false);
  const [briefing, setBriefing]     = useState<BriefingData | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(true);

  const load = useCallback(() => {
    return Promise.all([getDashboard(), getAgents()])
      .then(([s, a]) => {
        setSummary(s);
        const onDuty = a
          .filter(ag => ag.status === "ON_DUTY")
          .sort((x, y) => y.today_collected - x.today_collected);
        setAgents(onDuty.slice(0, 10));
      })
      .catch(() => {});
  }, []);

  const loadBriefing = useCallback((refresh = false) => {
    setBriefingLoading(true);
    return getBriefing(refresh)
      .then(b => setBriefing(b))
      .catch(() => {})
      .finally(() => setBriefingLoading(false));
  }, []);

  useEffect(() => {
    load().finally(() => setLoading(false));
    loadBriefing();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load, loadBriefing]);

  // Stagger: trigger bar animations after data loads
  useEffect(() => {
    if (agents.length > 0) {
      const t = setTimeout(() => setAnimated(true), 120);
      return () => clearTimeout(t);
    }
  }, [agents]);

  useEffect(() => {
    if (!loading) {
      const t = setTimeout(() => setBarReady(true), 160);
      return () => clearTimeout(t);
    }
  }, [loading]);

  const sosCount = summary?.sos_active_count ?? 0;

  if (loading) return <LoadingGrid />;

  const s = summary!;
  const collectionPct = Math.round(s.collection_rate_today);

  return (
    <div className="space-y-5">
      {/* SOS Alert */}
      {sosCount > 0 && (
        <div
          className="flex items-center gap-3 p-4 animate-pulse"
          style={{
            background: "#DC2626",
            borderRadius: "22px",
            color: "#fff",
          }}
        >
          <AlertTriangle className="w-6 h-6 flex-shrink-0" />
          <div className="flex-1">
            <p className="font-bold">{sosCount} Agent SOS Alert{sosCount > 1 ? "s" : ""} Active!</p>
            <p className="text-sm" style={{ color: "rgba(255,255,255,0.75)" }}>
              Immediate attention required — check Agents page
            </p>
          </div>
          <button
            onClick={() => navigate("/manager/agents")}
            className="px-3 py-1.5 rounded-xl text-sm font-semibold transition-colors"
            style={{ background: "#fff", color: "#DC2626" }}
          >
            Respond
          </button>
        </div>
      )}

      {/* KPI Grid — staggered entrance */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "Agents On Duty",  value: `${s.agents_on_duty}/${s.total_agents}`, icon: <Users className="w-5 h-5" />,       colorClass: "text-brand-600",   subtext: "active today" },
          { label: "Cases Today",     value: s.cases_today,                            icon: <Briefcase className="w-5 h-5" />,   colorClass: "text-brand-600",   subtext: `${s.visits_today} visits done` },
          { label: "Resolved Today",  value: s.cases_resolved_today,                   icon: <CheckCircle className="w-5 h-5" />, colorClass: "text-success-600", subtext: "cases closed" },
          { label: "PTPs Due",        value: s.ptps_due_today,                         icon: <Clock className="w-5 h-5" />,       colorClass: s.ptps_due_today > 5 ? "text-warning-600" : "text-slate-500", subtext: "today" },
        ].map((item, i) => (
          <div
            key={item.label}
            style={{ animation: `enter 420ms ${EASE} ${i * 60}ms both` }}
          >
            <StatCard {...item} />
          </div>
        ))}
      </div>

      {/* Collection progress */}
      <div
        className="card p-6"
        style={{ animation: `enter 420ms ${EASE} 240ms both` }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <div className="icon-circle bg-success-600" style={{ width: 36, height: 36 }}>
              <IndianRupee className="w-4 h-4 text-white" />
            </div>
            <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Today's Collections</h2>
          </div>
          <div className="text-right">
            <p className="text-2xl font-semibold text-success-600 tracking-tight">₹{(s.amount_collected_today / 100000).toFixed(1)}L</p>
            <p className="text-xs" style={{ color: "#6B6D76" }}>of ₹{(s.amount_target_today / 100000).toFixed(1)}L target</p>
          </div>
        </div>

        {/* Animated pill progress bar */}
        <div className="w-full rounded-full overflow-hidden" style={{ height: 12, background: "#EFF0F4" }}>
          <div
            className="h-full rounded-full"
            style={{
              width: barReady ? `${Math.min(collectionPct, 100)}%` : "0%",
              background: "linear-gradient(90deg, #22c55e, #16a34a)",
              boxShadow: "0 2px 8px rgba(22,163,74,0.35)",
              transition: `width 900ms ${EASE}`,
            }}
          />
        </div>
        <div className="flex justify-between text-sm mt-2">
          <span className="font-bold text-success-600">{collectionPct}% achieved</span>
          <span style={{ color: "#6B6D76" }}>₹{((s.amount_target_today - s.amount_collected_today) / 100000).toFixed(1)}L remaining</span>
        </div>

        {/* Collection breakdown */}
        <div className="grid grid-cols-3 gap-4 mt-4 pt-4" style={{ borderTop: "1px solid #EAEBEF" }}>
          <div className="text-center">
            <p className="text-lg font-bold" style={{ color: "#1C1C1F" }}>{collectionPct}%</p>
            <p className="text-xs" style={{ color: "#6B6D76" }}>Collection Rate</p>
          </div>
          <div className="text-center">
            <p className="text-lg font-bold" style={{ color: "#1C1C1F" }}>₹{Math.round(s.amount_collected_today / Math.max(s.agents_on_duty, 1) / 1000)}K</p>
            <p className="text-xs" style={{ color: "#6B6D76" }}>Per Agent Avg</p>
          </div>
          <div className="text-center">
            <p className="text-lg font-bold" style={{ color: "#1C1C1F" }}>{(s.cases_today / Math.max(s.agents_on_duty, 1)).toFixed(1)}</p>
            <p className="text-xs" style={{ color: "#6B6D76" }}>Cases/Agent</p>
          </div>
        </div>
      </div>

      {/* Agent Leaderboard */}
      <div
        className="card p-6"
        style={{ animation: `enter 420ms ${EASE} 300ms both` }}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Agent Leaderboard</h2>
          <a href="/manager/agents" className="text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors">
            View all →
          </a>
        </div>
        <div className="space-y-1">
          {agents.slice(0, 10).map((a, idx) => (
            <AgentRow key={a.id} agent={a} rank={idx + 1} animated={animated} delay={idx * 40} />
          ))}
        </div>
        {agents.length > 10 && (
          <div className="mt-3 pt-3 text-center" style={{ borderTop: "1px solid #EAEBEF" }}>
            <a href="/manager/agents" className="text-xs font-semibold text-brand-600 hover:text-brand-700 transition-colors">
              See all {agents.length} agents →
            </a>
          </div>
        )}
      </div>

      {/* AI Briefing + DPD Portfolio */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ animation: `enter 420ms ${EASE} 360ms both` }}>
        {/* AI Ops Briefing card */}
        {briefingLoading ? (
          <div className="card animate-pulse" style={{ height: 220, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
        ) : briefing ? (
          <AiBriefingCard briefing={briefing} onRefresh={() => loadBriefing(true)} />
        ) : (
          <div className="card p-5">
            <h3 className="text-sm font-bold mb-3 flex items-center gap-2" style={{ color: "#1C1C1F" }}>
              Pending Actions
            </h3>
            <div className="space-y-2.5">
              <ActionItem label="PTP follow-ups due today"      value={s.ptps_due_today}                    color="text-warning-600" />
              <ActionItem label="Cases pending first visit"     value={Math.round(s.cases_assigned * 0.18)} color="text-brand-600"   />
              <ActionItem label="Escalated cases"               value={3}                                    color="text-danger-600"  />
            </div>
          </div>
        )}

        {/* DPD Portfolio — real data from briefing when available */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold" style={{ color: "#1C1C1F" }}>Portfolio by DPD Bucket</h3>
            {briefing && (
              <span className="text-xs" style={{ color: "#6B6D76" }}>{briefing.total_cases_in_portfolio} cases</span>
            )}
          </div>
          <div className="space-y-2.5">
            {(briefing?.dpd_breakdown ?? []).map((b) => {
              const cfg = DPD_BUCKET_CONFIG[b.bucket] ?? { bar: "#94a3b8", shadow: "rgba(148,163,184,0.25)", label: b.bucket };
              const maxCount = Math.max(...(briefing?.dpd_breakdown ?? []).map(x => x.case_count), 1);
              const barPct = Math.round((b.case_count / maxCount) * 100);
              return (
                <div key={b.bucket} className="flex items-center gap-3">
                  <span className="text-xs flex-shrink-0" style={{ color: "#6B6D76", width: 140 }}>{cfg.label}</span>
                  <div className="flex-1 rounded-full overflow-hidden" style={{ height: 8, background: "#EFF0F4" }}>
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: barReady ? `${barPct}%` : "0%",
                        background: cfg.bar,
                        boxShadow: `0 2px 6px ${cfg.shadow}`,
                        transition: `width 850ms ${EASE}`,
                      }}
                    />
                  </div>
                  <span className="text-xs font-bold flex-shrink-0 text-right" style={{ width: 28, color: "#1C1C1F" }}>{b.case_count}</span>
                </div>
              );
            })}
            {!briefing && (
              <p className="text-xs" style={{ color: "#94a3b8" }}>Loading portfolio data…</p>
            )}
          </div>
          {briefing?.dpd_breakdown && briefing.dpd_breakdown.length > 0 && (
            <div className="mt-3 pt-3 flex gap-4 text-xs" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
              <span>Total: <strong style={{ color: "#1C1C1F" }}>₹{briefing.dpd_breakdown.reduce((s, b) => s + b.target_lakhs, 0).toFixed(1)}L</strong> target</span>
              <span>Collected: <strong className="text-success-600">₹{briefing.dpd_breakdown.reduce((s, b) => s + b.collected_lakhs, 0).toFixed(1)}L</strong></span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentRow({ agent, rank, animated, delay }: { agent: Agent; rank: number; animated: boolean; delay: number }) {
  const navigate  = useNavigate();
  const collected = agent.today_collected ?? 0;
  const target    = agent.today_target    ?? 0;
  const barPct    = target > 0 ? Math.min(Math.round((collected / target) * 100), 100) : 0;

  const rankStyle =
    rank === 1 ? { background: "rgba(234,179,8,0.15)",  color: "#92400E" } :
    rank === 2 ? { background: "rgba(148,163,184,0.2)", color: "#475569" } :
    rank === 3 ? { background: "rgba(249,115,22,0.15)", color: "#9A3412" } :
                 { background: "rgba(148,163,184,0.12)", color: "#64748b" };

  const barColor =
    rank === 1 ? "linear-gradient(90deg, #eab308, #d97706)" :
    rank <= 3   ? "linear-gradient(90deg, #1677FF, #0C4DB3)" :
                  "linear-gradient(90deg, #4090FF, #1677FF)";

  return (
    <div
      className="flex items-center gap-3 p-3 rounded-xl transition-all duration-150"
      style={{
        background: agent.sos_active ? "rgba(220,38,38,0.06)" : "transparent",
        animation: `enter 380ms cubic-bezier(0.16,1,0.3,1) ${delay}ms both`,
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = agent.sos_active ? "rgba(220,38,38,0.10)" : "rgba(22,119,255,0.04)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = agent.sos_active ? "rgba(220,38,38,0.06)" : "transparent"; }}
    >
      <span
        className="w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-full text-xs font-bold"
        style={rankStyle}
      >
        {rank}
      </span>
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm flex-shrink-0"
        style={{ background: "rgba(22,119,255,0.12)", color: "#1677FF" }}
      >
        {agent.full_name.charAt(0)}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              const today = new Date().toISOString().slice(0, 10);
              navigate(`/manager/cases?agent_id=${agent.id}&agent_name=${encodeURIComponent(agent.full_name)}&date_from=${today}&date_to=${today}`);
            }}
            className="text-sm font-semibold truncate text-left"
            style={{ color: "#0C66E4", background: "none", border: "none", cursor: "pointer", padding: 0, textDecoration: "underline", textDecorationColor: "rgba(12,102,228,0.3)", textUnderlineOffset: 2 }}
          >
            {agent.full_name}
          </button>
          <TierBadge tier={agent.tier} />
          {agent.sos_active && <span className="badge badge-red animate-pulse text-xs">SOS</span>}
        </div>
        <div className="flex items-center gap-2 mt-1.5">
          <div
            className="flex-1 rounded-full overflow-hidden cursor-default"
            style={{ height: 6, background: "#EFF0F4" }}
            title={`Collected ₹${collected.toLocaleString("en-IN")} of ₹${target.toLocaleString("en-IN")} today`}
          >
            <div
              className="h-full rounded-full"
              style={{
                width: animated ? `${barPct}%` : "0%",
                background: barColor,
                transition: "width 1.1s cubic-bezier(0.16,1,0.3,1)",
              }}
            />
          </div>
          <span className="text-xs flex-shrink-0 whitespace-nowrap" style={{ color: "#6B6D76" }}>
            ₹{(target / 1000).toFixed(0)}K target
          </span>
        </div>
      </div>
      <div className="text-right flex-shrink-0 min-w-[52px]">
        <p className="text-sm font-bold text-success-600">₹{(collected / 1000).toFixed(0)}K</p>
        <p className="text-xs" style={{ color: "#6B6D76" }}>{barPct}% done</p>
      </div>
      <div className="w-2 h-2 rounded-full flex-shrink-0 bg-success-500" />
    </div>
  );
}

function ActionItem({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm" style={{ color: "#6B6D76" }}>{label}</span>
      <span className={`text-sm font-bold ${color}`}>{value}</span>
    </div>
  );
}

function AiBriefingCard({ briefing, onRefresh }: { briefing: BriefingData; onRefresh: () => void }) {
  const signalColor = briefing.collection_velocity.pace_pct >= 50
    ? "#16a34a"
    : briefing.collection_velocity.pace_pct >= 25
    ? "#d97706"
    : "#dc2626";

  return (
    <div className="card p-5" style={{ border: "1px solid rgba(124,58,237,0.15)", background: "linear-gradient(160deg, #faf5ff 0%, #fff 60%)" }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div
            className="flex items-center justify-center rounded-xl"
            style={{ width: 28, height: 28, background: "linear-gradient(135deg, #7c3aed, #a855f7)" }}
          >
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-sm font-bold" style={{ color: "#1C1C1F" }}>AI Ops Briefing</span>
        </div>
        <button
          onClick={onRefresh}
          className="p-1.5 rounded-lg transition-colors hover:bg-purple-50"
          style={{ color: "#a855f7" }}
          title="Refresh briefing (cached 1h)"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Headline */}
      <p className="text-xs font-semibold leading-snug mb-3" style={{ color: "#1C1C1F" }}>{briefing.headline}</p>

      {/* PTP Risk pills + velocity */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        {briefing.ptp_risk.high > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full font-bold" style={{ background: "rgba(220,38,38,0.10)", color: "#dc2626" }}>
            {briefing.ptp_risk.high} HIGH-RISK PTP
          </span>
        )}
        {briefing.ptp_risk.medium > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full font-bold" style={{ background: "rgba(245,158,11,0.10)", color: "#d97706" }}>
            {briefing.ptp_risk.medium} MEDIUM
          </span>
        )}
        {briefing.ptp_risk.low > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full font-bold" style={{ background: "rgba(22,163,74,0.10)", color: "#16a34a" }}>
            {briefing.ptp_risk.low} LOW
          </span>
        )}
        <span className="text-xs flex items-center gap-1 ml-auto font-semibold" style={{ color: signalColor }}>
          {briefing.collection_velocity.pace_pct >= 40 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
          {briefing.collection_velocity.pace_pct}% pace · proj {briefing.collection_velocity.projected_eod_pct}% EOD
        </span>
      </div>

      {/* Stalled agents alert */}
      {briefing.stalled_agents.length > 0 && (
        <div
          className="flex items-start gap-2 rounded-xl px-3 py-2 mb-3"
          style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)" }}
        >
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" style={{ color: "#d97706" }} />
          <p className="text-xs" style={{ color: "#92400e" }}>
            {briefing.stalled_agents.length} agent{briefing.stalled_agents.length > 1 ? "s" : ""} with 0 visits today:{" "}
            {briefing.stalled_agents.slice(0, 2).map(a => a.name).join(", ")}
            {briefing.stalled_agents.length > 2 ? ` +${briefing.stalled_agents.length - 2} more` : ""}
          </p>
        </div>
      )}

      {/* Recommended actions */}
      <div className="space-y-1.5">
        {briefing.recommended_actions.map((ra, i) => (
          <div key={i} className="flex items-start gap-2">
            <span
              className="flex-shrink-0 text-xs font-bold px-1.5 py-0.5 rounded-md"
              style={{
                background: ra.urgency === "NOW" ? "rgba(220,38,38,0.10)" : "rgba(22,119,255,0.10)",
                color: ra.urgency === "NOW" ? "#dc2626" : "#1677FF",
              }}
            >
              {ra.urgency}
            </span>
            <p className="text-xs leading-snug" style={{ color: "#6B6D76" }}>{ra.action}</p>
          </div>
        ))}
      </div>

      {/* Pending actions summary row */}
      <div className="mt-3 pt-3 flex gap-4 text-xs" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
        <span>PTPs due: <strong className="text-warning-600">{briefing.pending_actions.ptps_due}</strong></span>
        <span>No visit yet: <strong className="text-brand-600">{briefing.pending_actions.cases_pending_first_visit}</strong></span>
        <span>Escalated: <strong className="text-danger-600">{briefing.pending_actions.escalated_cases}</strong></span>
      </div>
    </div>
  );
}

function LoadingGrid() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="card animate-pulse" style={{ height: 96, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
        ))}
      </div>
      <div className="card animate-pulse" style={{ height: 128, background: "#EFF0F4", border: "none", boxShadow: "none" }} />
    </div>
  );
}
