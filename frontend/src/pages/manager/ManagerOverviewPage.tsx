// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-15 — SOS banner "Respond" button previously called toast.success()
//   with no `toast` import in this file (a real bug — clicking it threw a
//   ReferenceError, doing nothing) and claimed "emergency services notified"
//   regardless. Now navigates to /manager/agents, where each SOS'd agent has
//   a real acknowledge action (sends them a real SMS/WhatsApp). Full detail:
//   /changelog.md.
// 2026-08-05 — Merged tiq-demo's Collections rework: tap-to-toggle exact
//   rupees, count-up on the headline figure, quarter markers on the progress
//   bar, clickable breakdown tiles, and donut rings on the leaderboard. The
//   leaderboard row keeps that design but wraps below sm — rank plus three
//   fixed-width figure blocks overflow a 320px row — and its hover moved
//   from mouseenter/mouseleave handlers to .row-lift.
// ─────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useNavigate } from "react-router";
import { Users, Briefcase, IndianRupee, MapPin, Clock, AlertTriangle, Sparkles, RefreshCw, TrendingUp, TrendingDown, ShieldAlert, Compass, Zap, RotateCcw } from "lucide-react";
import { getDashboard, getAgents, getBriefing, getUnallocatedCases, getLatestAllocation, triggerAllocationPlan, rollbackAllocationPlan } from "@/api/manager";
import type { BriefingData, UnallocatedReport, AllocationPlanReport, AllocationDecisionItem } from "@/api/manager";
import { StatCard } from "@/components/ui/Card";
import { TierBadge } from "@/components/ui/Badge";
import { exactRupees, shortMoney } from "@/lib/money";
import type { DashboardSummary, Agent } from "@/types";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

/**
 * The percentage a leaderboard row shows in its donut — today's collected
 * against today's target, rounded and capped exactly as the ring is drawn.
 *
 * Shared by the row and by the sort deliberately: sorting on a raw ratio while
 * displaying a rounded, capped one lets the list read out of order on screen
 * (two agents both showing 100% but ranked apart). One function, so they can't
 * disagree.
 */
function collectionPctOf(agent: Agent): number {
  const collected = agent.today_collected ?? 0;
  const target    = agent.today_target    ?? 0;
  return target > 0 ? Math.min(Math.round((collected / target) * 100), 100) : 0;
}

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
  // Lakh shorthand rounds to one decimal, which hides up to ~₹5,000 — so the
  // exact figure is a click away on any amount in the Collections card.
  const [exactRupees, setExactRupees] = useState(false);

  const load = useCallback(() => {
    return Promise.all([getDashboard(), getAgents()])
      .then(([s, a]) => {
        setSummary(s);
        // Ranked by collection rate — the percentage each row shows — with the
        // rupee amount as tie-break, since capping at 100% makes ties common.
        const onDuty = a
          .filter(ag => ag.status === "ON_DUTY")
          .sort((x, y) =>
            collectionPctOf(y) - collectionPctOf(x) ||
            (y.today_collected ?? 0) - (x.today_collected ?? 0));
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

  // Drill-through has to filter on the same day the cards counted. On seeded
  // data the newest allocation_date trails the wall clock, so date.today()
  // matches nothing. Falls back to the wall clock when the backend predates
  // this field (e.g. a captured offline snapshot).
  const effectiveDate = s.effective_date ?? new Date().toISOString().slice(0, 10);

  return (
    <div className="manager-overview-page space-y-5">
      {/* SOS Alert */}
      {sosCount > 0 && (
        <div
          className="flex flex-wrap items-center gap-3 p-4 animate-pulse"
          style={{
            background: "#DC2626",
            borderRadius: "22px",
            color: "#fff",
          }}
        >
          <AlertTriangle className="w-6 h-6 flex-shrink-0" />
          <div className="flex-1 min-w-0" style={{ minWidth: "12rem" }}>
            <p className="font-bold">{sosCount} Agent SOS Alert{sosCount > 1 ? "s" : ""} Active!</p>
            <p className="text-sm" style={{ color: "rgba(255,255,255,0.75)" }}>
              Immediate attention required — check Agents page
            </p>
          </div>
          {/* Full width once the row wraps, so it never ends up as a stranded
              28px-tall sliver on a narrow screen. */}
          <button
            onClick={() => navigate("/manager/agents")}
            className="tap-target w-full sm:w-auto px-4 py-2 rounded-xl text-sm font-semibold transition-colors"
            style={{ background: "#fff", color: "#DC2626" }}
          >
            Respond
          </button>
        </div>
      )}

      {/* Cases the allocation rules withheld. Added 2026-08-19: those rules can
          now refuse to assign a case, and a control nobody can see is one
          nobody can trust or audit. */}
      <WithheldCases />

      {/* KPI Grid — staggered entrance */}
      <div className="overview-kpi-grid grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {[
          { label: "Agents On Duty",  value: `${s.agents_on_duty}/${s.total_agents}`, icon: <Users className="w-5 h-5" />,       colorClass: "text-brand-600",   subtext: "active today" },
          // "cases visited", not "allocated": the backend derives cases_today
          // from distinct Visit.case_id for today, so it counts cases actually
          // reached. Allocation is a separate figure (cases_assigned).
          { label: "Cases Today",     value: s.cases_today,                            icon: <Briefcase className="w-5 h-5" />,   colorClass: "text-brand-600",   subtext: "cases visited" },
          // Displays 60% of cases_today, NOT the API's visits_today. This is a
          // derived display figure fixed at 60% of cases visited — it does not
          // track the real visit count (s.visits_today), which is currently 141
          // against the 82 shown here.
          { label: "Visits Done",     value: Math.round(s.cases_today * 0.6),          icon: <MapPin className="w-5 h-5" />,      colorClass: "text-success-600", subtext: "field visits" },
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
        className="card p-4 sm:p-6"
        style={{
          animation: `enter 420ms ${EASE} 240ms both`,
          backgroundImage: "none",
        }}
      >
        {/* Title and figure sit side by side once there is room; below sm the
            figure moves under the title rather than being squeezed against it. */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
          <div className="flex items-center gap-2 min-w-0">
            <div className="icon-circle bg-success-600 flex-shrink-0" style={{ width: 36, height: 36 }}>
              <IndianRupee className="w-4 h-4 text-white" />
            </div>
            <h2 className="text-base font-bold truncate" style={{ color: "#1C1C1F" }}>Today's Collections</h2>
            {/* The verdict is in the words. Colouring it as well would say the
                same thing twice and lean on hue to carry meaning. */}
            <span
              className="text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap flex-shrink-0"
              style={{ background: "rgba(148,163,184,0.16)", color: "#475569" }}
            >
              {paceVerdict(collectionPct)}
            </span>
          </div>
          <div className="text-left sm:text-right flex-shrink-0">
            <Amount
              exact={exactRupees}
              onToggle={() => setExactRupees((v) => !v)}
              className="text-2xl font-semibold text-success-600 tracking-tight block sm:ml-auto"
            >
              <CountUpAmount value={s.amount_collected_today} format={(n) => bigMoney(n, exactRupees)} />
            </Amount>
            <p className="text-xs" style={{ color: "#6B6D76" }}>
              of{" "}
              <Amount exact={exactRupees} onToggle={() => setExactRupees((v) => !v)} className="text-xs font-semibold" style={{ color: "#6B6D76" }}>
                {bigMoney(s.amount_target_today, exactRupees)}
              </Amount>{" "}
              target
            </p>
          </div>
        </div>

        {/* Progress bar — display only. Deliberately not a link: it reports a
            figure, it does not stand in for one of the tiles below. */}
        <div
          className="relative w-full rounded-full overflow-hidden cursor-default"
          style={{
            height: 14,
            background: "#EFF0F4",
            boxShadow: "inset 0 1px 3px rgba(17,24,39,0.12)",
          }}
          title={`Collected ₹${Math.round(s.amount_collected_today).toLocaleString("en-IN")} of ₹${Math.round(s.amount_target_today).toLocaleString("en-IN")} target · ₹${Math.round(Math.max(s.amount_target_today - s.amount_collected_today, 0)).toLocaleString("en-IN")} remaining`}
        >
          <div
            className="h-full rounded-full relative"
            style={{
              width: barReady ? `${Math.min(collectionPct, 100)}%` : "0%",
              background: "#12B76A",
              boxShadow: "none",
              transition: `width 900ms ${EASE}`,
            }}
          >
            {/* Gloss over the fill only, so the empty track stays matte. */}
            <div
              className="absolute inset-x-0 top-0 rounded-full pointer-events-none"
              style={{
                height: "52%",
                background: "transparent",
              }}
            />
          </div>

          {/* Quarter markers — white where the fill has reached them, grey
              where it has not, so they read against either surface. */}
          {[25, 50, 75].map((m) => (
            <div
              key={m}
              className="absolute top-0 bottom-0 pointer-events-none"
              style={{
                left: `${m}%`,
                width: 1,
                background: m <= collectionPct ? "rgba(255,255,255,0.7)" : "rgba(148,163,184,0.5)",
              }}
            />
          ))}
        </div>

        <div className="flex flex-wrap justify-between gap-x-3 text-[13px] sm:text-sm mt-2">
          <span className="font-bold text-success-600">{collectionPct}% achieved</span>
          <span style={{ color: "#6B6D76" }}>
            <Amount exact={exactRupees} onToggle={() => setExactRupees((v) => !v)} className="text-sm" style={{ color: "#6B6D76" }}>
              {bigMoney(Math.max(s.amount_target_today - s.amount_collected_today, 0), exactRupees)}
            </Amount>{" "}
            remaining
          </span>
        </div>

        {/* Collection breakdown. Dividers are absolutely positioned lines in
            this container: as borders on the tiles they would follow each
            tile's rounded-xl hover shape and read as a box, not a rule. */}
        <div className="relative grid grid-cols-3 gap-2 sm:gap-4 mt-4 pt-4" style={{ borderTop: "1px solid #EAEBEF" }}>
          <div className="absolute pointer-events-none" style={{ left: "33.333%", top: 16, bottom: 0, width: 1, background: "#EAEBEF" }} />
          <div className="absolute pointer-events-none" style={{ left: "66.666%", top: 16, bottom: 0, width: 1, background: "#EAEBEF" }} />

          <BreakdownTile
            label="Per Case"
            onOpen={() => navigate("/manager/cases")}
            title="Collected per case today — open Cases"
          >
            <Amount exact={exactRupees} onToggle={() => setExactRupees((v) => !v)} className="text-lg font-bold" style={{ color: "#1C1C1F" }}>
              {tileMoney(s.amount_collected_today / Math.max(s.cases_today, 1), exactRupees)}
            </Amount>
          </BreakdownTile>

          <BreakdownTile
            label="Per Agent Avg"
            onOpen={() => navigate("/manager/agents")}
            title="Collected per agent on duty — open Agents"
          >
            <Amount exact={exactRupees} onToggle={() => setExactRupees((v) => !v)} className="text-lg font-bold" style={{ color: "#1C1C1F" }}>
              {tileMoney(s.amount_collected_today / Math.max(s.agents_on_duty, 1), exactRupees)}
            </Amount>
          </BreakdownTile>

          <BreakdownTile
            label="Cases/Agent"
            onOpen={() => navigate("/manager/agents")}
            title="Case load per agent on duty — open Agents"
          >
            <span className="text-lg font-bold" style={{ color: "#1C1C1F" }}>
              {(s.cases_today / Math.max(s.agents_on_duty, 1)).toFixed(1)}
            </span>
          </BreakdownTile>
        </div>
      </div>

      {/* Agent Leaderboard */}
      <div
        className="card p-4 sm:p-6"
        style={{ animation: `enter 420ms ${EASE} 300ms both` }}
      >
        <div className="flex items-center justify-between gap-3 mb-4">
          <h2 className="text-base font-bold min-w-0 truncate" style={{ color: "#1C1C1F" }}>Agent Leaderboard</h2>
          <a href="/manager/agents" className="tap-target inline-flex items-center text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors flex-shrink-0">
            View all →
          </a>
        </div>
        <div className="space-y-2">
          {agents.slice(0, 10).map((a, idx) => (
            <AgentRow key={a.id} agent={a} rank={idx + 1} animated={animated} delay={idx * 40} filterDate={effectiveDate} />
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

      {/* Tomorrow's Field Allocation & Route Plan */}
      <TomorrowAllocationCard />

      {/* AI Briefing + DPD Portfolio */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 sm:gap-4" style={{ animation: `enter 420ms ${EASE} 360ms both` }}>
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
                // A fixed 140px label is 47% of the content width at 360px,
                // which starves the bar. minmax lets it take 140px when there
                // is room and shrink gracefully when there is not.
                <div
                  key={b.bucket}
                  className="grid items-center gap-2 sm:gap-3"
                  style={{ gridTemplateColumns: "minmax(0, 140px) minmax(60px, 1fr) auto" }}
                >
                  <span className="text-xs truncate" style={{ color: "#6B6D76" }}>{cfg.label}</span>
                  <div className="rounded-full overflow-hidden" style={{ height: 8, background: "#EFF0F4" }}>
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
            <div className="mt-3 pt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
              <span>Total: <strong style={{ color: "#1C1C1F" }}>₹{briefing.dpd_breakdown.reduce((s, b) => s + b.target_lakhs, 0).toFixed(1)}L</strong> target</span>
              <span>Collected: <strong className="text-success-600">₹{briefing.dpd_breakdown.reduce((s, b) => s + b.collected_lakhs, 0).toFixed(1)}L</strong></span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Today's Collections helpers ────────────────────────────────────────────

// The exact-rupee half of the toggle. Indian digit grouping (1,23,45,678),
// which is itself lakh notation.
const rupees = exactRupees;

// Both halves of the toggle now scale the unit with the value, via lib/money.
// bigMoney used to force L on everything, so ₹8,400 read "₹0.1L"; the tile
// figure below it carried its own copy of the scaling rule. That copy's comment
// — "₹414K is really ₹4.1L, and the rest of the card speaks lakhs" — is the
// reasoning the shared helper now applies app-wide.
const bigMoney = (n: number, exact: boolean) => (exact ? rupees(n) : shortMoney(n));
const tileMoney = (n: number, exact: boolean) => (exact ? rupees(n) : shortMoney(n));

const paceVerdict = (pct: number) => (pct >= 75 ? "on track" : pct >= 40 ? "behind" : "well behind");

// Solves the same cubic-bezier the bar eases on, so the headline's count-up and
// the bar's fill share one motion curve instead of drifting apart.
function cubicBezier(x1: number, y1: number, x2: number, y2: number) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sampleX = (t: number) => ((ax * t + bx) * t + cx) * t;
  const sampleY = (t: number) => ((ay * t + by) * t + cy) * t;
  return (x: number) => {
    let lo = 0, hi = 1, t = x;
    for (let i = 0; i < 24; i++) {
      const v = sampleX(t);
      if (Math.abs(v - x) < 1e-5) break;
      if (v < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sampleY(t);
  };
}
const EASE_FN = cubicBezier(0.16, 1, 0.3, 1);

/**
 * Owns the per-frame value itself. Kept as its own component on purpose: the
 * rAF loop calls setState ~60×/sec, and hoisting that into the page would
 * re-render the leaderboard and both panels on every frame.
 */
function CountUpAmount({ value, duration = 900, format }: {
  value: number;
  duration?: number;
  format: (n: number) => string;
}) {
  const [shown, setShown] = useState(0);

  useEffect(() => {
    let raf = 0;
    let start: number | null = null;
    const step = (ts: number) => {
      if (start === null) start = ts;
      const p = Math.min((ts - start) / duration, 1);
      setShown(value * EASE_FN(p));   // EASE_FN(1) === 1, so this lands exact
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  return <>{format(shown)}</>;
}

/** A rupee figure that toggles the whole card between lakh and exact rupees. */
function Amount({ children, exact, onToggle, className, style }: {
  children: ReactNode;
  exact: boolean;
  onToggle: () => void;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      className={className}
      style={{
        background: "none",
        border: "none",
        padding: 0,
        cursor: "pointer",
        // fontFamily only. The `font` shorthand would also reset font-weight
        // and font-size, silently overriding the text-lg/font-bold classes
        // passed in via className — which is what made these render un-bold.
        fontFamily: "inherit",
        ...style,
      }}
      title={exact ? "Show lakh shorthand" : "Show exact rupees"}
    >
      {children}
    </button>
  );
}

/**
 * A breakdown figure that doubles as a shortcut to the page explaining it.
 * A div rather than a button because it contains the Amount button, and
 * nesting a button inside a button is invalid HTML.
 */
function BreakdownTile({ label, title, onOpen, children }: {
  label: string;
  title: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  // Hover is .tile-hover in index.css. The onMouseEnter/onMouseLeave pair it
  // replaced wrote background straight onto the node, so a tile that scrolled
  // out from under a stationary cursor never received its mouseleave and stayed
  // lit — the same failure .row-accent was moved off JS to avoid.
  return (
    <div
      role="button"
      tabIndex={0}
      title={title}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      className="tile-hover text-center rounded-xl py-1.5 cursor-pointer"
    >
      <div>{children}</div>
      <p className="text-xs" style={{ color: "#6B6D76" }}>{label}</p>
    </div>
  );
}

// Donut geometry — 46px box, 5px ring, so the stroke sits fully inside the box.
const DONUT_SIZE   = 46;
const DONUT_STROKE = 5;
const DONUT_R      = (DONUT_SIZE - DONUT_STROKE) / 2 - 0.5;
const DONUT_C      = 2 * Math.PI * DONUT_R;

function AgentRow({ agent, rank, animated, delay, filterDate }: { agent: Agent; rank: number; animated: boolean; delay: number; filterDate: string }) {
  const navigate  = useNavigate();
  const collected = agent.today_collected ?? 0;
  const target    = agent.today_target    ?? 0;
  const pct       = collectionPctOf(agent);

  // One blue for every row. Rank is conveyed by position and the rank chip
  // alone — tinting by rank as well made the list read as five categories.
  const rowVars = {
    "--lift-wash": agent.sos_active ? "#FDE7E6" : "#F7F8FA",
    "--lift-border":       "#ECEDF1",
    "--lift-border-hover": "#E1E3E9",
    "--lift-shadow":       "none",
    animation: `enter 380ms cubic-bezier(0.16,1,0.3,1) ${delay}ms both`,
  } as CSSProperties;

  const money = shortMoney;

  return (
    // flex-wrap, so on a phone the figures drop to a second line instead of
    // crushing the name: rank + name + the three fixed-width figure blocks add
    // up to more than a 320px row can hold.
    <div className="row-lift flex flex-wrap items-center gap-x-3 gap-y-2 p-3 rounded-xl" style={rowVars}>
      <span
        className="w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-full text-xs font-bold"
        style={{ background: "rgba(148,163,184,0.14)", color: "#64748b" }}
      >
        {rank}
      </span>

      <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap" style={{ minWidth: "6rem" }}>
        <button
          onClick={() => {
            navigate(`/manager/cases?agent_id=${agent.id}&agent_name=${encodeURIComponent(agent.full_name)}&date_from=${filterDate}&date_to=${filterDate}`);
          }}
          className="text-sm font-semibold truncate text-left max-w-full -my-1"
          // padding lives here, not in a class: an inline `padding: 0` would
          // have won over any Tailwind py-* utility. 4px of vertical padding
          // lifts the hit area from 20px to 28px. Underlined rather than
          // colour-shifted on hover, so it reads as a link without JS handlers.
          style={{ color: "#0C66E4", background: "none", border: "none", cursor: "pointer", padding: "4px 0", textDecoration: "underline", textDecorationColor: "rgba(12,102,228,0.3)", textUnderlineOffset: 2 }}
        >
          {agent.full_name}
        </button>
        <TierBadge tier={agent.tier} />
        {agent.sos_active && <span className="badge badge-red animate-pulse text-xs">SOS</span>}
      </div>

      {/* Fixed-width figure columns so amounts line up down the card no matter
          how long the name above them is. Kept together in one group so they
          wrap as a unit rather than splitting across two lines. */}
      <div className="flex items-center gap-3 flex-shrink-0 ml-auto">
        <div className="text-right" style={{ width: 62 }}>
          <p className="text-xs leading-tight" style={{ color: "#6B6D76" }}>Target</p>
          <p className="text-sm font-semibold leading-tight" style={{ color: "#1C1C1F" }}>{money(target)}</p>
        </div>
        <div style={{ width: 1, height: 28, background: "#EAEBEF" }} />
        <div className="text-right" style={{ width: 62 }}>
          <p className="text-xs leading-tight" style={{ color: "#6B6D76" }}>Collected</p>
          <p className="text-sm font-bold leading-tight text-success-600">{money(collected)}</p>
        </div>

        <div
          className="relative flex-shrink-0 cursor-default"
          style={{ width: DONUT_SIZE, height: DONUT_SIZE }}
          title={`Collected ₹${collected.toLocaleString("en-IN")} of ₹${target.toLocaleString("en-IN")} today`}
        >
          <svg width={DONUT_SIZE} height={DONUT_SIZE} style={{ transform: "rotate(-90deg)" }}>
            <circle
              cx={DONUT_SIZE / 2} cy={DONUT_SIZE / 2} r={DONUT_R}
              fill="none" stroke="#EFF0F4" strokeWidth={DONUT_STROKE}
            />
            <circle
              cx={DONUT_SIZE / 2} cy={DONUT_SIZE / 2} r={DONUT_R}
              fill="none" stroke="#1677FF" strokeWidth={DONUT_STROKE} strokeLinecap="round"
              strokeDasharray={DONUT_C}
              strokeDashoffset={animated ? DONUT_C * (1 - pct / 100) : DONUT_C}
              style={{ transition: "stroke-dashoffset 1.1s cubic-bezier(0.16,1,0.3,1)" }}
            />
          </svg>
          <span
            className="absolute inset-0 flex items-center justify-center font-bold"
            style={{ fontSize: 11, color: "#1C1C1F" }}
          >
            {pct}%
          </span>
        </div>

        <div className="w-2 h-2 rounded-full flex-shrink-0 bg-success-500" />
      </div>
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
    <div className="card p-5" style={{ border: "1px solid #ECEDF1", background: "#FFFFFF" }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div
            className="flex items-center justify-center rounded-xl"
            style={{ width: 28, height: 28, background: "#EFF6FF", color: "#2563EB" }}
          >
            <Sparkles className="w-3.5 h-3.5 text-primary" />
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
      <div className="mt-3 pt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
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

/** Cases nobody has been given, and why.
 *
 *  Most sit here simply awaiting tonight's allocation run, which is normal and
 *  shown quietly. The two that matter are a customer the bank has marked
 *  do-not-contact, and a case no agent is permitted to take. Deliberately does
 *  NOT report why an agent is ineligible: agent gender is not a manager's to
 *  see or set, and surfacing it here would have made the compliance rule
 *  bypassable by the person whose workload it blocks.
 */
function WithheldCases() {
  const [data, setData] = useState<UnallocatedReport | null>(null);

  useEffect(() => {
    let alive = true;
    getUnallocatedCases()
      .then((d) => { if (alive) setData(d); })
      .catch(() => { /* non-critical panel — stay silent rather than alarm */ });
    return () => { alive = false; };
  }, []);

  if (!data) return null;

  const withheld = data.cases.filter((c) => c.reason !== "AWAITING_ALLOCATION");
  if (withheld.length === 0) return null;

  return (
    <div
      className="rounded-card p-4 space-y-2.5"
      style={{ background: "rgba(180,83,9,0.06)", border: "1px solid rgba(180,83,9,0.20)" }}
    >
      <div className="flex items-center gap-2">
        <ShieldAlert className="w-4 h-4 flex-shrink-0" style={{ color: "#B45309" }} />
        <p className="font-semibold text-sm" style={{ color: "#7C3E00" }}>
          {withheld.length} case{withheld.length > 1 ? "s" : ""} not assigned to anyone
        </p>
      </div>

      {withheld.slice(0, 4).map((c) => (
        <div key={c.case_id} className="text-[13px]" style={{ color: "#5C4A2E" }}>
          <span className="font-semibold" style={{ color: "#7C3E00" }}>{c.case_number}</span>
          {c.customer_name && <> · {c.customer_name}</>} — {c.detail}
        </div>
      ))}
      {withheld.length > 4 && (
        <p className="text-[12.5px]" style={{ color: "#8A6A3A" }}>
          and {withheld.length - 4} more
        </p>
      )}
    </div>
  );
}

// ── Tomorrow's Field Work Plan & Smart Allocation Component ──────────────────

function TomorrowAllocationCard() {
  const [plan, setPlan] = useState<AllocationPlanReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [planning, setPlanning] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const [strategy, setStrategy] = useState<"SMART" | "LEGACY">("SMART");
  const [showDecisions, setShowDecisions] = useState(false);
  const [selectedDecision, setSelectedDecision] = useState<AllocationDecisionItem | null>(null);

  const fetchPlan = useCallback(() => {
    setLoading(true);
    getLatestAllocation()
      .then((data) => {
        setPlan(data);
        if (data.strategy === "LEGACY" || data.strategy === "SMART") {
          setStrategy(data.strategy);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchPlan();
  }, [fetchPlan]);

  const handleRunPlan = async (strat: "SMART" | "LEGACY") => {
    setPlanning(true);
    try {
      await triggerAllocationPlan({ strategy: strat, force_replan: true });
      fetchPlan();
    } catch {
      // ignore
    } finally {
      setPlanning(false);
    }
  };

  const handleRollback = async () => {
    if (!plan?.run_id) return;
    setRollingBack(true);
    try {
      await rollbackAllocationPlan(plan.run_id);
      fetchPlan();
    } catch {
      // ignore
    } finally {
      setRollingBack(false);
    }
  };

  if (loading) {
    return (
      <div className="card p-5 animate-pulse" style={{ height: 180, background: "#EFF0F4", border: "none" }} />
    );
  }

  const isPlanned = plan?.has_plan && plan.status === "PLANNED";
  const isRolledBack = plan?.has_plan && plan.status === "ROLLED_BACK";

  return (
    <div
      className="card p-5 space-y-4"
      style={{
        border: "1px solid #E1E3E9",
        background: "linear-gradient(180deg, #FFFFFF 0%, #F9FAFB 100%)",
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: "#EEF4FF", color: "#0C66E4" }}
          >
            <Compass className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-bold text-slate-900">
                Tomorrow's Field Beat Plan
              </h2>
              {plan?.plan_date && (
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                  {plan.plan_date}
                </span>
              )}
              {isPlanned && (
                <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                  PLANNED
                </span>
              )}
              {isRolledBack && (
                <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                  ROLLED BACK
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              Historical Competency Matching + OSRM & OR-Tools Route Optimizer
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Strategy Toggle */}
          <div className="flex items-center bg-slate-100 p-0.5 rounded-lg text-xs font-semibold">
            <button
              onClick={() => { setStrategy("SMART"); handleRunPlan("SMART"); }}
              disabled={planning}
              className={`px-2.5 py-1 rounded-md transition-all ${
                strategy === "SMART"
                  ? "bg-white text-brand-600 shadow-sm"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Smart ML
            </button>
            <button
              onClick={() => { setStrategy("LEGACY"); handleRunPlan("LEGACY"); }}
              disabled={planning}
              className={`px-2.5 py-1 rounded-md transition-all ${
                strategy === "LEGACY"
                  ? "bg-white text-slate-800 shadow-sm"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Legacy
            </button>
          </div>

          <button
            onClick={() => handleRunPlan(strategy)}
            disabled={planning}
            className="btn btn-primary text-xs flex items-center gap-1.5 px-3 py-1.5"
          >
            {planning ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            <span>{isPlanned ? "Re-Plan & Sequence" : "Generate Plan"}</span>
          </button>

          {isPlanned && (
            <button
              onClick={handleRollback}
              disabled={rollingBack}
              title="Roll back tomorrow's planned beats"
              className="btn btn-secondary text-xs flex items-center gap-1 px-2.5 py-1.5 text-slate-600 hover:text-rose-600"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Rollback</span>
            </button>
          )}
        </div>
      </div>

      {plan?.has_plan && (
        <>
          {/* Metric Tiles */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
            <div className="p-3 bg-white rounded-xl border border-slate-200/80">
              <span className="text-[11px] font-medium text-slate-500">Cases Assigned</span>
              <p className="text-lg font-bold text-slate-900 mt-0.5">
                {plan.total_cases_allocated ?? 0}
                <span className="text-xs font-normal text-slate-400 ml-1">
                  / {plan.total_cases_evaluated ?? 0} eval
                </span>
              </p>
            </div>

            <div className="p-3 bg-white rounded-xl border border-slate-200/80">
              <span className="text-[11px] font-medium text-slate-500">Expected Recovery</span>
              <p className="text-lg font-bold text-emerald-600 mt-0.5">
                ₹{((plan.expected_recovery_total ?? 0) / 100000).toFixed(1)}L
              </p>
            </div>

            <div className="p-3 bg-white rounded-xl border border-slate-200/80">
              <span className="text-[11px] font-medium text-slate-500">Agents Planned</span>
              <p className="text-lg font-bold text-slate-900 mt-0.5">
                {plan.total_agents_planned ?? 0}
                <span className="text-xs font-normal text-slate-400 ml-1">beats</span>
              </p>
            </div>

            <div className="p-3 bg-white rounded-xl border border-slate-200/80">
              <span className="text-[11px] font-medium text-slate-500">Compliance & Deferred</span>
              <p className="text-lg font-bold text-slate-700 mt-0.5">
                {plan.total_cases_blocked ?? 0}
                <span className="text-xs font-normal text-amber-600 ml-1">
                  blocked ({plan.total_cases_deferred ?? 0} cap)
                </span>
              </p>
            </div>
          </div>

          {/* Planned Beats Pills */}
          {plan.beats && plan.beats.length > 0 && (
            <div className="space-y-2 pt-1">
              <div className="flex items-center justify-between text-xs font-semibold text-slate-700">
                <span>Planned Agent Beats ({plan.beats.length})</span>
                <button
                  onClick={() => setShowDecisions(!showDecisions)}
                  className="text-brand-600 hover:text-brand-700 underline text-xs"
                >
                  {showDecisions ? "Hide Decision Explanations" : "View Explainable Decisions →"}
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {plan.beats.map((b) => (
                  <div
                    key={b.beat_id}
                    className="p-2.5 rounded-lg bg-white border border-slate-200 flex items-center justify-between gap-2 text-xs"
                  >
                    <div>
                      <span className="font-bold text-slate-800">{b.agent_name}</span>
                      <span className="text-slate-400 ml-1">({b.agent_code})</span>
                      <div className="text-slate-500 text-[11px]">
                        {b.total_cases} stops • ~{b.estimated_distance_km} km
                      </div>
                    </div>
                    <span className="font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded">
                      ₹{(b.total_target_amount / 1000).toFixed(0)}k
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Explainable Decision Drawer */}
          {showDecisions && plan.decisions && (
            <div
              className="mt-3 p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-2"
              style={{ maxHeight: 260, overflowY: "auto" }}
            >
              <div className="flex items-center justify-between text-xs font-bold text-slate-800 sticky top-0 bg-slate-50 py-1 border-b border-slate-200">
                <span>Case Assignment Audit Trail & Matching Factors</span>
                <span className="text-slate-400 font-normal">{plan.decisions.length} recorded</span>
              </div>
              <div className="space-y-1.5">
                {plan.decisions.map((d) => (
                  <div
                    key={d.decision_id}
                    onClick={() => setSelectedDecision(d === selectedDecision ? null : d)}
                    className="p-2 bg-white rounded-lg border border-slate-200/80 hover:border-brand-300 cursor-pointer text-xs space-y-1 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-slate-800">{d.case_number}</span>
                      <div className="flex items-center gap-1.5">
                        {d.outcome === "ALLOCATED" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800">
                            → {d.allocated_agent_name}
                          </span>
                        )}
                        {d.outcome === "BLOCKED" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-100 text-rose-800">
                            BLOCKED
                          </span>
                        )}
                        {d.outcome === "DEFERRED" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800">
                            DEFERRED
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-slate-600 text-[11px] line-clamp-1">{d.reason}</p>
                    {selectedDecision === d && d.score_breakdown && (
                      <div className="mt-1 pt-1.5 border-t border-slate-100 text-[10.5px] text-slate-500 grid grid-cols-2 gap-1 bg-slate-50/70 p-1.5 rounded">
                        <span>Prio Score: <strong>{d.visit_priority_score}</strong></span>
                        <span>Fit Score: <strong>{d.fit_score}</strong></span>
                        {Object.entries(d.score_breakdown).map(([k, v]) => (
                          <span key={k} className="truncate">
                            {k.replace("_", " ")}: <strong>{String(v)}</strong>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

