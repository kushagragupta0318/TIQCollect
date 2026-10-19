// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-16 — NEW PAGE. Tomorrow's beat plan gets its own route.
//
//   Until today the whole allocation workflow lived in a collapsible card on
//   the overview: objective switcher, Generate / Re-Plan, four plan tiles,
//   fifteen beat cards, Export CSV, Rollback, and a 900-row Case Assignment
//   Audit Trail behind a "Show Details" toggle. It was a page pretending to be
//   a card, and its GET /allocation/latest — ~930 decisions with score
//   breakdowns and ML blocks — fired on every overview load whether or not the
//   manager wanted the plan.
//
//   The component moved verbatim (TomorrowAllocationCard.tsx). This file is
//   only the route: a title and the card, opened expanded. The overview keeps
//   a status strip that links here, sharing the same React Query key so the
//   two never show different plans.
// ─────────────────────────────────────────────────────────────────────────────

// MANAGER-SIDE WORDING, 2026-09-16. The manager pages say "field plan" and
// "route" where the code and the agent app say "beat". Bank clients and demo
// audiences do not know the word; agents and agency staff do, so the agent
// app keeps it. The model, the API fields and the route paths are unchanged
// (`/manager/beat-plan`, `plan.beats`, `beat_date` …) — this is labels only.

import { TomorrowAllocationCard } from "./TomorrowAllocationCard";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

export default function ManagerBeatPlanPage() {
  return (
    <div className="space-y-4">
      <div style={{ animation: `enter 420ms ${EASE} 0ms both` }}>
        <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>
          Field Plan
        </h1>
        <p className="text-[13px] sm:text-sm mt-0.5" style={{ color: "#6B6D76" }}>
          Tomorrow's allocation — objective, each agent's route and every assignment decision
        </p>
      </div>
      <TomorrowAllocationCard defaultExpanded />
    </div>
  );
}
