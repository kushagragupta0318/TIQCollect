// The per-manager filter shared by the Agents and Cases pages (agency admins
// only — a manager's roster is all one manager, so the control is pointless
// for them and is not rendered).
//
// One definition, because two copies of "which managers are in this roster"
// drift: the Agents page filters agents directly, the Cases page filters cases
// through their agent, and both must agree on the list and its order.
import type { Agent } from "@/types";

export const ALL_MANAGERS = "ALL";

export interface ManagerOption {
  id: string;
  name: string;
}

/** The distinct managers represented in a roster, by name. */
export function managerOptions(agents: readonly Agent[]): ManagerOption[] {
  const byId = new Map<string, string>();
  for (const a of agents) {
    if (a.manager_user_id) byId.set(a.manager_user_id, a.manager_name ?? "Unassigned");
  }
  return [...byId.entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((x, y) => x.name.localeCompare(y.name));
}

/** Whether the control is worth showing: more than one manager in the roster. */
export function showManagerFilter(agents: readonly Agent[]): boolean {
  return managerOptions(agents).length > 1;
}

/**
 * The month-on-month split shown above the Agents table.
 *
 * `collection_rate_delta_pts` is null when the backend had fewer than two
 * months carrying a target for that agent, so the change is UNKNOWN — not
 * zero. Counting those as Flat would assert "no change" about an agent with
 * no history; dropping them silently (the original bug) left the three rows
 * summing to less than the roster above them with nothing to explain it. They
 * get their own count instead, so improving + declining + flat + noHistory
 * always equals the roster.
 */
export interface TrendSplitCounts {
  up: number;
  down: number;
  flat: number;
  /** Delta known for this many agents — the denominator the bars scale against. */
  total: number;
  /** Too little history to compare. */
  noHistory: number;
}

export function trendSplit(
  agents: readonly { collection_rate_delta_pts?: number | null }[],
): TrendSplitCounts {
  const deltas = agents
    .map((a) => a.collection_rate_delta_pts)
    .filter((d): d is number => d !== null && d !== undefined);
  return {
    up: deltas.filter((d) => d > 0).length,
    down: deltas.filter((d) => d < 0).length,
    flat: deltas.filter((d) => d === 0).length,
    total: deltas.length,
    noHistory: agents.length - deltas.length,
  };
}

/** agent id -> manager id, for filtering rows that only carry an agent. */
export function managerByAgentId(agents: readonly Agent[]): Map<string, string> {
  const m = new Map<string, string>();
  for (const a of agents) if (a.manager_user_id) m.set(a.id, a.manager_user_id);
  return m;
}

/**
 * The filter to actually apply. A selection survives in state after the roster
 * changes under it (an agent reassigned, a manager's last agent removed): the
 * control then hides — showManagerFilter is false below two managers — while
 * the old id kept filtering, leaving a silently narrowed list with no visible
 * way back. Falling back to ALL when the selection is no longer on offer makes
 * that self-heal.
 */
export function resolveManagerFilter(agents: readonly Agent[], selected: string): string {
  if (selected === ALL_MANAGERS) return ALL_MANAGERS;
  return managerOptions(agents).some((m) => m.id === selected) ? selected : ALL_MANAGERS;
}
