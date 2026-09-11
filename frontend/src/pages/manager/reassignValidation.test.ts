import { describe, expect, it } from "vitest";
import { PROBLEM_TEXT, REASON_MIN_CHARS, reassignProblem, rowChip } from "./reassignValidation";

// ── The reassign dialog's rules ─────────────────────────────────────────────

const base = { currentAgentId: "a1", newAgentId: "a2", reason: "Borrower requested a change." };

describe("reassignProblem", () => {
  it("accepts a different agent with a real reason", () => {
    expect(reassignProblem(base)).toBeNull();
  });

  it("insists on an agent first, so the message matches the field the user is on", () => {
    expect(reassignProblem({ ...base, newAgentId: null })).toBe("choose_agent");
    expect(reassignProblem({ ...base, newAgentId: null, reason: "" })).toBe("choose_agent");
  });

  it("refuses the agent who already holds the case", () => {
    expect(reassignProblem({ ...base, newAgentId: "a1" })).toBe("same_agent");
  });

  it("refuses an unassigned case outright — reassignment is not first assignment", () => {
    // UNASSIGNED cases are new cases; the nightly allocator gives them their
    // first agent. This used to read "allows any agent when the case is
    // unassigned", which described a workflow the product does not have.
    expect(reassignProblem({ ...base, currentAgentId: null, newAgentId: "a1" })).toBe("no_owner");
    // It wins over every other problem: no agent chosen, blank reason — still
    // the lifecycle answer, because nothing the user types can make it valid.
    expect(reassignProblem({ currentAgentId: null, newAgentId: null, reason: "" })).toBe("no_owner");
    expect(PROBLEM_TEXT.no_owner).toMatch(/tonight's plan will assign it/);
  });

  it("rejects an empty reason and a whitespace-only reason identically", () => {
    // The server treats these the same (422), so the dialog must not let one
    // through that the other would block.
    for (const blank of ["", "   ", "\n\t ", " "]) {
      expect(reassignProblem({ ...base, reason: blank })).toBe("reason_blank");
    }
  });

  it("rejects a reason that is too short to be a reason", () => {
    expect(reassignProblem({ ...base, reason: "ok" })).toBe("reason_too_short");
    expect(reassignProblem({ ...base, reason: "x".repeat(REASON_MIN_CHARS - 1) })).toBe("reason_too_short");
    expect(reassignProblem({ ...base, reason: "x".repeat(REASON_MIN_CHARS) })).toBeNull();
  });

  it("measures length after trimming, not before", () => {
    expect(reassignProblem({ ...base, reason: "   ok   " })).toBe("reason_too_short");
  });

  it("has a sentence for every problem it can raise", () => {
    for (const p of ["no_owner", "choose_agent", "same_agent", "reason_blank", "reason_too_short"] as const) {
      expect(PROBLEM_TEXT[p]).toMatch(/\S/);
    }
  });
});

// ── The row chip: Visited ×N is gone, ✓ Visited today stays ────────────────

const open = { status: "IN_PROGRESS", target_amount: 10_000, collected_amount: 0 };

describe("rowChip", () => {
  it("shows ✓ Visited today when the case was visited today", () => {
    expect(rowChip({ ...open, is_visited_today: true })).toBe("visited_today");
  });

  it("shows nothing for a case worked before but not today — the old Visited ×N", () => {
    // This is the removed chip. A case with any lifetime visit count and no
    // visit today used to render "Visited ×N"; it now renders no chip at all.
    expect(rowChip({ ...open, is_visited_today: false })).toBeNull();
    expect(rowChip({ ...open })).toBeNull();
  });

  it("does not read visit_count at all", () => {
    // The pre-2026-09-03 bug was this very field being used for "today".
    // Passing an absurd count must change nothing.
    expect(rowChip({ ...open, is_visited_today: false, visit_count: 99 } as never)).toBeNull();
    expect(rowChip({ ...open, is_visited_today: true, visit_count: 0 } as never)).toBe("visited_today");
  });

  it("shows Resolved for a paid case, and Resolved outranks Visited today", () => {
    expect(rowChip({ ...open, status: "PAID", is_visited_today: true })).toBe("resolved");
    expect(rowChip({ status: "IN_PROGRESS", target_amount: 500, collected_amount: 500 })).toBe("resolved");
  });

  it("never treats a zero-target case as resolved by arithmetic", () => {
    expect(rowChip({ status: "IN_PROGRESS", target_amount: 0, collected_amount: 0 })).toBeNull();
  });
});
