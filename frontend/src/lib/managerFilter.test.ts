// @vitest-environment node
/**
 * The per-manager filter shared by the Agents and Cases pages. The case that
 * matters is resolveManagerFilter: a selection outliving the roster it came
 * from would otherwise keep filtering while its control is hidden.
 */
import { describe, expect, it } from "vitest";
import type { Agent } from "@/types";
import {
  ALL_MANAGERS, managerByAgentId, managerOptions, resolveManagerFilter, showManagerFilter,
  trendSplit,
} from "./managerFilter";

function agent(id: string, managerId: string | null, managerName: string | null): Agent {
  return { id, manager_user_id: managerId, manager_name: managerName } as unknown as Agent;
}

const TWO = [agent("a1", "m1", "Vikram"), agent("a2", "m2", "Sunita"), agent("a3", "m1", "Vikram")];

describe("managerOptions", () => {
  it("lists each manager once, by name", () => {
    expect(managerOptions(TWO)).toEqual([
      { id: "m2", name: "Sunita" },
      { id: "m1", name: "Vikram" },
    ]);
  });

  it("ignores agents with no manager", () => {
    expect(managerOptions([agent("a1", null, null)])).toEqual([]);
  });
});

describe("showManagerFilter", () => {
  it("is shown only when the roster spans more than one manager", () => {
    expect(showManagerFilter(TWO)).toBe(true);
    // A manager's own roster is all one manager — the control would do nothing.
    expect(showManagerFilter([agent("a1", "m1", "Vikram")])).toBe(false);
  });
});

describe("resolveManagerFilter", () => {
  it("keeps a selection that is still on offer", () => {
    expect(resolveManagerFilter(TWO, "m1")).toBe("m1");
  });

  it("falls back to ALL when the selected manager left the roster", () => {
    // The control hides below two managers, so a surviving selection would
    // filter invisibly with no way to clear it.
    expect(resolveManagerFilter([agent("a1", "m1", "Vikram")], "m2")).toBe(ALL_MANAGERS);
    expect(resolveManagerFilter([], "m1")).toBe(ALL_MANAGERS);
  });

  it("passes ALL through untouched", () => {
    expect(resolveManagerFilter(TWO, ALL_MANAGERS)).toBe(ALL_MANAGERS);
  });
});

describe("managerByAgentId", () => {
  it("maps agent -> manager for rows that only carry an agent", () => {
    expect(managerByAgentId(TWO).get("a3")).toBe("m1");
  });
});

describe("trendSplit", () => {
  const d = (delta: number | null) => ({ collection_rate_delta_pts: delta });

  it("counts a null delta as unknown, never as flat", () => {
    // null = the backend had under two months with a target for that agent.
    // Calling that "no change" asserts something it does not know.
    const r = trendSplit([d(null), d(null), d(0)]);
    expect(r.flat).toBe(1);
    expect(r.noHistory).toBe(2);
  });

  it("always reconciles to the roster it was given", () => {
    // The reported bug: 4 improving + 11 declining + 0 flat = 15 shown above
    // an 18-agent roster, with the missing 3 dropped silently.
    const roster = [
      ...Array.from({ length: 4 }, () => d(2.5)),
      ...Array.from({ length: 11 }, () => d(-1.5)),
      ...Array.from({ length: 3 }, () => d(null)),
    ];
    const r = trendSplit(roster);
    expect(r).toMatchObject({ up: 4, down: 11, flat: 0, noHistory: 3 });
    expect(r.up + r.down + r.flat + r.noHistory).toBe(roster.length);
  });

  it("keeps total as the known-delta denominator, so a bar never exceeds 100%", () => {
    const r = trendSplit([d(1), d(-1), d(null)]);
    expect(r.total).toBe(2);              // not 3: the unknown has no bar
    expect(r.up / r.total).toBeLessThanOrEqual(1);
  });

  it("reports an all-unknown roster as nothing to compare", () => {
    const r = trendSplit([d(null), d(null)]);
    expect(r.total).toBe(0);              // the card shows "Not enough history yet"
    expect(r.noHistory).toBe(2);
  });

  it("treats an absent field like an explicit null", () => {
    expect(trendSplit([{}, {}]).noHistory).toBe(2);
  });
});
