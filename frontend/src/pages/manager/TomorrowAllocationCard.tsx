// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-16 — MOVED OUT OF ManagerOverviewPage.tsx, VERBATIM.
//
//   This component — the objective switcher, Generate / Re-Plan, the four
//   plan tiles, the fifteen beat cards, Export CSV, Rollback and the
//   Case Assignment Audit Trail — was ~700 lines of a 1,576-line overview
//   page. It is a WORKFLOW (choose, plan, review, export, roll back), not a
//   glance-level KPI, and the "Show / Hide Details" toggle it grew existed only
//   because it was too big for the page it sat on. It now has its own route,
//   /manager/beat-plan (ManagerBeatPlanPage.tsx), and the overview keeps a
//   compact status strip (BeatPlanSummaryStrip below) that links here.
//
//   Nothing inside the component changed in the move except the one prop:
//   `defaultExpanded`, so the dedicated page opens expanded and the strip does
//   not need it. Every query key, mutation, toast and reason-line is as it was
//   on the overview, so the plan page and the strip share one React Query
//   cache entry ("manager", "allocation", "latest") and cannot disagree.
//
//   The changelog history for the code below lives in ManagerOverviewPage.tsx's
//   header (it was written there) and in the comments carried with each block.
// ─────────────────────────────────────────────────────────────────────────────

// MANAGER-SIDE WORDING, 2026-09-16. The manager pages say "field plan" and
// "route" where the code and the agent app say "beat". Bank clients and demo
// audiences do not know the word; agents and agency staff do, so the agent
// app keeps it. The model, the API fields and the route paths are unchanged
// (`/manager/beat-plan`, `plan.beats`, `beat_date` …) — this is labels only.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { AlertTriangle, ArrowRight, Compass, Download, IndianRupee, RefreshCw, RotateCcw, Route, Scale, Zap, ChevronDown, ChevronUp } from "lucide-react";
import { toast } from "react-hot-toast";
import { getLatestAllocation, triggerAllocationPlan, rollbackAllocationPlan, exportAllocationDecisions, getAllocationSettings, updateAllocationSettings } from "@/api/manager";
import type { AllocationDecisionItem } from "@/api/manager";
import { isTimeout } from "@/api/axios";
import { errorDetail, errorStatus } from "@/lib/apiError";
import { shortMoney } from "@/lib/money";
import { mlBadge, ownerTag, rankedReasons } from "./allocationReasons";


// Same four options as ManagerOverviewPage.AS_BEFORE, for the same reason: make
// React Query put exactly what the hand-rolled fetches put on the wire. Copied
// rather than exported from the page so this module does not import a page.
const AS_BEFORE = {
  retry: false,
  staleTime: 0,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
} as const;


/**
 * Ranked reasons for the case-assignment audit panel.
 *
 * The panel used to render Object.entries(score_breakdown) verbatim, so a manager
 * read internal key names and raw decimals — "affinity score: 0.804", and
 * "expected case_inn: 49631.54", that one severed by a replace("_", " ") that
 * only ever replaced the first underscore, then clipped by a CSS truncate.
 *
 * But formatting was never the real problem: ten unlabelled numbers is not an
 * explanation. Without a reference point a score says nothing about why this
 * case went to this agent.
 *
 * The allocator utility is a weighted sum, so weight x score is each factor's
 * literal share of the decision. global_allocator.py now keeps those shares as
 * score_breakdown.contributions instead of discarding them; this ranks them and
 * names them in English. No model and no generated prose — it is the same
 * arithmetic that made the decision, stated rather than dumped.
 *
 * Decisions written before 2026-09-02 carry no `contributions` key and fall back
 * to the old raw dump, so an existing plan stays readable until it is re-planned.
 */
type Objective = "BALANCED" | "MAX_RECOVERY" | "MIN_DISTANCE";

export function TomorrowAllocationCard({ defaultExpanded = false }: { defaultExpanded?: boolean }) {
  const planQ = useQuery({
    queryKey: ["manager", "allocation", "latest"],
    queryFn: () => getLatestAllocation(),
    ...AS_BEFORE,
  });
  const plan = planQ.data ?? null;
  // `isFetching`, not `isPending`: the old `fetchPlan` set `loading` true on
  // EVERY call, including the refetches after a re-plan and a rollback, so the
  // card showed its skeleton again each time. `isPending` would only cover the
  // first load and would silently drop that.
  const loading = planQ.isFetching;
  const refetchPlan = planQ.refetch;

  const settingsQ = useQuery({
    queryKey: ["manager", "allocation", "settings"],
    queryFn: getAllocationSettings,
    ...AS_BEFORE,
  });

  const [planning, setPlanning] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  // The saved objective SEEDS the switcher; the manager can then change it.
  // Derived rather than copied into state by an effect, so there is no
  // synchronous setState to schedule and no window in which the two disagree.
  //
  // ONE BEHAVIOUR DIFFERENCE, AND IT IS A FIX. The old effect called
  // `setObjective(settings.objective)` whenever the GET resolved. If the manager
  // clicked a different objective while that request was still in flight, the
  // late response overwrote their choice. Here their choice wins, because it is
  // checked first. The window was small and nobody is likely to have hit it, but
  // it is a real difference and not an accident of the rewrite.
  const [objectiveChoice, setObjectiveChoice] = useState<Objective | null>(null);
  const objective: Objective =
    objectiveChoice ?? (settingsQ.data?.objective as Objective | undefined) ?? "BALANCED";
  const setObjective = setObjectiveChoice;
  const [showDecisions, setShowDecisions] = useState(false);
  // "View Explainable Decisions" opens a ~900-row drawer BELOW the fold, and
  // until 2026-09-17 nothing moved the viewport, so a manager clicked and saw
  // no change. The drawer scrolls into view once it has rendered; a scroll in
  // the click handler would run before the element exists. Only on OPEN —
  // closing leaves the viewport alone. Honours prefers-reduced-motion.
  const decisionsRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!showDecisions || !decisionsRef.current) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    decisionsRef.current.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [showDecisions]);
  const [selectedDecision, setSelectedDecision] = useState<AllocationDecisionItem | null>(null);
  const [decisionFilter, setDecisionFilter] = useState<"ALL" | "ALLOCATED" | "DEFERRED" | "BLOCKED">("ALL");

  // Hoisted out of the memo, 2026-09-10. Reading `plan?.decisions` INSIDE it
  // made the React Compiler infer `plan` as the dependency while the array said
  // `plan?.decisions` — "inferred less specific property than source" — and
  // rather than pick a side it skipped optimizing this component entirely.
  // Binding the narrow value here keeps the dependency exactly as narrow as it
  // was and lets the two agree, so nothing recomputes more often than before.
  const decisions = plan?.decisions;

  const sortedDecisions = useMemo(() => {
    if (!decisions) return [];
    const outcomeOrder: Record<string, number> = {
      ALLOCATED: 1,
      DEFERRED: 2,
      DEFERRED_ROUTE_INFEASIBLE: 2,
      BLOCKED: 3,
    };
    const list = [...decisions].sort((a, b) => {
      const rankA = outcomeOrder[a.outcome] ?? 2;
      const rankB = outcomeOrder[b.outcome] ?? 2;
      if (rankA !== rankB) return rankA - rankB;
      return (b.visit_priority_score ?? 0) - (a.visit_priority_score ?? 0);
    });
    if (decisionFilter === "ALL") return list;
    if (decisionFilter === "DEFERRED") {
      return list.filter((d) => d.outcome === "DEFERRED" || d.outcome === "DEFERRED_ROUTE_INFEASIBLE");
    }
    return list.filter((d) => d.outcome === decisionFilter);
  }, [decisions, decisionFilter]);

  const handleRunPlan = async (
    obj?: "BALANCED" | "MAX_RECOVERY" | "MIN_DISTANCE"
  ) => {
    const activeObj = obj || objective;
    setPlanning(true);
    try {
      await triggerAllocationPlan({
        strategy: "SMART",
        objective: activeObj,
        force_replan: true,
      });
      toast.success("Tomorrow's field plan generated & sequenced!");
      void refetchPlan();
    } catch (err) {
      // A TIMEOUT IS NOT A FAILURE, and must not be reported as one. The
      // request keeps running on the server and usually succeeds; telling the
      // user to "try again" sends a second request into the first one, which
      // the planner answers with a 409. Refresh instead — the plan is very
      // likely already there.
      if (isTimeout(err)) {
        toast.error(
          "Still generating — this can take a couple of minutes on a large " +
          "book. Do not re-run; refresh in a moment to see the plan."
        );
        void refetchPlan();
      } else if (errorStatus(err) === 409) {
        toast.error(errorDetail(err, "A plan is already being generated."));
      } else {
        toast.error(errorDetail(err, "Failed to generate plan — please try again"));
      }
    } finally {
      setPlanning(false);
    }
  };

  const handleSelectObjective = (obj: "BALANCED" | "MAX_RECOVERY" | "MIN_DISTANCE") => {
    setObjective(obj);
    updateAllocationSettings({ objective: obj }).catch(() => {});
    handleRunPlan(obj);
  };

  const handleRollback = async () => {
    if (!plan?.run_id) return;
    setRollingBack(true);
    try {
      await rollbackAllocationPlan(plan.run_id);
      toast.success("Allocation plan rolled back successfully");
      void refetchPlan();
    } catch {
      toast.error("Failed to roll back plan");
    } finally {
      setRollingBack(false);
    }
  };

  const handleExportCSV = async () => {
    if (!plan?.run_id) return;
    try {
      toast.loading("Generating CSV export...", { id: "csv-export" });
      const blob = await exportAllocationDecisions(plan.run_id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `allocation_decisions_${plan.plan_date || "plan"}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success("CSV exported successfully!", { id: "csv-export" });
    } catch {
      toast.error("Failed to export decisions CSV", { id: "csv-export" });
    }
  };

  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  if (loading) {
    return (
      <div className="card p-5 animate-pulse" style={{ height: 180, background: "#EFF0F4", border: "none" }} />
    );
  }

  const isPlanned = plan?.has_plan && plan.status === "PLANNED";
  const isRolledBack = plan?.has_plan && plan.status === "ROLLED_BACK";

  // Subtitle. This read "Global Bipartite Optimization + OR-Tools VRPTW Route
  // Sequencing" — a hardcoded string, aimed at the wrong reader and not reliably
  // true. An agency manager does not price a plan on whether it was solved with a
  // bipartite matching, and core/routing.py degrades twice over: OSRM falls back
  // to Haversine when unreachable (routing.py:87) and OR-Tools falls back to a
  // nearest-neighbour walk when the solve throws (routing.py:201). With
  // OSRM_BASE_URL defaulting to the public demo server, a plan built by neither
  // named technique was still being labelled with both.
  //
  // What a manager actually wants off this line is what the plan optimised for
  // and how much driving it implies. Both are already here: the objective drives
  // the switcher below, and estimated_distance_km is stored per beat. Route
  // length is used rather than cluster spread because it is the figure a manager
  // already reads on each beat card, and it maps to fuel and hours.
  const OBJECTIVE_BLURB: Record<string, string> = {
    BALANCED: "Balanced for recovery and travel",
    MAX_RECOVERY: "Prioritising recovery value",
    MIN_DISTANCE: "Prioritising short routes",
  };
  // One entry per preset the allocator understands. `hint` is the plain-words
  // account of what the preset trades off, shown as the tooltip and as the
  // caption under the control once selected.
  const OBJECTIVE_OPTIONS: ReadonlyArray<{
    key: Objective;
    label: string;
    Icon: typeof Scale;
    activeClass: string;
    hint: string;
  }> = [
    {
      key: "BALANCED",
      label: "Balanced",
      Icon: Scale,
      activeClass: "text-brand-600",
      hint: "Weighs expected recovery and travel distance roughly equally.",
    },
    {
      key: "MAX_RECOVERY",
      label: "Max Recovery",
      Icon: IndianRupee,
      activeClass: "text-emerald-700",
      hint: "Sends agents to the highest expected-recovery cases, accepting longer routes.",
    },
    {
      key: "MIN_DISTANCE",
      label: "Min Distance",
      Icon: Route,
      activeClass: "text-blue-700",
      hint: "Keeps each agent's route short, accepting lower expected recovery.",
    },
  ];
  const routeKms = (plan?.beats ?? [])
    .map((b) => b.estimated_distance_km)
    .filter((k) => typeof k === "number" && k > 0);
  const avgRouteKm = routeKms.length
    ? routeKms.reduce((a, b) => a + b, 0) / routeKms.length
    : 0;
  const planSubtitle = [
    OBJECTIVE_BLURB[objective] ?? OBJECTIVE_BLURB.BALANCED,
    plan?.has_plan && (plan.total_agents_planned ?? 0) > 0
      ? `${plan.total_agents_planned} agent routes`
      : null,
    avgRouteKm > 0 ? `~${Math.round(avgRouteKm)} km average route` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className="card p-5 transition-all duration-200"
      style={{
        border: "1px solid #E1E3E9",
        background: "linear-gradient(180deg, #FFFFFF 0%, #F9FAFB 100%)",
      }}
    >
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex flex-wrap items-start justify-between gap-3 cursor-pointer select-none"
      >
        <div className="flex items-center gap-2.5">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: "#EEF4FF", color: "#0C66E4" }}
          >
            <Compass className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-bold text-slate-900 hover:text-brand-600 transition-colors">
                Tomorrow's Field Plan
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
              {!isExpanded && plan?.has_plan && (
                <span className="text-xs font-medium text-slate-600 bg-slate-100 px-2.5 py-0.5 rounded-full border border-slate-200">
                  {plan.total_cases_allocated ?? 0} cases • ₹{(((plan.allocated_collectable_total || plan.expected_recovery_total) ?? 0) / 100000).toFixed(1)}L Expected Recovery • {plan.total_agents_planned ?? 0} agent routes
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              {planSubtitle}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap" onClick={(e) => e.stopPropagation()}>
          {isExpanded && isPlanned && (
            <>
              <button
                onClick={handleExportCSV}
                title="Download decisions audit CSV"
                className="btn btn-secondary text-xs flex items-center gap-1.5 px-3 py-1.5"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Export CSV</span>
              </button>
              <button
                onClick={handleRollback}
                disabled={rollingBack}
                title="Roll back tomorrow's planned routes"
                className="btn btn-secondary text-xs flex items-center gap-1.5 px-3 py-1.5 hover:text-rose-600"
              >
                {rollingBack ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                <span>Rollback</span>
              </button>
            </>
          )}

          {/* Toggle Details Chevron Button */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="btn btn-secondary text-xs flex items-center gap-1.5 px-3 py-1.5"
          >
            <span>{isExpanded ? "Hide Details" : "Show Details"}</span>
            {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Planning controls get their own row beneath the header. They used to sit
          in the header's right-hand group, which made that group wide enough to wrap
          onto a full-width strip — so Export CSV / Rollback / Hide Details drifted off
          the top-right corner. Those three stay in the header; the objective switcher
          and Re-Plan, which are the wide ones, moved down here. */}
      {isExpanded && (
        <div className="flex items-center gap-2 flex-wrap mt-3">
          {/* Allocation Objective Selector.

              A segmented control rather than three emoji-prefixed buttons: the
              icons come from the same Lucide set as the rest of the page, the
              group is announced as a radiogroup, and the selected objective
              explains itself in a caption beneath — the weights behind each
              preset (global_allocator.get_objective_weights) are not something a
              manager should have to remember. */}
          <div
            role="radiogroup"
            aria-label="Allocation objective"
            className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-100 p-0.5 text-xs font-medium"
          >
            {OBJECTIVE_OPTIONS.map(({ key, label, Icon, activeClass, hint }) => {
              const active = objective === key;
              return (
                <button
                  key={key}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => handleSelectObjective(key)}
                  disabled={planning}
                  title={hint}
                  className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-all disabled:cursor-not-allowed disabled:opacity-60 ${
                    active
                      ? `bg-white shadow-sm font-semibold ${activeClass}`
                      : "text-slate-500 hover:text-slate-800"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" strokeWidth={active ? 2.25 : 2} aria-hidden="true" />
                  <span>{label}</span>
                </button>
              );
            })}
          </div>
          <button
            onClick={() => handleRunPlan()}
            disabled={planning}
            className="btn btn-primary text-xs flex items-center gap-1.5 px-3 py-1.5"
          >
            {planning ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            <span>{isPlanned ? "Re-Plan & Sequence" : "Generate Plan"}</span>
          </button>
          <p className="basis-full text-[11px] text-slate-500 mt-0.5">
            {OBJECTIVE_OPTIONS.find((o) => o.key === objective)?.hint ?? OBJECTIVE_OPTIONS[0].hint}
          </p>
        </div>
      )}

      {/* The nightly run failed and nothing has replaced it since.

          Shown whether or not a plan exists. Until 2026-09-03 a crash in the
          20:00 task wrote nothing at all, so this panel looked identical to a
          quiet night and the failure lived only in container logs. The message
          says what to do, not just that something broke. */}
      {plan?.last_failure && (
        <div
          className="mt-3 flex items-start gap-2.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5"
          role="status"
        >
          <AlertTriangle className="w-4 h-4 text-rose-600 flex-shrink-0 mt-0.5" />
          <div className="min-w-0">
            <p className="text-xs font-bold text-rose-900">
              {plan.has_plan
                ? "The scheduled 8 PM plan failed — this plan was generated manually"
                : "The scheduled 8 PM plan failed. No routes were created."}
            </p>
            <p className="text-[11px] text-rose-800/90 mt-0.5 break-words">
              {plan.last_failure.error_type}
              {plan.last_failure.error ? `: ${plan.last_failure.error}` : ""}
            </p>
            <p className="text-[11px] text-rose-700/80 mt-1">
              {plan.last_failure.failed_at
                ? `Failed ${new Date(plan.last_failure.failed_at).toLocaleString("en-IN", {
                    dateStyle: "medium", timeStyle: "short",
                  })}. `
                : ""}
              {plan.has_plan
                ? "Tomorrow's run will fail the same way until it is fixed."
                : "Use Generate Plan to create today's routes now."}
            </p>
          </div>
        </div>
      )}

      {isExpanded && plan?.has_plan && (
        <div className="space-y-4 pt-3">
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

            {/* 2026-09-10 — this tile showed the expected figure ALONE, and a
                manager reading it beside the dashboard's "Today's Collections"
                saw ₹10.3L against ₹71.2L and asked why the plan had written off
                most of the book. It had not. The forecast is the collectable
                balance weighted by each borrower's modelled chance of paying,
                and without its base on screen there was no way to see that.

                THE PERCENTAGE COMES FROM THE NUMBERS IT EXPLAINS — collectable,
                not target, because collectable is what the allocator actually
                multiplies. Dividing by the lifetime target would print a rate
                the model never computed, which is the mistake the decision panel
                made on 2026-09-09. The target is shown too, unlabelled as a
                denominator, because it is the figure the dashboard states and a
                manager needs to be able to tie the two cards together. */}
            {/* ONE DENOMINATOR ON SCREEN. 2026-09-10.
                The first version of this tile printed both bases — "₹10.3L /
                ₹65.2L collectable" over "16% modelled recovery · ₹71.2L target"
                — and a reader's immediate reaction was that one of the two must
                be wrong. Fair: two large rupee figures, stacked, differing, with
                nothing on screen saying why. Both were correct (₹71.2L lifetime
                target less ₹5.9L already collected leaves ₹65.2L still owed),
                but a number that has to be defended is a number badly presented.

                So the visible line carries only the base the percentage is
                actually computed from, and the reconciliation to the dashboard's
                target moved into the hover text — available to whoever asks the
                question, absent for everyone else. The rate still comes from the
                two numbers beside it. */}
            <div
              className="p-3 bg-white rounded-xl border border-slate-200/80"
              title={
                (plan.allocated_target_total ?? 0) > 0
                  ? `₹${((plan.allocated_target_total ?? 0) / 100000).toFixed(1)}L is the lifetime target on these ` +
                    `${plan.total_cases_allocated ?? 0} cases. ₹` +
                    `${(((plan.allocated_target_total ?? 0) - (plan.allocated_collectable_total ?? 0)) / 100000).toFixed(1)}L ` +
                    `of it has already been paid, leaving ₹${((plan.allocated_collectable_total ?? 0) / 100000).toFixed(1)}L ` +
                    `still owed — the figure shown. The model's own forecast for the next cycle, ` +
                    `case by case, is ₹${((plan.expected_recovery_total ?? 0) / 100000).toFixed(1)}L.`
                  : undefined
              }
            >
              {/* 2026-09-16, PRODUCT DIRECTION: THE HEADLINE IS THE COLLECTABLE
                  BALANCE, NOT THE MODELLED FORECAST.
                  Everything above this line describes the tile printing the
                  forecast (₹9.7L) over its base (₹63.8L). The product asked for
                  the base as the headline and the forecast off the tile: the
                  full amount the plan is going after, not the model's discount
                  on it. The forecast is unchanged in the data —
                  expected_recovery_total still drives the allocator and every
                  per-case line — and the tooltip still states it, so the
                  reconciliation is a hover away rather than gone. Where an old
                  plan has no collectable total the forecast is shown, so the
                  tile never reads ₹0.0L on a real plan. */}
              <span className="text-[11px] font-medium text-slate-500">Expected Recovery</span>
              <p className="text-lg font-bold text-emerald-600 mt-0.5">
                ₹{(((plan.allocated_collectable_total || plan.expected_recovery_total) ?? 0) / 100000).toFixed(1)}L
              </p>
              {(plan.allocated_collectable_total ?? 0) > 0 && (
                <p className="text-[11px] text-slate-400 mt-0.5">
                  collectable across {plan.total_cases_allocated ?? 0} allocated cases
                </p>
              )}
            </div>

            <div className="p-3 bg-white rounded-xl border border-slate-200/80">
              <span className="text-[11px] font-medium text-slate-500">Agents Planned</span>
              <p className="text-lg font-bold text-slate-900 mt-0.5">
                {plan.total_agents_planned ?? 0}
                <span className="text-xs font-normal text-slate-400 ml-1">routes</span>
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
                <span>Planned Agent Routes ({plan.beats.length})</span>
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
                    {/* The balance still owed on this agent's stops, in the same
                        unit as the KPI above (lib/money scales K → L → Cr).
                        Until 2026-09-16 this printed the lifetime target in
                        thousands — "₹737k" — and fifteen of those summed to a
                        figure no tile on the page showed. Falls back to the
                        target on a plan from before the field existed. */}
                    <span
                      className="font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded"
                      title="Balance still collectable across this agent's stops"
                    >
                      {shortMoney(b.total_collectable_amount ?? b.total_target_amount)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Explainable Decision Drawer */}
          {showDecisions && plan.decisions && (
            <div
              ref={decisionsRef}
              // NO PADDING ON THE SCROLL CONTAINER. 2026-09-17. This element
              // scrolls and its heading is `sticky top-0`; with `p-3` here the
              // heading pinned 12px BELOW the scrollport edge (sticky offsets
              // from the padding box), and rows scrolled up through that
              // uncovered strip and appeared above the heading. The padding
              // now lives on the heading and on the rows wrapper, so the
              // heading sits flush at the top and covers everything that
              // scrolls under it.
              className="mt-3 bg-slate-50 rounded-xl border border-slate-200"
              // scroll-margin keeps the drawer's heading clear of the sticky
              // page header when scrollIntoView lands it at the top.
              style={{ maxHeight: 360, overflowY: "auto", scrollMarginTop: 96 }}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-slate-800 sticky top-0 bg-slate-50 px-3 pt-3 pb-1.5 border-b border-slate-200 z-10">
                <div className="flex items-center gap-2">
                  <span>Case Assignment Audit Trail & Matching Factors</span>
                  <span className="text-slate-400 font-normal">({plan.decisions.length} recorded)</span>
                </div>
                {/* Outcome Filter Pills */}
                <div className="flex items-center gap-1 bg-white p-0.5 rounded-lg border border-slate-200 text-[11px] font-semibold">
                  <button
                    onClick={() => setDecisionFilter("ALL")}
                    className={`px-2 py-0.5 rounded transition-colors ${
                      decisionFilter === "ALL" ? "bg-slate-800 text-white font-bold" : "text-slate-600 hover:text-slate-900"
                    }`}
                  >
                    All ({plan.decisions.length})
                  </button>
                  <button
                    onClick={() => setDecisionFilter("ALLOCATED")}
                    className={`px-2 py-0.5 rounded transition-colors ${
                      decisionFilter === "ALLOCATED" ? "bg-emerald-600 text-white font-bold" : "text-slate-600 hover:text-slate-900"
                    }`}
                  >
                    Allocated ({plan.decisions.filter((d: AllocationDecisionItem) => d.outcome === "ALLOCATED").length})
                  </button>
                  <button
                    onClick={() => setDecisionFilter("DEFERRED")}
                    className={`px-2 py-0.5 rounded transition-colors ${
                      decisionFilter === "DEFERRED" ? "bg-amber-600 text-white font-bold" : "text-slate-600 hover:text-slate-900"
                    }`}
                  >
                    Deferred ({plan.decisions.filter((d: AllocationDecisionItem) => d.outcome.startsWith("DEFERRED")).length})
                  </button>
                  <button
                    onClick={() => setDecisionFilter("BLOCKED")}
                    className={`px-2 py-0.5 rounded transition-colors ${
                      decisionFilter === "BLOCKED" ? "bg-rose-600 text-white font-bold" : "text-slate-600 hover:text-slate-900"
                    }`}
                  >
                    Blocked ({plan.decisions.filter((d: AllocationDecisionItem) => d.outcome === "BLOCKED").length})
                  </button>
                </div>
              </div>
              <div className="space-y-1.5 px-3 pt-2 pb-3">
                {sortedDecisions.map((d: AllocationDecisionItem) => (
                  <div
                    key={d.decision_id}
                    onClick={() => setSelectedDecision(d === selectedDecision ? null : d)}
                    className="p-2 bg-white rounded-lg border border-slate-200/80 hover:border-brand-300 cursor-pointer text-xs space-y-1 transition-colors"
                  >
                    {/* Wraps on narrow screens: the case number keeps its
                        own line and the badges flow beneath it, each on one
                        line (`whitespace-nowrap`) — on a phone they used to
                        squeeze into one row and overprint each other. */}
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                      <span className="font-bold text-slate-800 whitespace-nowrap">{d.case_number}</span>
                      <div className="flex flex-wrap items-center justify-end gap-1.5 min-w-0">
                        {/* Says the model was involved, without saying how. A
                            manager needs to know the recovery estimate came
                            from a model — the version and input coverage sit in
                            the tooltip for anyone who asks. Absent entirely
                            when the model did NOT drive the decision, so the
                            badge never quietly means "scored and ignored". */}
                        {(() => {
                          const badge = mlBadge(d.ml);
                          return badge ? (
                            <span
                              title={badge.title}
                              className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-violet-100 text-violet-800 whitespace-nowrap"
                            >
                              {badge.label}
                            </span>
                          ) : null;
                        })()}
                        {/* Ownership as a TAG, not a reason. Since the
                            2026-09-11 gate an owned case can only go back to
                            its owner, so "Already their case" stopped being an
                            explanation and became a fact about the row — it is
                            withheld from the ranked list (GATE_TERMS) and shown
                            here instead. Absent on a new case. */}
                        {(() => {
                          const tag = ownerTag(d.score_breakdown, d.allocated_agent_name);
                          return tag ? (
                            <span
                              title={tag.title}
                              className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-700 border border-slate-200 whitespace-nowrap"
                            >
                              {tag.label}
                            </span>
                          ) : null;
                        })()}
                        {d.outcome === "ALLOCATED" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 whitespace-nowrap">
                            → {d.allocated_agent_name}
                          </span>
                        )}
                        {d.outcome === "BLOCKED" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-100 text-rose-800">
                            BLOCKED
                          </span>
                        )}
                        {(d.outcome === "DEFERRED" || d.outcome === "DEFERRED_ROUTE_INFEASIBLE") && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800">
                            {d.outcome === "DEFERRED_ROUTE_INFEASIBLE" ? "ROUTE OUTLIER" : "DEFERRED"}
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-slate-600 text-[11px] line-clamp-1">{d.reason}</p>
                    {selectedDecision === d && d.score_breakdown && (
                      <div className="mt-1 pt-1.5 border-t border-slate-100 text-[10.5px] text-slate-500 bg-slate-50/70 p-1.5 rounded space-y-0.5">
                        {rankedReasons({ ...d.score_breakdown, collectable_amount: d.collectable_amount }).map((r) => (
                          <div key={r.key} className="flex items-baseline justify-between gap-3">
                            <span className="text-slate-600">{r.label}</span>
                            <span className="tabular-nums text-slate-400 flex-shrink-0">{r.share}</span>
                          </div>
                        ))}
                        <div className="pt-1 mt-1 border-t border-slate-200/70 text-slate-400">
                          Priority {d.visit_priority_score} · Fit {d.fit_score}
                          {typeof d.score_breakdown.objective === "string" && ` · ${String(d.score_breakdown.objective).replace(/_/g, " ").toLowerCase()}`}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ── The overview's status strip ──────────────────────────────────────────────
//
// What stays on the overview after the move: whether tomorrow's plan exists,
// what it covers, and — above all — whether the nightly run failed. The failure
// is the one thing on the old card a manager must never have to click through
// to see, so it is rendered here in full, exactly as the card renders it.
//
// Reads the SAME query key as the card, so opening the plan page after the
// overview is a cache hit, and the two can never show different plans.
export function BeatPlanSummaryStrip() {
  const planQ = useQuery({
    queryKey: ["manager", "allocation", "latest"],
    queryFn: () => getLatestAllocation(),
    ...AS_BEFORE,
  });
  const plan = planQ.data ?? null;
  const settingsQ = useQuery({
    queryKey: ["manager", "allocation", "settings"],
    queryFn: getAllocationSettings,
    ...AS_BEFORE,
  });
  const objective = (settingsQ.data?.objective as Objective | undefined) ?? "BALANCED";
  const objectiveWords: Record<Objective, string> = {
    BALANCED: "Balanced",
    MAX_RECOVERY: "Max Recovery",
    MIN_DISTANCE: "Min Distance",
  };
  const collectable = plan?.has_plan
    ? ((plan.allocated_collectable_total || plan.expected_recovery_total) ?? 0)
    : 0;

  return (
    <div
      className="card p-4 sm:p-5"
      style={{ border: "1px solid #E1E3E9", background: "linear-gradient(180deg, #FFFFFF 0%, #F9FAFB 100%)" }}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2 rounded-lg bg-brand-50 text-brand-600 flex-shrink-0">
            <Compass className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-bold text-slate-900 text-base">Tomorrow's Field Plan</h3>
              {plan?.has_plan && (
                <>
                  <span className="text-xs font-mono text-slate-500 bg-slate-100 px-2 py-0.5 rounded">{plan.plan_date}</span>
                  <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">{plan.status}</span>
                </>
              )}
              {planQ.isPending && (
                <span className="text-xs text-slate-400">loading…</span>
              )}
              {plan && !plan.has_plan && !planQ.isPending && (
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">NOT PLANNED YET</span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              {plan?.has_plan
                ? `${plan.total_cases_allocated ?? 0} cases · ${shortMoney(collectable)} expected recovery · ${plan.total_agents_planned ?? 0} agent routes · ${objectiveWords[objective]}`
                : plan?.message ?? "Tomorrow's plan, objective and every allocation decision."}
            </p>
          </div>
        </div>
        <Link
          to="/manager/beat-plan"
          className="btn btn-primary text-xs flex items-center gap-1.5 px-3 py-1.5 flex-shrink-0"
        >
          <span>{plan?.has_plan ? "Open plan" : "Plan tomorrow"}</span>
          <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      </div>

      {plan?.last_failure && (
        <div
          className="mt-3 flex items-start gap-2.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5"
          role="status"
        >
          <AlertTriangle className="w-4 h-4 text-rose-600 flex-shrink-0 mt-0.5" />
          <div className="min-w-0">
            <p className="text-xs font-bold text-rose-900">
              {plan.has_plan
                ? "The scheduled 8 PM plan failed — this plan was generated manually"
                : "The scheduled 8 PM plan failed. No routes were created."}
            </p>
            <p className="text-[11px] text-rose-800/90 mt-0.5 break-words">
              {plan.last_failure.error_type}
              {plan.last_failure.error ? `: ${plan.last_failure.error}` : ""}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
