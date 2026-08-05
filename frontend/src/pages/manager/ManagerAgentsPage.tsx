// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-15 — Both SOS "Respond"/"Emergency Response" buttons previously
//   just called toast.success("...dispatched") with nothing behind it. Now
//   call acknowledgeAgentSos(), which sends the agent a real SMS/WhatsApp
//   confirming their manager has seen the alert. Full detail: /changelog.md.
// ─────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Search, MapPin, AlertTriangle, Phone, ChevronDown, Brain, Shuffle, X, Loader2, TrendingUp, TrendingDown, Minus, IndianRupee } from "lucide-react";
import { toast } from "react-hot-toast";
import { getAgents, getAgentInsight, getReallocationPlan, updateAgentStatus, acknowledgeAgentSos } from "@/api/manager";
import type { AgentInsight, ReallocationPlan } from "@/api/manager";
import { Input } from "@/components/ui/Input";
import { TierBadge } from "@/components/ui/Badge";
import { useModalA11y } from "@/hooks/useModalA11y";
import type { Agent, AgentStatus } from "@/types";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

export default function ManagerAgentsPage() {
  const [agents, setAgents]         = useState<Agent[]>([]);
  const [loading, setLoading]       = useState(true);
  const [search, setSearch]         = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ON_DUTY" | "OFF_DUTY">("ALL");
  const [tierFilter, setTierFilter]     = useState<"ALL" | "TIER_1" | "TIER_2" | "TIER_3">("ALL");
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [barReady, setBarReady]     = useState(false);

  const load = useCallback(() => {
    return getAgents().then((a) => setAgents(a)).catch(() => {});
  }, []);

  // Typed as AgentStatus, not string — a plain `string` here widened the whole
  // mapped array and made it unassignable back to Agent[] (the one real type
  // error this file had).
  const handleStatusChange = useCallback((id: string, newStatus: AgentStatus) => {
    setAgents((prev) => prev.map((a) => a.id === id ? { ...a, status: newStatus } : a));
  }, []);

  useEffect(() => {
    load().finally(() => setLoading(false));
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  // Trigger bar animations after agents load — also reset on re-mount so animations replay
  useEffect(() => {
    setBarReady(false);
    if (!loading && agents.length > 0) {
      const t = setTimeout(() => setBarReady(true), 160);
      return () => clearTimeout(t);
    }
  }, [loading, agents.length]);

  const filtered = useMemo(() => {
    return agents.filter((a) => {
      if (statusFilter !== "ALL" && a.status !== statusFilter) return false;
      if (tierFilter  !== "ALL" && a.tier   !== tierFilter)   return false;
      if (search) {
        const q = search.toLowerCase();
        return (
          a.full_name.toLowerCase().includes(q) ||
          a.employee_code.toLowerCase().includes(q) ||
          a.territory.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [agents, search, statusFilter, tierFilter]);

  const onDuty    = agents.filter((a) => a.status === "ON_DUTY").length;
  const sosAgents = agents.filter((a) => a.sos_active);
  const tier1     = agents.filter((a) => a.tier === "TIER_1").length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div
        className="flex items-center justify-between gap-3"
        style={{ animation: `enter 420ms ${EASE} 0ms both` }}
      >
        <div className="min-w-0">
          <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>Field Agents</h1>
          <p className="text-[13px] sm:text-sm" style={{ color: "#6B6D76" }}>
            {onDuty} on duty · {agents.length - onDuty} off · {tier1} Tier 1 agents
          </p>
        </div>
        {sosAgents.length > 0 && (
          <div
            className="flex items-center gap-2 px-3 sm:px-4 py-2 rounded-xl font-semibold text-xs sm:text-sm animate-pulse flex-shrink-0"
            style={{ background: "rgba(220,38,38,0.10)", border: "1px solid rgba(220,38,38,0.25)", color: "#991B1B" }}
          >
            <AlertTriangle className="w-4 h-4 flex-shrink-0" />
            <span className="whitespace-nowrap">{sosAgents.length} SOS<span className="hidden sm:inline"> Active</span></span>
          </div>
        )}
      </div>

      {/* SOS agents banner */}
      {sosAgents.length > 0 && (
        <div
          className="rounded-[22px] p-4 space-y-2"
          style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.18)" }}
        >
          {sosAgents.map((a) => (
            <div key={a.id} className="flex flex-wrap items-center gap-2 sm:gap-3">
              <AlertTriangle className="w-4 h-4 animate-pulse flex-shrink-0" style={{ color: "#DC2626" }} />
              <div className="flex-1 min-w-0" style={{ minWidth: "10rem" }}>
                <span className="text-sm font-semibold" style={{ color: "#991B1B" }}>{a.full_name}</span>
                <span className="text-xs ml-2" style={{ color: "#B91C1C" }}>{a.territory} · {a.employee_code}</span>
              </div>
              {/* Share the row once it wraps, so neither button becomes a sliver */}
              <div className="flex gap-2 w-full sm:w-auto">
                <button
                  onClick={() => {
                    acknowledgeAgentSos(a.id)
                      .then(() => toast.success(`${a.full_name} notified — manager response acknowledged`))
                      .catch(() => toast.error("Could not notify agent — check Twilio config"));
                  }}
                  className="tap-target flex-1 sm:flex-none text-xs text-white px-3 rounded-xl font-semibold transition-colors hover:opacity-90"
                  style={{ background: "#DC2626" }}
                >
                  Respond
                </button>
                <a
                  href={`tel:${a.employee_code}`}
                  className="tap-target flex-1 sm:flex-none text-xs px-3 rounded-xl font-semibold flex items-center justify-center gap-1 transition-colors"
                  style={{ background: "#fff", border: "1px solid rgba(220,38,38,0.25)", color: "#DC2626" }}
                >
                  <Phone className="w-3 h-3" /> Call
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      {/* Filters — 7 segmented buttons will not fit across 360px, so below lg
          they sit in a scroll rail. Every option stays visible and reachable;
          only the viewport moves. Same idiom as AgentCasesPage's chip row. */}
      <div
        className="space-y-2 lg:space-y-0 lg:flex lg:flex-wrap lg:gap-3"
        style={{ animation: `enter 420ms ${EASE} 60ms both` }}
      >
        <div className="lg:flex-1 lg:min-w-48">
          <Input placeholder="Search name, ID, territory..." value={search} onChange={(e) => setSearch(e.target.value)} leftIcon={<Search className="w-4 h-4" />} />
        </div>
        <div className="rail scrollbar-hide gap-2 lg:gap-3 lg:overflow-visible -mx-3 px-3 sm:-mx-4 sm:px-4 lg:mx-0 lg:px-0">
          <div className="flex rounded-xl overflow-hidden flex-shrink-0" style={{ border: "1px solid #EAEBEF" }}>
            {(["ALL", "ON_DUTY", "OFF_DUTY"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                aria-pressed={statusFilter === s}
                className="tap-target-h px-3 py-2 text-xs font-semibold transition hover:brightness-95 whitespace-nowrap"
                style={{
                  background: statusFilter === s ? "#1677FF" : "#fff",
                  color: statusFilter === s ? "#fff" : "#6B6D76",
                }}
              >
                {s === "ALL" ? "All" : s === "ON_DUTY" ? "On Duty" : "Off Duty"}
              </button>
            ))}
          </div>
          <div className="flex rounded-xl overflow-hidden flex-shrink-0" style={{ border: "1px solid #EAEBEF" }}>
            {(["ALL", "TIER_1", "TIER_2", "TIER_3"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTierFilter(t)}
                aria-pressed={tierFilter === t}
                className="tap-target-h px-3 py-2 text-xs font-semibold transition hover:brightness-95 whitespace-nowrap"
                style={{
                  background: tierFilter === t ? "#1677FF" : "#fff",
                  color: tierFilter === t ? "#fff" : "#6B6D76",
                }}
              >
                {t === "ALL" ? "All Tiers" : t.replace("_", " ")}
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="text-xs" style={{ color: "#6B6D76" }}>{filtered.length} agents shown</p>

      {/* Agent table */}
      <div
        className="card p-0 overflow-hidden"
        style={{ animation: `enter 420ms ${EASE} 120ms both` }}
      >
        <div
          className="hidden lg:grid grid-cols-12 gap-4 px-4 py-3 border-b text-xs font-semibold uppercase tracking-wide"
          style={{ background: "#F5F6F9", borderColor: "#EAEBEF", color: "#6B6D76" }}
        >
          <span className="col-span-1">#</span>
          <span className="col-span-3">Agent</span>
          <span className="col-span-2">Territory</span>
          <span className="col-span-1">Tier</span>
          <span className="col-span-1">Score</span>
          <span className="col-span-2">Collection Rate</span>
          <span className="col-span-1">PTP Rate</span>
          <span className="col-span-1">Status</span>
        </div>

        {loading
          ? Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-14 border-b animate-pulse" style={{ borderColor: "#EAEBEF", background: "#F5F6F9" }} />
            ))
          : filtered.map((agent, i) => (
              <AgentRow
                key={agent.id}
                agent={agent}
                rank={i + 1}
                expanded={expandedAgent === agent.id}
                onToggle={() => setExpandedAgent(expandedAgent === agent.id ? null : agent.id)}
                barReady={barReady}
                delay={i * 30}
                onStatusChange={handleStatusChange}
              />
            ))
        }

        {!loading && filtered.length === 0 && (
          <div className="py-16 text-center" style={{ color: "#6B6D76" }}>
            <p className="text-sm">No agents match the current filters</p>
          </div>
        )}
      </div>
    </div>
  );
}

function AgentRow({
  agent, rank, expanded, onToggle, barReady, delay, onStatusChange,
}: {
  agent: Agent; rank: number; expanded: boolean; onToggle: () => void; barReady: boolean; delay: number; onStatusChange: (id: string, newStatus: AgentStatus) => void;
}) {
  const navigate      = useNavigate();
  const ptpRate       = Math.round(agent.ptp_rate_pct ?? 0);
  const collectionPct = Math.min(Math.round(agent.collection_rate_pct ?? 0), 100);

  const [insight, setInsight]               = useState<AgentInsight | null>(null);
  const [insightLoading, setInsightLoading] = useState(false);
  const [plan, setPlan]                     = useState<ReallocationPlan | null>(null);
  const [planLoading, setPlanLoading]       = useState(false);
  const [showPlan, setShowPlan]             = useState(false);
  const [statusUpdating, setStatusUpdating] = useState(false);
  const [sosAcking, setSosAcking]           = useState(false);

  async function acknowledgeSos() {
    setSosAcking(true);
    try {
      await acknowledgeAgentSos(agent.id);
      toast.success(`${agent.full_name} notified — manager response acknowledged`);
    } catch {
      toast.error("Could not notify agent — check Twilio config");
    } finally {
      setSosAcking(false);
    }
  }

  async function toggleStatus() {
    const newStatus = agent.status === "ON_DUTY" ? "OFF_DUTY" : "ON_DUTY";
    setStatusUpdating(true);
    try {
      await updateAgentStatus(agent.id, newStatus);
      onStatusChange(agent.id, newStatus);
      toast.success(`${agent.full_name} marked ${newStatus === "ON_DUTY" ? "On Duty" : "Off Duty"}`);
    } catch {
      toast.error("Could not update status");
    } finally {
      setStatusUpdating(false);
    }
  }

  async function fetchInsight() {
    setInsightLoading(true);
    try {
      const data = await getAgentInsight(agent.id);
      setInsight(data);
    } catch {
      toast.error("Could not load AI insight");
    } finally {
      setInsightLoading(false);
    }
  }

  async function fetchPlan() {
    setPlanLoading(true);
    try {
      const data = await getReallocationPlan(agent.id);
      setPlan(data);
      setShowPlan(true);
    } catch {
      toast.error("Could not load reallocation plan");
    } finally {
      setPlanLoading(false);
    }
  }

  return (
    <div
      className="border-b last:border-0"
      style={{
        borderColor: "#EAEBEF",
        background: agent.sos_active ? "rgba(220,38,38,0.04)" : "transparent",
        animation: `enter 380ms cubic-bezier(0.16,1,0.3,1) ${delay}ms both`,
      }}
    >
      {/* Hover and the expanded state are both driven by .row-accent in
          index.css, shared with Case Management. No inline background here on
          purpose: an inline style would outrank the class rule and the :hover
          would never show. */}
      <div
        className={`hidden lg:grid grid-cols-12 gap-4 px-4 py-3.5 text-sm items-center cursor-pointer row-accent${
          agent.sos_active ? " row-accent-danger" : ""
        }${expanded ? " is-expanded" : ""}`}
        onClick={onToggle}
      >
        <span className="col-span-1 font-medium" style={{ color: "#6B6D76" }}>{rank}</span>

        <div className="col-span-3 flex items-center gap-2 min-w-0">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm flex-shrink-0"
            style={{ background: "rgba(22,119,255,0.12)", color: "#1677FF" }}
          >
            {agent.full_name.charAt(0)}
          </div>
          <div className="min-w-0">
            <p className="font-semibold truncate" style={{ color: "#1C1C1F" }}>{agent.full_name}</p>
            <p className="text-xs" style={{ color: "#6B6D76" }}>{agent.employee_code}</p>
          </div>
          {agent.sos_active && <AlertTriangle className="w-4 h-4 animate-pulse flex-shrink-0" style={{ color: "#DC2626" }} />}
        </div>

        <div className="col-span-2 flex items-center gap-1 text-xs" style={{ color: "#6B6D76" }}>
          <MapPin className="w-3 h-3 flex-shrink-0" />
          <span className="truncate">{agent.territory}</span>
        </div>

        <div className="col-span-1"><TierBadge tier={agent.tier} /></div>

        <div className="col-span-1">
          <span className="font-bold text-brand-600">{agent.ranking_score.toFixed(0)}</span>
        </div>

        <div className="col-span-2">
          <div className="flex items-center gap-2">
            <div className="flex-1 rounded-full overflow-hidden" style={{ height: 6, background: "#EFF0F4" }}>
              <div
                className="h-full rounded-full"
                style={{
                  width: barReady ? `${collectionPct}%` : "0%",
                  background: collectionPct >= 60 ? "#22c55e" : collectionPct >= 35 ? "#f59e0b" : "#ef4444",
                  transition: `width 900ms ${EASE}`,
                }}
              />
            </div>
            <span className={`text-xs font-bold flex-shrink-0 ${collectionPct >= 60 ? "text-success-600" : collectionPct >= 35 ? "text-warning-600" : "text-danger-600"}`}>
              {collectionPct}%
            </span>
          </div>
          <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>{agent.current_month_visits} visits · ₹{(agent.current_month_collections / 100000).toFixed(1)}L</p>
        </div>

        <div className="col-span-1">
          <span className={`text-sm font-bold ${ptpRate >= 70 ? "text-success-600" : ptpRate >= 50 ? "text-warning-600" : "text-danger-600"}`}>
            {ptpRate}%
          </span>
        </div>

        <div className="col-span-1 flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${agent.status === "ON_DUTY" ? "bg-success-500" : "bg-slate-300"}`} />
          <span className={`text-xs font-medium ${agent.status === "ON_DUTY" ? "text-success-600" : "text-slate-400"}`}>
            {agent.status === "ON_DUTY" ? "On Duty" : "Off"}
          </span>
          <ChevronDown
            className="w-3 h-3 ml-auto transition-transform"
            style={{ color: "#C4C6CF", transform: expanded ? "rotate(180deg)" : "none" }}
          />
        </div>
      </div>

      {/* Mobile card — brought to parity with the desktop row: it was missing
          rank and the collection-rate bar, both of which are the point of the
          leaderboard ordering. */}
      <div
        className="lg:hidden p-4 cursor-pointer"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
      >
        <div className="flex items-center gap-2.5">
          <span
            className="w-6 h-6 flex-shrink-0 flex items-center justify-center rounded-full text-xs font-bold"
            style={{ background: "rgba(148,163,184,0.14)", color: "#64748b" }}
          >
            {rank}
          </span>
          <div
            className="w-10 h-10 rounded-full flex items-center justify-center font-bold flex-shrink-0"
            style={{ background: "rgba(22,119,255,0.12)", color: "#1677FF" }}
          >
            {agent.full_name.charAt(0)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="font-semibold truncate" style={{ color: "#1C1C1F" }}>{agent.full_name}</p>
              {agent.sos_active && <AlertTriangle className="w-3.5 h-3.5 animate-pulse flex-shrink-0" style={{ color: "#DC2626" }} />}
            </div>
            <p className="text-xs truncate" style={{ color: "#6B6D76" }}>{agent.employee_code} · {agent.territory}</p>
          </div>
          <TierBadge tier={agent.tier} />
          <ChevronDown
            className="w-4 h-4 flex-shrink-0 transition-transform"
            style={{ color: "#C4C6CF", transform: expanded ? "rotate(180deg)" : "none" }}
          />
        </div>

        {/* Collection rate — the desktop row's headline metric */}
        <div className="flex items-center gap-2 mt-2.5">
          <div className="flex-1 rounded-full overflow-hidden" style={{ height: 6, background: "#EFF0F4" }}>
            <div
              className="h-full rounded-full"
              style={{
                width: barReady ? `${collectionPct}%` : "0%",
                background: collectionPct >= 60 ? "#22c55e" : collectionPct >= 35 ? "#f59e0b" : "#ef4444",
                transition: `width 900ms ${EASE}`,
              }}
            />
          </div>
          <span className={`text-xs font-bold flex-shrink-0 ${collectionPct >= 60 ? "text-success-600" : collectionPct >= 35 ? "text-warning-600" : "text-danger-600"}`}>
            {collectionPct}%
          </span>
        </div>

        <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-xs" style={{ color: "#6B6D76" }}>
          <span>Score <strong className="text-brand-600">{agent.ranking_score.toFixed(0)}</strong></span>
          <span>₹{(agent.current_month_collections / 100000).toFixed(1)}L collected</span>
          <span>{agent.current_month_visits} visits</span>
          <span>PTP {ptpRate}%</span>
          <span className="flex items-center gap-1 ml-auto">
            <span className={`w-2 h-2 rounded-full ${agent.status === "ON_DUTY" ? "bg-success-500" : "bg-slate-300"}`} />
            <span className={agent.status === "ON_DUTY" ? "text-success-600 font-medium" : "text-slate-400"}>
              {agent.status === "ON_DUTY" ? "On Duty" : "Off"}
            </span>
          </span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          className="px-4 pb-4 border-t"
          style={{ background: "#F5F6F9", borderColor: "#EAEBEF" }}
        >
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
            <StatMini label="Visits This Month"  value={String(agent.current_month_visits)} />
            <StatMini label="PTPs Set"           value={String(agent.current_month_ptps_set)} />
            <StatMini label="PTPs Honored"       value={String(agent.current_month_ptps_honored)} />
            <StatMini label="Lifetime Rate"      value={`${(agent.lifetime_collection_rate * 100).toFixed(0)}%`} />
          </div>
          <div className="flex flex-wrap gap-2 mt-3">
            {agent.languages_spoken.map((l) => (
              <span key={l} className="text-xs badge badge-blue px-2.5 py-1">{l}</span>
            ))}
            <span className="text-xs badge badge-gray px-2.5 py-1">{agent.specialization}</span>
            <span className="text-xs badge badge-gray px-2.5 py-1">Max {agent.max_cases_per_day} cases/day</span>
          </div>
          <div className="flex gap-2 mt-3 flex-wrap">
            <button
              onClick={() => {
                const sixMonths = new Date();
                sixMonths.setMonth(sixMonths.getMonth() - 6);
                const dateFrom = sixMonths.toISOString().slice(0, 10);
                const dateTo   = new Date().toISOString().slice(0, 10);
                navigate(`/manager/cases?agent_id=${agent.id}&agent_name=${encodeURIComponent(agent.full_name)}&date_from=${dateFrom}&date_to=${dateTo}`);
              }}
              className="tap-target text-xs px-3 py-1.5 rounded-xl font-semibold transition hover:brightness-95 inline-flex items-center justify-center"
              style={{ background: "#0C66E4", color: "white" }}
            >
              View Cases →
            </button>
            <button
              onClick={() => toast.success(`Message sent to ${agent.full_name}`)}
              className="tap-target text-xs px-3 py-1.5 rounded-xl font-semibold badge-blue transition hover:brightness-95 inline-flex items-center justify-center"
            >
              Send Message
            </button>
            <button
              onClick={fetchPlan}
              disabled={planLoading}
              className="tap-target text-xs px-3 py-1.5 rounded-xl font-semibold transition hover:brightness-95 flex items-center justify-center gap-1.5"
              style={{ background: planLoading ? "#e2e8f0" : "#f1f5f9", color: "#475569", border: "1px solid #e2e8f0" }}
            >
              {planLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Shuffle className="w-3 h-3" />}
              Reallocation Plan
            </button>
            {!insight && !insightLoading && (
              <button
                onClick={fetchInsight}
                className="tap-target text-xs px-3 py-1.5 rounded-xl font-semibold transition hover:brightness-95 flex items-center justify-center gap-1.5"
                style={{ background: "linear-gradient(135deg, #7c3aed, #a855f7)", color: "white" }}
              >
                <Brain className="w-3 h-3" /> AI Insight
              </button>
            )}
            {insightLoading && (
              <span className="text-xs px-3 py-1.5 flex items-center gap-1.5" style={{ color: "#7c3aed" }}>
                <Loader2 className="w-3 h-3 animate-spin" /> Analysing…
              </span>
            )}
            <button
              onClick={toggleStatus}
              disabled={statusUpdating}
              className="tap-target text-xs px-3 py-1.5 rounded-xl font-semibold transition hover:brightness-95 flex items-center justify-center gap-1.5"
              style={{
                background: agent.status === "ON_DUTY" ? "rgba(245,158,11,0.10)" : "rgba(22,163,74,0.10)",
                color: agent.status === "ON_DUTY" ? "#d97706" : "#16a34a",
                border: `1px solid ${agent.status === "ON_DUTY" ? "rgba(245,158,11,0.25)" : "rgba(22,163,74,0.25)"}`,
              }}
            >
              {statusUpdating
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <span className={`w-1.5 h-1.5 rounded-full ${agent.status === "ON_DUTY" ? "bg-warning-500" : "bg-success-500"}`} />
              }
              {agent.status === "ON_DUTY" ? "Mark Off Duty" : "Mark On Duty"}
            </button>
            {agent.sos_active && (
              <button
                onClick={acknowledgeSos}
                disabled={sosAcking}
                className="text-xs text-white px-3 py-1.5 rounded-xl font-semibold animate-pulse transition hover:brightness-95 flex items-center gap-1.5"
                style={{ background: "#DC2626" }}
              >
                {sosAcking ? <Loader2 className="w-3 h-3 animate-spin" /> : "🆘"} Emergency Response
              </button>
            )}
          </div>

          {/* AI Performance Insight strip */}
          {insight && (
            <AgentInsightStrip insight={insight} />
          )}
        </div>
      )}

      {/* Reallocation Plan Modal */}
      {showPlan && plan && (
        <ReallocationModal plan={plan} onClose={() => setShowPlan(false)} />
      )}
    </div>
  );
}

function StatMini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl p-3" style={{ background: "#fff", border: "1px solid #EAEBEF" }}>
      <p className="text-base font-bold" style={{ color: "#1C1C1F" }}>{value}</p>
      <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>{label}</p>
    </div>
  );
}

function AgentInsightStrip({ insight }: { insight: AgentInsight }) {
  const signalConfig = {
    IMPROVING: { icon: <TrendingUp className="w-4 h-4" />, color: "#16a34a", bg: "rgba(22,163,74,0.08)", border: "rgba(22,163,74,0.2)", label: "IMPROVING" },
    DECLINING:  { icon: <TrendingDown className="w-4 h-4" />, color: "#dc2626", bg: "rgba(220,38,38,0.06)", border: "rgba(220,38,38,0.2)", label: "DECLINING" },
    STABLE:     { icon: <Minus className="w-4 h-4" />, color: "#6b7280", bg: "rgba(107,114,128,0.06)", border: "rgba(107,114,128,0.2)", label: "STABLE" },
  }[insight.performance_signal] ?? { icon: <Minus className="w-4 h-4" />, color: "#6b7280", bg: "rgba(107,114,128,0.06)", border: "rgba(107,114,128,0.2)", label: "STABLE" };

  const vsTeamRate = insight.current_month.collection_rate_pct - insight.team_avg.collection_rate_pct;
  const vsTeamPtp  = insight.current_month.ptp_rate_pct - insight.team_avg.ptp_rate_pct;

  return (
    <div
      className="mt-3 rounded-xl p-3"
      style={{ background: "linear-gradient(160deg, #faf5ff 0%, #fff 70%)", border: "1px solid rgba(124,58,237,0.15)" }}
    >
      <div className="flex items-center gap-2 mb-2">
        <div
          className="flex items-center justify-center rounded-lg flex-shrink-0"
          style={{ width: 24, height: 24, background: "linear-gradient(135deg, #7c3aed, #a855f7)" }}
        >
          <Brain className="w-3 h-3 text-white" />
        </div>
        <span className="text-xs font-bold" style={{ color: "#1C1C1F" }}>AI Performance Analysis</span>
        <span
          className="ml-auto flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full"
          style={{ background: signalConfig.bg, color: signalConfig.color, border: `1px solid ${signalConfig.border}` }}
        >
          {signalConfig.icon}
          {signalConfig.label}
        </span>
      </div>

      <p className="text-xs leading-relaxed mb-2.5" style={{ color: "#374151" }}>{insight.insight_text}</p>

      {/* vs Team comparison pills */}
      <div className="flex flex-wrap gap-1.5 mb-2.5">
        <span
          className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{
            background: vsTeamRate >= 0 ? "rgba(22,163,74,0.10)" : "rgba(220,38,38,0.10)",
            color: vsTeamRate >= 0 ? "#16a34a" : "#dc2626",
          }}
        >
          Collection {vsTeamRate >= 0 ? "+" : ""}{vsTeamRate.toFixed(1)}% vs team
        </span>
        <span
          className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{
            background: vsTeamPtp >= 0 ? "rgba(22,163,74,0.10)" : "rgba(220,38,38,0.10)",
            color: vsTeamPtp >= 0 ? "#16a34a" : "#dc2626",
          }}
        >
          PTP {vsTeamPtp >= 0 ? "+" : ""}{vsTeamPtp.toFixed(1)}% vs team
        </span>
        {(() => {
          const yieldDelta = insight.current_month.per_visit_yield - insight.team_avg.per_visit_yield;
          return (
            <span
              className="text-xs px-2 py-0.5 rounded-full font-semibold"
              style={{
                background: yieldDelta >= 0 ? "rgba(22,163,74,0.10)" : "rgba(220,38,38,0.10)",
                color: yieldDelta >= 0 ? "#16a34a" : "#dc2626",
              }}
            >
              ₹{(insight.current_month.per_visit_yield / 1000).toFixed(0)}K/visit ({yieldDelta >= 0 ? "+" : ""}₹{Math.abs(yieldDelta / 1000).toFixed(0)}K vs team)
            </span>
          );
        })()}
        <span className="text-xs px-2 py-0.5 rounded-full font-semibold" style={{ background: "rgba(22,119,255,0.08)", color: "#1677FF" }}>
          {insight.current_month.active_cases} active · {insight.current_month.resolved_cases} resolved
        </span>
        <span className="text-xs px-2 py-0.5 rounded-full font-semibold" style={{ background: "rgba(100,116,139,0.08)", color: "#475569" }}>
          {insight.current_month.visits_today} visits today
        </span>
      </div>

      {/* Recommended action */}
      <div
        className="flex items-start gap-2 rounded-lg px-2.5 py-2"
        style={{ background: "rgba(124,58,237,0.06)", border: "1px solid rgba(124,58,237,0.12)" }}
      >
        <span className="text-xs font-bold flex-shrink-0 mt-0.5" style={{ color: "#7c3aed" }}>ACTION</span>
        <p className="text-xs leading-snug" style={{ color: "#4b5563" }}>{insight.recommended_action}</p>
      </div>
    </div>
  );
}

function ReallocationModal({ plan, onClose }: { plan: ReallocationPlan; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  useModalA11y(true, panelRef, onClose);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Reallocation plan"
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className="w-full sm:max-w-2xl max-h-[92svh] sm:max-h-[85svh] flex flex-col rounded-t-[22px] sm:rounded-[22px] overflow-hidden outline-none"
        style={{ background: "#fff" }}
      >
        {/* Grab handle — sheet affordance, phone only */}
        <div className="sm:hidden flex justify-center pt-2.5 pb-1 flex-shrink-0">
          <span style={{ width: 36, height: 4, borderRadius: 999, background: "#C4C6CF" }} />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 sm:py-4" style={{ borderBottom: "1px solid #EAEBEF" }}>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Shuffle className="w-4 h-4 flex-shrink-0" style={{ color: "#475569" }} />
              <span className="font-bold text-base truncate" style={{ color: "#1C1C1F" }}>Reallocation Plan</span>
            </div>
            <p className="text-xs mt-0.5 truncate" style={{ color: "#6B6D76" }}>
              From {plan.from_agent.name} · {plan.from_agent.territory} · {plan.from_agent.tier}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close reallocation plan" className="tap-target p-2 rounded-xl transition-colors hover:bg-slate-100 flex items-center justify-center flex-shrink-0">
            <X className="w-5 h-5" style={{ color: "#6B6D76" }} />
          </button>
        </div>

        {/* Summary bar */}
        <div className="px-4 sm:px-5 py-3 flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ background: "#F5F6F9", borderBottom: "1px solid #EAEBEF" }}>
          <span><strong className="text-success-600">{plan.summary.can_reallocate}</strong> cases can be reallocated</span>
          <span><strong style={{ color: "#6B6D76" }}>{plan.summary.cannot_reallocate}</strong> at capacity</span>
          <span><strong className="text-brand-600">{plan.summary.agents_receiving}</strong> agent{plan.summary.agents_receiving !== 1 ? "s" : ""} receiving</span>
        </div>

        {/* Case list */}
        <div className="flex-1 overflow-y-auto divide-y" style={{ borderColor: "#EAEBEF" }}>
          {plan.suggested_reallocations.length === 0 && (
            <div className="py-12 text-center" style={{ color: "#6B6D76" }}>
              <Shuffle className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="text-sm">No cases can be reallocated — all agents at capacity</p>
            </div>
          )}
          {plan.suggested_reallocations.map((r) => (
            <div key={r.case_id} className="px-4 sm:px-5 py-3">
              {/* Target agent drops below the case once the row is too narrow
                  to hold both without truncating the customer name. */}
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1.5 sm:gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold" style={{ color: "#1C1C1F" }}>{r.customer_name}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded-full font-bold" style={{
                      background: r.dpd_bucket === "NPA" ? "rgba(124,58,237,0.10)" : r.dpd_bucket === "BUCKET_3" ? "rgba(220,38,38,0.10)" : "rgba(245,158,11,0.10)",
                      color: r.dpd_bucket === "NPA" ? "#7c3aed" : r.dpd_bucket === "BUCKET_3" ? "#dc2626" : "#d97706",
                    }}>{r.dpd_bucket.replace("_", " ")}</span>
                    <span className="text-xs" style={{ color: "#6B6D76" }}>{r.case_number}</span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs" style={{ color: "#6B6D76" }}>
                    <span className="flex items-center gap-0.5"><IndianRupee className="w-3 h-3" />{(r.target_amount / 1000).toFixed(0)}K target</span>
                    <span>{r.priority} priority</span>
                    <span>DPD {r.dpd}</span>
                  </div>
                </div>
                <div className="text-left sm:text-right flex-shrink-0">
                  <p className="text-xs font-bold" style={{ color: "#0C66E4" }}>→ {r.to_agent_name}</p>
                  <p className="text-xs" style={{ color: "#6B6D76" }}>{r.to_agent_tier} · {r.to_agent_available_slots} slots free</p>
                </div>
              </div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <Brain className="w-3 h-3 flex-shrink-0" style={{ color: "#a855f7" }} />
                <p className="text-xs" style={{ color: "#7c3aed" }}>{r.match_reason}</p>
              </div>
            </div>
          ))}
          {plan.unallocatable_cases.length > 0 && (
            <div className="px-4 sm:px-5 py-3">
              <p className="text-xs font-semibold mb-2" style={{ color: "#dc2626" }}>Cannot reallocate ({plan.unallocatable_cases.length})</p>
              {plan.unallocatable_cases.map((u) => (
                <div key={u.case_number} className="flex flex-wrap justify-between gap-x-3 text-xs py-1" style={{ color: "#6B6D76" }}>
                  <span className="min-w-0">{u.customer_name} · {u.case_number}</span>
                  <span className="flex-shrink-0">₹{(u.target_amount / 1000).toFixed(0)}K · {u.reason}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 sm:px-5 py-4 flex gap-3 safe-bottom" style={{ borderTop: "1px solid #EAEBEF" }}>
          <button
            onClick={() => { toast.success(`Reallocation plan logged for ${plan.from_agent.name}`); onClose(); }}
            className="tap-target flex-1 py-2.5 rounded-xl text-sm font-semibold text-white transition hover:brightness-95"
            style={{ background: "#0C66E4" }}
          >
            Apply Plan
          </button>
          <button
            onClick={onClose}
            className="tap-target px-4 py-2.5 rounded-xl text-sm font-semibold transition hover:brightness-95"
            style={{ background: "#F5F6F9", color: "#475569" }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
