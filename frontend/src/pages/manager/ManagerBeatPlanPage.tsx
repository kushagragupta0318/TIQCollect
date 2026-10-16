// ─── CHANGELOG ──────────────────────────────────────────────────────────────
// The Field Plan page: TomorrowAllocationCard, opened expanded.
//
// MANAGER-SIDE WORDING, 2026-09-16. The manager pages say "field plan" and
// "route" where the code and the agent app say "beat". Bank clients and demo
// audiences do not know the word; agents and agency staff do, so the agent
// app and every identifier keep it (`/manager/beat-plan`, `plan.beats`,
// `beat_date` …) — this is labels only.
//
// 2026-10-15 — AGENCY_ADMIN: a field plan belongs to ONE manager (allocation
// runs, beats and settings are all keyed by manager_user_id, and agents report
// to a manager, never to the admin). So the admin picks a manager here and
// reads — or generates — that manager's plan. A manager sees no picker: the
// page is their own plan, exactly as before.
// ────────────────────────────────────────────────────────────────────────────
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { TomorrowAllocationCard } from "./TomorrowAllocationCard";
import { getAgencyManagers } from "@/api/manager";
import { useAuthStore } from "@/store/authStore";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

export default function ManagerBeatPlanPage() {
  const isAgencyAdmin = useAuthStore((st) => st.user?.role) === "AGENCY_ADMIN";

  const managersQ = useQuery({
    queryKey: ["manager", "managers"],
    queryFn: getAgencyManagers,
    enabled: isAgencyAdmin,
    staleTime: 60_000,
  });
  const managers = managersQ.data ?? [];

  const [picked, setPicked] = useState<string>("");
  // Default to the first manager once the list lands, so the admin sees a real
  // plan rather than an empty page. Derived, not an effect: no setState to
  // schedule and no window where the two disagree.
  const selected = picked || managers[0]?.id || "";

  return (
    <div className="space-y-4">
      <div
        className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3"
        style={{ animation: `enter 420ms ${EASE} 0ms both` }}
      >
        <div className="min-w-0">
          <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>
            Field Plan
          </h1>
          <p className="text-[13px] sm:text-sm mt-0.5" style={{ color: "#6B6D76" }}>
            Tomorrow's allocation — objective, each agent's route and every assignment decision
          </p>
        </div>

        {isAgencyAdmin && (
          <label className="flex-shrink-0">
            <span className="mb-1.5 block text-[13px] font-normal" style={{ color: "#98A2B3" }}>Manager</span>
            <select
              className="input text-[13px] py-2 px-2.5 tap-target-h w-full sm:w-60"
              value={selected}
              onChange={(e) => setPicked(e.target.value)}
              aria-label="Field plan for manager"
              disabled={managersQ.isPending || managers.length === 0}
            >
              {managersQ.isPending && <option value="">Loading managers…</option>}
              {!managersQ.isPending && managers.length === 0 && <option value="">No managers yet</option>}
              {managers.map((m) => (
                <option key={m.id} value={m.id}>{m.full_name}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {isAgencyAdmin && !selected ? (
        // No manager resolved yet (list still loading, or none exist). The card
        // must NOT mount: its queries would go out with no manager_user_id and
        // the server answers 422 — "select a manager" — which is correct for
        // the API and wrong as a first paint.
        <p className="text-sm p-6" style={{ color: "#6B6D76" }}>
          {managersQ.isPending
            ? "Loading managers…"
            : "No managers in this agency yet — add one on the Managers page, and their field plan appears here."}
        </p>
      ) : isAgencyAdmin && !managersQ.isPending && managers.length === 0 ? (
        // Nothing to plan: a plan needs a manager's roster, and there is no
        // manager yet. Say so rather than showing an empty allocation card.
        <p className="text-sm p-6" style={{ color: "#6B6D76" }}>
          No managers in this agency yet — add one on the Managers page, and their
          field plan appears here.
        </p>
      ) : (
        // `key` remounts the card when the admin switches manager, so no state
        // from the previous manager's plan survives into the next one.
        <TomorrowAllocationCard key={selected || "self"} defaultExpanded managerUserId={isAgencyAdmin ? selected : undefined} />
      )}
    </div>
  );
}
