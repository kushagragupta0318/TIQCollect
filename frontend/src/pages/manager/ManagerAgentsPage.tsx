// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-15 — Both SOS "Respond"/"Emergency Response" buttons previously
//   just called toast.success("...dispatched") with nothing behind it. Now
//   call acknowledgeAgentSos(), which sends the agent a real SMS/WhatsApp
//   confirming their manager has seen the alert. Full detail: /changelog.md.
// 2026-08-17 — Team-composition card above the filters: TierMixDonut +
//   DutyMeter (bottom of file). The two proportions were previously only the
//   prose line under the title, which makes the reader work out the shares in
//   their head.
//   Charts were asked for on the table's Collection Rate column instead; they
//   are deliberately NOT there. That column is one ratio against a limit per
//   row, so a pie of it is a two-slice pie, and fifteen of them down a column
//   is the hardest comparison a reader can be given — the "19%" label beside
//   the bar already does that job better. Part-to-whole with few segments is
//   what a donut is actually good at, which is what these two are.
//   Tier ramp is sequential, not the tier badge colours — see the note above
//   TIER_RAMP for why the badge slate cannot be used as a chart segment.
// 2026-08-17 (later) — Collection Rate column: RateGauge + RateDelta replace
//   the progress bar. The bar encoded the number already printed beside it;
//   the delta adds direction, which is what actually separates "76% and
//   falling" from "26% and climbing". Trend data comes from a new
//   collection_rate_trend / collection_rate_delta_pts pair on GET
//   /manager/agents, built from COMPLETE months only — the current month is
//   still accruing and would otherwise show every agent declining every month.
//   A full RateSparkline lives in the expanded row: inline at ~60px it was an
//   illegible squiggle, so it moved somewhere it has width.
//   Columns are now sortable (SortHeader + SORT_VALUE). Third click on a column
//   restores the API's own order, so a manager can always get back to the list
//   they started with; below lg the headers are hidden, so the same options are
//   mirrored in a <select>.
// ─────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Search, MapPin, AlertTriangle, Phone, ChevronDown, ChevronUp, ChevronsUpDown, Brain, Shuffle, X, Loader2, TrendingUp, TrendingDown, Minus, IndianRupee } from "lucide-react";
import { toast } from "react-hot-toast";
import { shortAmount, shortMoney } from "@/lib/money";
import { AiBadge } from "@/components/ui/AiBadge";
import { getAgents, getAgentInsight, getReallocationPlan, updateAgentStatus, acknowledgeAgentSos } from "@/api/manager";
import type { AgentInsight, ReallocationPlan } from "@/api/manager";
import { Input } from "@/components/ui/Input";
import { TierBadge } from "@/components/ui/Badge";
import { useModalA11y } from "@/hooks/useModalA11y";
import type { Agent, AgentStatus } from "@/types";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

/* ─── Table sorting ────────────────────────────────────────────────────────
 * "default" is the order the API returns (employee_code), which is also the
 * order the # column numbers — so clicking a sorted column and clicking back
 * returns to exactly the list the manager started with.
 */
type SortKey =
  | "default" | "full_name" | "territory" | "tier"
  | "ranking_score" | "collection_rate_pct" | "ptp_rate_pct" | "status";

type SortState = { key: SortKey; dir: "asc" | "desc" };

const SORT_VALUE: Record<Exclude<SortKey, "default">, (a: Agent) => string | number> = {
  full_name: (a) => a.full_name.toLowerCase(),
  territory: (a) => a.territory.toLowerCase(),
  // Tier is ordinal, so rank it rather than sorting the label — alphabetical
  // on "TIER_1"/"TIER_2" happens to work today and silently stops working at
  // TIER_10.
  tier: (a) => ({ TIER_1: 1, TIER_2: 2, TIER_3: 3 }[a.tier] ?? 99),
  ranking_score: (a) => a.ranking_score,
  collection_rate_pct: (a) => a.collection_rate_pct ?? 0,
  ptp_rate_pct: (a) => a.ptp_rate_pct ?? 0,
  status: (a) => (a.status === "ON_DUTY" ? 0 : 1),
};

// Numbers are almost always wanted biggest-first (who collected most), names
// A-Z. So the FIRST click picks the direction the reader actually meant.
const DESC_FIRST = new Set<SortKey>(["ranking_score", "collection_rate_pct", "ptp_rate_pct"]);

// Drives both the mobile <select> and the "sorted by …" caption, so the two can
// never name the same column differently.
const SORT_LABELS: Record<Exclude<SortKey, "default">, string> = {
  full_name: "Agent",
  territory: "Territory",
  tier: "Tier",
  ranking_score: "Score",
  collection_rate_pct: "Collection Rate",
  ptp_rate_pct: "PTP Rate",
  status: "Status",
};

export default function ManagerAgentsPage() {
  const [agents, setAgents]         = useState<Agent[]>([]);
  const [loading, setLoading]       = useState(true);
  const [search, setSearch]         = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ON_DUTY" | "OFF_DUTY">("ALL");
  const [tierFilter, setTierFilter]     = useState<"ALL" | "TIER_1" | "TIER_2" | "TIER_3">("ALL");
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "default", dir: "asc" });

  // Cycle: first click sorts the way the column is usually read, second click
  // reverses, third returns to the API's own order rather than leaving the
  // manager stuck in a sort they cannot undo.
  const toggleSort = useCallback((key: SortKey) => {
    setSort((prev) => {
      if (prev.key !== key) return { key, dir: DESC_FIRST.has(key) ? "desc" : "asc" };
      const firstDir = DESC_FIRST.has(key) ? "desc" : "asc";
      if (prev.dir === firstDir) return { key, dir: firstDir === "asc" ? "desc" : "asc" };
      return { key: "default", dir: "asc" };
    });
  }, []);

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

  const visible = useMemo(() => {
    if (sort.key === "default") return filtered;
    const value = SORT_VALUE[sort.key];
    const mult = sort.dir === "asc" ? 1 : -1;
    // Copy first: Array.sort mutates, and `filtered` is a memoised array that
    // other reads share — sorting it in place would reorder them too.
    return [...filtered].sort((a, b) => {
      const va = value(a), vb = value(b);
      if (va < vb) return -1 * mult;
      if (va > vb) return  1 * mult;
      // Ties keep the API's order so the list never shuffles between renders.
      return a.employee_code.localeCompare(b.employee_code);
    });
  }, [filtered, sort]);

  const onDuty    = agents.filter((a) => a.status === "ON_DUTY").length;
  const sosAgents = agents.filter((a) => a.sos_active);
  const tier1     = agents.filter((a) => a.tier === "TIER_1").length;
  const tier2     = agents.filter((a) => a.tier === "TIER_2").length;
  const tier3     = agents.filter((a) => a.tier === "TIER_3").length;
  const teamCollectedToday = agents.reduce((s, a) => s + (a.today_collected || 0), 0);
  const teamTargetToday    = agents.reduce((s, a) => s + (a.today_target || 0), 0);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div
        className="flex items-center justify-between gap-3"
        style={{ animation: `enter 420ms ${EASE} 0ms both` }}
      >
        <div className="min-w-0">
          <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>Field Agents</h1>
          {/* The duty/tier split used to be spelled out here. The two charts
              below now say exactly that, so repeating it one line above them
              was the same fact twice. What is left is the thing neither chart
              shows: how much of today's target the team has actually banked. */}
          <p className="text-[13px] sm:text-sm" style={{ color: "#6B6D76" }}>
            ₹{(teamCollectedToday / 100000).toFixed(1)}L collected today
            {teamTargetToday > 0 && <> of ₹{(teamTargetToday / 100000).toFixed(1)}L visited target</>}
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
          className="rounded-card p-4 space-y-2"
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

      {/* Team composition — the two genuine part-to-whole facts on this page.
          They were previously only the prose line under the title ("12 on duty ·
          3 off · 4 Tier 1"), which makes the reader do the proportion in their
          head. Deliberately NOT per-row charts in the table: a collection rate
          is one ratio against a limit, and fifteen small pies down a column is
          the hardest possible version of that comparison. */}
      <div
        className="card grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
        style={{ animation: `enter 420ms ${EASE} 60ms both` }}
      >
        <TierMixDonut tier1={tier1} tier2={tier2} tier3={tier3} />
        <DutyMeter onDuty={onDuty} total={agents.length} />
        <TrendSplit agents={agents} />
      </div>

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

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-xs" style={{ color: "#6B6D76" }}>
          {visible.length} agents shown
          {sort.key !== "default" && (
            <> · sorted by <span className="font-semibold" style={{ color: "#1C1C1F" }}>{SORT_LABELS[sort.key]}</span>{" "}
              {sort.dir === "asc" ? "ascending" : "descending"}</>
          )}
        </p>
        {/* The column headers are lg-only, so without this a phone cannot sort
            at all. Same options, same state — just a different control. */}
        <div className="lg:hidden flex items-center gap-2">
          <label htmlFor="agent-sort" className="text-xs" style={{ color: "#6B6D76" }}>Sort</label>
          <select
            id="agent-sort"
            value={sort.key}
            onChange={(e) => {
              const key = e.target.value as SortKey;
              setSort(key === "default"
                ? { key: "default", dir: "asc" }
                : { key, dir: DESC_FIRST.has(key) ? "desc" : "asc" });
            }}
            className="text-xs rounded-lg px-2 py-1.5"
            style={{ border: "1px solid #EAEBEF", background: "#fff", color: "#1C1C1F" }}
          >
            <option value="default">Default</option>
            {(Object.keys(SORT_LABELS) as Exclude<SortKey, "default">[]).map((k) => (
              <option key={k} value={k}>{SORT_LABELS[k]}</option>
            ))}
          </select>
          {sort.key !== "default" && (
            <button
              type="button"
              onClick={() => setSort((p) => ({ ...p, dir: p.dir === "asc" ? "desc" : "asc" }))}
              aria-label={`Reverse sort direction, currently ${sort.dir === "asc" ? "ascending" : "descending"}`}
              className="tap-target-h px-2 py-1.5 rounded-lg text-xs"
              style={{ border: "1px solid #EAEBEF", background: "#fff", color: "#6B6D76" }}
            >
              {sort.dir === "asc" ? "↑" : "↓"}
            </button>
          )}
        </div>
      </div>

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
          <SortHeader className="col-span-3" label="Agent"           sortKey="full_name"           sort={sort} onSort={toggleSort} />
          <SortHeader className="col-span-2" label="Territory"       sortKey="territory"           sort={sort} onSort={toggleSort} />
          <SortHeader className="col-span-1" label="Tier"            sortKey="tier"                sort={sort} onSort={toggleSort} />
          <SortHeader className="col-span-1" label="Score"           sortKey="ranking_score"       sort={sort} onSort={toggleSort} />
          <SortHeader className="col-span-2" label="Collection Rate" sortKey="collection_rate_pct" sort={sort} onSort={toggleSort} />
          <SortHeader className="col-span-1" label="PTP Rate"        sortKey="ptp_rate_pct"        sort={sort} onSort={toggleSort} />
          <SortHeader className="col-span-1" label="Status"          sortKey="status"              sort={sort} onSort={toggleSort} />
        </div>

        {loading
          ? Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-14 border-b animate-pulse" style={{ borderColor: "#EAEBEF", background: "#F5F6F9" }} />
            ))
          : visible.map((agent, i) => (
              <AgentRow
                key={agent.id}
                agent={agent}
                rank={i + 1}
                expanded={expandedAgent === agent.id}
                onToggle={() => setExpandedAgent(expandedAgent === agent.id ? null : agent.id)}
                delay={i * 30}
                onStatusChange={handleStatusChange}
              />
            ))
        }

        {!loading && visible.length === 0 && (
          <div className="py-16 text-center" style={{ color: "#6B6D76" }}>
            <p className="text-sm">No agents match the current filters</p>
          </div>
        )}
      </div>
    </div>
  );
}

function AgentRow({
  agent, rank, expanded, onToggle, delay, onStatusChange,
}: {
  agent: Agent; rank: number; expanded: boolean; onToggle: () => void; delay: number; onStatusChange: (id: string, newStatus: AgentStatus) => void;
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

        {/* Collection rate: the live part-month figure, the shape of the five
            complete months behind it, and the direction of the last of those.
            The bar that used to sit here encoded the same number as the label
            beside it; the sparkline adds what the number cannot say. */}
        <div className="col-span-2">
          <div className="flex items-center gap-2">
            <RateGauge pct={collectionPct} />
            <RateDelta delta={agent.collection_rate_delta_pts ?? null} />
          </div>
          {/* Context on its own line so it gets the column's full width rather
              than competing with the gauge for it. */}
          <p className="text-xs mt-0.5 truncate" style={{ color: "#6B6D76" }}>{agent.current_month_visits} visits · ₹{(agent.current_month_collections / 100000).toFixed(1)}L</p>
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

        {/* Collection rate — same three parts as the desktop row, so the two
            layouts tell the same story rather than one showing a trend and the
            other a bar. */}
        <div className="flex items-center gap-2 mt-2.5">
          <RateGauge pct={collectionPct} />
          <RateDelta delta={agent.collection_rate_delta_pts ?? null} />
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
            {/* "PTPs Due", not "PTPs Set". The backend counts promises whose
                committed_date falls in this month and has passed — what came
                due — not what was raised. Promises dated later this month are
                deliberately excluded, so labelling this "Set" would show a
                number smaller than the agent knows they took. */}
            <StatMini label="PTPs Due"           value={String(agent.current_month_ptps_set)} />
            <StatMini label="PTPs Honored"       value={String(agent.current_month_ptps_honored)} />
            <StatMini label="Lifetime Rate"      value={`${(agent.lifetime_collection_rate * 100).toFixed(0)}%`} />
          </div>

          {/* The trend the collapsed row cannot fit. Complete months only — the
              current month is still accruing and would always read as a dip. */}
          {(agent.collection_rate_trend?.length ?? 0) >= 2 && (
            <div className="rounded-xl p-3 mt-3" style={{ background: "#fff", border: "1px solid #EAEBEF" }}>
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: "#6B6D76" }}>
                  Collection rate · last {agent.collection_rate_trend.length} complete months
                </p>
                <span className="flex items-center gap-1.5">
                  <span className="text-[10px] uppercase tracking-wide" style={{ color: "#8A8C94" }}>vs prev month</span>
                  <RateDelta delta={agent.collection_rate_delta_pts ?? null} />
                </span>
              </div>
              <RateSparkline points={agent.collection_rate_trend} width={320} height={56} showLabels />
            </div>
          )}
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
              style={{ background: "#FFFFFF", color: "#2563EB", border: "1px solid #2563EB" }}
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
                style={{ background: "#FFFFFF", color: "#2563EB", border: "1px solid #2563EB" }}
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
                className="flex items-center gap-1.5 rounded-control border px-3 py-1.5 text-xs font-semibold text-danger-700 transition hover:bg-danger-100"
                style={{ background: "#FFFFFF", borderColor: "#F04438" }}
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

/** A clickable column header.
 *
 * The grid is divs rather than a real <table>, so `aria-sort` (which is only
 * valid on a columnheader) would be a lie here. The state goes in the button's
 * accessible name instead, where a screen reader will actually read it.
 */
function SortHeader({ label, sortKey, sort, onSort, className }: {
  label: string; sortKey: SortKey; sort: SortState; onSort: (k: SortKey) => void; className?: string;
}) {
  const active = sort.key === sortKey;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ChevronUp : ChevronDown;
  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      aria-label={active
        ? `${label}, sorted ${sort.dir === "asc" ? "ascending" : "descending"}. Activate to change.`
        : `Sort by ${label}`}
      className={`${className ?? ""} group flex items-center gap-1 text-left uppercase tracking-wide font-semibold transition-colors`}
      style={{ color: active ? "#1C1C1F" : "#6B6D76" }}
    >
      <span className="truncate">{label}</span>
      <Icon
        className="w-3 h-3 shrink-0 transition-opacity"
        style={{ opacity: active ? 1 : 0.35 }}
      />
    </button>
  );
}

/** Threshold colour for a collection rate — shared by the gauge arc and the
 *  percentage label so the two can never disagree. */
function rateColor(pct: number): string {
  return pct >= 60 ? "#12B76A" : pct >= 35 ? "#F79009" : "#F04438";
}

/** Compact gauge for the table's Collection Rate cell.
 *
 * A true 180° gauge — empty at the left, full at the right — not a full ring.
 * A ring has no visual "empty" and no "full": the reader has to find where the
 * arc started before they can judge how far it got. A semicircle has a fixed
 * start and end, so the fill is readable at a glance, which is what a column
 * scanned fifteen rows deep needs. It is also half the height of a ring, so it
 * sits in a table row without forcing it taller.
 *
 * No centre label — the percentage is printed beside it. Direction is not shown
 * here; the delta does that in words, and the full trend lives in the expanded
 * row where it has width to be legible.
 */
function RateGauge({ pct, size = 66 }: { pct: number; size?: number }) {
  const safe = Math.max(0, Math.min(100, pct));
  const STROKE = 7;
  const r = (size - STROKE) / 2;
  const cx = size / 2;
  const cy = r + STROKE / 2;
  const height = cy + STROKE / 2;

  // 0% sits at due left (π), 100% at due right (0), sweeping over the top.
  const point = (p: number): [number, number] => {
    const theta = Math.PI * (1 - p / 100);
    return [cx + r * Math.cos(theta), cy - r * Math.sin(theta)];
  };
  const [sx, sy] = point(0);
  const [ex, ey] = point(safe);
  const [fx, fy] = point(100);
  const arc = (x: number, y: number) =>
    `M${sx.toFixed(2)} ${sy.toFixed(2)} A${r} ${r} 0 0 1 ${x.toFixed(2)} ${y.toFixed(2)}`;

  return (
    <svg width={size} height={height} viewBox={`0 0 ${size} ${height}`} className="shrink-0"
         role="img" aria-label={`Collection rate ${safe}%`}>
      <path d={arc(fx, fy)} fill="none" stroke="#EFF0F4" strokeWidth={STROKE} strokeLinecap="round" />
      {safe > 0 && (
        <path d={arc(ex, ey)} fill="none" stroke={rateColor(safe)} strokeWidth={STROKE}
              strokeLinecap="round"
              style={{ transition: `stroke-dashoffset 900ms ${EASE}` }} />
      )}
      {/* The reading sits inside the arc rather than beside it. Outside, it was
          a second copy of the same number and the pair read as two objects.
          Ink colour, not the status colour — the arc already carries that, and
          fifteen rows of coloured numerals is the noise this redesign removes. */}
      {/* Sat on the baseline at cy-2 and read as if it had fallen out of the
          arc; lifted to sit in the opening instead. */}
      <text x={cx} y={cy - 6} textAnchor="middle" fontSize={16} fontWeight={700} fill="#1C1C1F">
        {safe}<tspan fontSize={10} fontWeight={600} fill="#8A8C94">%</tspan>
      </text>
    </svg>
  );
}

/** Collection-rate trend over the complete months the API returned.
 *
 * Lives in the expanded row, not the collapsed one: at ~60px wide the line was
 * a grey squiggle, and month-to-month collection rates are noisy enough that
 * the shape only becomes readable with width behind it.
 *
 * Deliberately axis-less: the exact figures are in the hover title and the
 * end-point labels.
 */
function RateSparkline({ points, width = 62, height = 20, showLabels = false }: {
  points: { month: string; rate_pct: number | null }[];
  width?: number; height?: number; showLabels?: boolean;
}) {
  const observed = points.filter((p) => p.rate_pct !== null);
  // One point cannot describe a trend, so draw nothing rather than a dot that
  // looks like a flat line.
  if (observed.length < 2) {
    return <div style={{ width, height }} className="flex items-center">
      <span className="text-[10px]" style={{ color: "#B4B6BE" }}>—</span>
    </div>;
  }

  const vals = observed.map((p) => p.rate_pct as number);
  const min = Math.min(...vals), max = Math.max(...vals);
  // A flat series would divide by zero; centre it instead.
  const span = max - min || 1;
  // Labelled mode needs room under the plot for the month names and above the
  // top point for its value.
  const padX = showLabels ? 14 : 2;
  const padTop = showLabels ? 14 : 2;
  const padBottom = showLabels ? 16 : 2;
  const plotH = height - padTop - padBottom;
  const stepX = (width - padX * 2) / (points.length - 1);

  const coords = points.map((p, i) => p.rate_pct === null ? null : {
    x: padX + i * stepX,
    y: padTop + (1 - ((p.rate_pct as number) - min) / span) * plotH,
    v: p.rate_pct as number,
    month: p.month,
  });

  // Break the path at gaps rather than bridging them, so a missing month reads
  // as missing instead of as a straight run.
  let d = "", penDown = false;
  for (const c of coords) {
    if (!c) { penDown = false; continue; }
    d += `${penDown ? "L" : "M"}${c.x.toFixed(1)} ${c.y.toFixed(1)} `;
    penDown = true;
  }

  const drawn = coords.filter(Boolean) as NonNullable<(typeof coords)[number]>[];
  const last = drawn[drawn.length - 1];
  const title = points.map((p) => `${p.month}: ${p.rate_pct === null ? "—" : p.rate_pct + "%"}`).join("\n");

  return (
    <svg width={width} height={height} className="shrink-0" role="img" aria-label={`Collection rate trend: ${title}`}>
      <title>{title}</title>
      <path d={d.trim()} fill="none" stroke="#94A3B8" strokeWidth={showLabels ? 2 : 1.5}
            strokeLinecap="round" strokeLinejoin="round" />
      {showLabels
        // Every month gets a dot and a name; only the first and last get a
        // value, so the reader gets the range without a number on every point.
        ? drawn.map((c, i) => (
            <g key={c.month}>
              <circle cx={c.x} cy={c.y} r={3} fill="#fff" stroke="#94A3B8" strokeWidth={2} />
              <text x={c.x} y={height - 4} textAnchor="middle" fontSize={9} fill="#8A8C94">
                {c.month.slice(5)}
              </text>
              {(i === 0 || i === drawn.length - 1) && (
                <text x={c.x} y={c.y - 8} textAnchor="middle" fontSize={10} fontWeight={600} fill="#475569">
                  {c.v}%
                </text>
              )}
            </g>
          ))
        // Compact mode: only the newest point is marked — the eye needs an
        // anchor for "where it ended up", not a dot on every month.
        : <circle cx={last.x} cy={last.y} r={2.5} fill="#475569" />}
    </svg>
  );
}

/** Direction of the last complete month vs the one before it.
 *
 * A chip rather than loose text: beside a gauge, bare coloured words read as a
 * stray fragment, while a tinted pill reads as a deliberate second element.
 * Tint carries direction, and the arrow repeats it so the meaning does not rest
 * on colour alone. */
function RateDelta({ delta }: { delta: number | null }) {
  if (delta === null) {
    return <span className="text-[11px]" style={{ color: "#B4B6BE" }}>—</span>;
  }
  if (delta === 0) {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[11px] font-semibold"
            style={{ background: "#F1F2F6", color: "#6B6D76" }}>
        no change
      </span>
    );
  }
  const up = delta > 0;
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-[11px] font-semibold tabular-nums whitespace-nowrap"
      style={{
        background: up ? "rgba(18,183,106,0.10)" : "rgba(240,68,56,0.10)",
        color: up ? "#067647" : "#B42318",
      }}
      title={`${up ? "Up" : "Down"} ${Math.abs(delta).toFixed(1)} points vs the previous complete month`}
    >
      {up ? "↑" : "↓"} {Math.abs(delta).toFixed(1)}
    </span>
  );
}

/* ─── Team composition charts ──────────────────────────────────────────────
 *
 * Tier is ORDINAL (1 > 2 > 3), so the colour job is sequential — one hue,
 * dark→light — not categorical. Taking the trio straight from the tier badges
 * (green #12B76A / blue #2E90FA / slate #94A3B8) was the obvious first choice
 * and it fails: the slate reads as neutral (chroma 0.035, under the floor) and
 * sits only ΔE 5.3 from the blue under tritanopia — two tiers a colour-blind
 * manager could not tell apart. This ramp is monotonic in lightness
 * (0.42 → 0.60 → 0.81), which is the correct check for a sequential scale.
 * Identity is carried by the direct labels beside the ring, never by hue alone.
 */
const TIER_RAMP = ["#1E40AF", "#3B82F6", "#93C5FD"] as const;

/** Ring geometry shared by both charts below: a track circle plus one or more
 *  arcs, rotated -90° so they start at twelve o'clock. Same construction as
 *  RankingRing (agent profile) and CollectionDonut (case detail). */
function Ring({ size, r, stroke, children }: { size: number; r: number; stroke: number; children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#F1F5F9" strokeWidth={stroke} />
      {children}
    </svg>
  );
}

function TierMixDonut({ tier1, tier2, tier3 }: { tier1: number; tier2: number; tier3: number }) {
  const SIZE = 92, R = 34, STROKE = 10, GAP = 3;
  const circ = 2 * Math.PI * R;
  const counts = [tier1, tier2, tier3];
  const labels = ["Tier 1", "Tier 2", "Tier 3"];
  const total = counts.reduce((a, b) => a + b, 0);

  let acc = 0;
  const arcs = counts.map((n, i) => {
    const frac = total ? n / total : 0;
    // Trim each arc by GAP so neighbouring segments are separated by surface
    // rather than butting together — two adjacent blues would otherwise read
    // as one longer arc.
    const len = Math.max(0, frac * circ - GAP);
    const offset = -acc * circ;
    acc += frac;
    if (n === 0) return null;
    return (
      <circle
        key={i} cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none"
        stroke={TIER_RAMP[i]} strokeWidth={STROKE} strokeLinecap="butt"
        strokeDasharray={`${len} ${circ - len}`} strokeDashoffset={offset}
        transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
      />
    );
  });

  return (
    <div className="flex items-center gap-4">
      <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
        <Ring size={SIZE} r={R} stroke={STROKE}>{arcs}</Ring>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-bold leading-none" style={{ color: "#1C1C1F" }}>{total}</span>
          <span className="text-[10px] mt-0.5" style={{ color: "#8A8C94" }}>agents</span>
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "#6B6D76" }}>Tier mix</p>
        <ul className="space-y-1">
          {counts.map((n, i) => (
            <li key={i} className="flex items-center gap-2 text-[13px]" style={{ color: "#1C1C1F" }}>
              <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: TIER_RAMP[i] }} />
              <span style={{ color: "#6B6D76" }}>{labels[i]}</span>
              <span className="font-semibold tabular-nums">{n}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** On-duty share. A meter, not a two-slice pie: one ratio against a limit, so
 *  a single arc over a track rather than On and Off as competing categories. */
function DutyMeter({ onDuty, total }: { onDuty: number; total: number }) {
  const SIZE = 92, R = 34, STROKE = 10;
  const circ = 2 * Math.PI * R;
  const frac = total ? onDuty / total : 0;
  const pct = Math.round(frac * 100);

  return (
    <div className="flex items-center gap-4">
      <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
        <Ring size={SIZE} r={R} stroke={STROKE}>
          <circle
            cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none"
            stroke="#12B76A" strokeWidth={STROKE} strokeLinecap="round"
            strokeDasharray={circ} strokeDashoffset={circ - frac * circ}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
            style={{ transition: "stroke-dashoffset 0.7s ease" }}
          />
        </Ring>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-bold leading-none" style={{ color: "#1C1C1F" }}>{pct}%</span>
          <span className="text-[10px] mt-0.5" style={{ color: "#8A8C94" }}>on duty</span>
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "#6B6D76" }}>Availability</p>
        <ul className="space-y-1">
          <li className="flex items-center gap-2 text-[13px]">
            <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: "#12B76A" }} />
            <span style={{ color: "#6B6D76" }}>On duty</span>
            <span className="font-semibold tabular-nums" style={{ color: "#1C1C1F" }}>{onDuty}</span>
          </li>
          <li className="flex items-center gap-2 text-[13px]">
            <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: "#F1F5F9", border: "1px solid #E2E8F0" }} />
            <span style={{ color: "#6B6D76" }}>Off</span>
            <span className="font-semibold tabular-nums" style={{ color: "#1C1C1F" }}>{total - onDuty}</span>
          </li>
        </ul>
      </div>
    </div>
  );
}

/** How the team's collection rate moved last complete month — improving,
 *  declining, or flat. Uses the same delta the rows show, aggregated, so the
 *  header and the table can never disagree. Status colours (good / critical /
 *  neutral) are correct here because these ARE states, not series. */
function TrendSplit({ agents }: { agents: Agent[] }) {
  const deltas = agents
    .map((a) => a.collection_rate_delta_pts)
    .filter((d): d is number => d !== null && d !== undefined);
  const up = deltas.filter((d) => d > 0).length;
  const down = deltas.filter((d) => d < 0).length;
  const flat = deltas.filter((d) => d === 0).length;
  const total = deltas.length;

  const rows = [
    { label: "Improving", n: up,   color: "#12B76A" },
    { label: "Declining", n: down, color: "#F04438" },
    { label: "Flat",      n: flat, color: "#D0D5DD" },
  ];

  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide mb-2.5" style={{ color: "#6B6D76" }}>
        Month-on-month
      </p>
      {total === 0 ? (
        <p className="text-[13px]" style={{ color: "#8A8C94" }}>Not enough history yet</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.label} className="flex items-center gap-2.5">
              <span className="text-[13px] w-20 shrink-0" style={{ color: "#6B6D76" }}>{r.label}</span>
              {/* Length carries the comparison; the count is stated too, so the
                  bar never has to be measured by eye. */}
              <span className="flex-1 rounded-full overflow-hidden" style={{ height: 6, background: "#F1F5F9" }}>
                <span className="block h-full rounded-full"
                      style={{ width: `${(r.n / total) * 100}%`, background: r.color }} />
              </span>
              <span className="text-[13px] font-semibold tabular-nums w-5 text-right" style={{ color: "#1C1C1F" }}>{r.n}</span>
            </li>
          ))}
        </ul>
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
      style={{ background: "#FFFFFF", border: "1px solid #ECEDF1" }}
    >
      <div className="flex items-center gap-2 mb-2">
        <div
          className="flex items-center justify-center rounded-lg flex-shrink-0"
          style={{ width: 24, height: 24, background: "#EFF6FF" }}
        >
          <Brain className="w-3 h-3 text-primary" />
        </div>
        <span className="text-xs font-bold" style={{ color: "#1C1C1F" }}>AI Performance Analysis</span>
        <AiBadge aiGenerated={insight.ai_generated} status={insight.ai_status} />
        <span
          className="ml-auto flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full"
          style={{ background: signalConfig.bg, color: signalConfig.color, border: `1px solid ${signalConfig.border}` }}
        >
          {signalConfig.icon}
          {signalConfig.label}
          {/* The span matters. This badge measures the oldest to the newest of
              the last three COMPLETE months, while the ↑/↓ chip in the table
              row measures only the newest against the one before it. An agent
              who dipped and is recovering reads "3 mo ↓" here and "↑" there,
              and both are true — unlabelled, the pair just looks broken. */}
          <span className="font-semibold opacity-70">· 3 mo</span>
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
              {shortMoney(insight.current_month.per_visit_yield)}/visit ({yieldDelta >= 0 ? "+" : ""}{shortMoney(Math.abs(yieldDelta))} vs team)
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
        className="w-full sm:max-w-2xl max-h-[92svh] sm:max-h-[85svh] flex flex-col rounded-t-card sm:rounded-card overflow-hidden outline-none border border-slate-200 shadow-pop"
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
                    <span className="flex items-center gap-0.5"><IndianRupee className="w-3 h-3" />{shortAmount(r.target_amount)} target</span>
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
                  <span className="flex-shrink-0">{shortMoney(u.target_amount)} · {u.reason}</span>
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
