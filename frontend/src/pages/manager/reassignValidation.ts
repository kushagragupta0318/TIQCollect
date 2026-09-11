/**
 * The rules the Reassign dialog enforces before it will submit. Pure, so they
 * can be tested without mounting a page — the same shape as casesViewState.ts.
 *
 * The server enforces all of this again (a blank reason is a 422 there, and
 * the hard gates are re-checked for the incoming agent). This is the copy that
 * stops a manager clicking Submit and then reading the error, not the copy
 * that makes the move safe.
 */

export const REASON_MIN_CHARS = 5;

export type ReassignInput = {
  currentAgentId: string | null;
  newAgentId: string | null;
  reason: string;
};

export type ReassignProblem =
  | "no_owner"
  | "choose_agent"
  | "same_agent"
  | "reason_blank"
  | "reason_too_short";

/**
 * What stops this reassignment from being submitted, or null when nothing
 * does. Checked in the order a person fills the form: agent first, reason
 * second — so the message they see matches the field they are on.
 *
 * PRODUCT INVARIANT, first of all: an UNASSIGNED case is a NEW case, and the
 * nightly allocator gives it its first agent. Reassignment moves ownership
 * that already exists. The dialog cannot normally be opened for an unowned
 * case (the case list and detail hide them), so this branch is a guard for
 * the invariant rather than a message anyone should routinely see — and the
 * server refuses the same state with the same sentence.
 */
export function reassignProblem(i: ReassignInput): ReassignProblem | null {
  if (!i.currentAgentId) return "no_owner";
  if (!i.newAgentId) return "choose_agent";
  if (i.currentAgentId && i.newAgentId === i.currentAgentId) return "same_agent";
  // trim() — not just length — because the server rejects whitespace-only
  // reasons and a dialog that accepted them would send a request it knows
  // will fail.
  const reason = i.reason.trim();
  if (reason.length === 0) return "reason_blank";
  if (reason.length < REASON_MIN_CHARS) return "reason_too_short";
  return null;
}

export const PROBLEM_TEXT: Record<ReassignProblem, string> = {
  no_owner: "This case has no agent yet — tonight's plan will assign it. Only an assigned case can be reassigned.",
  choose_agent: "Choose the agent who should take this case.",
  same_agent: "That agent already holds this case.",
  reason_blank: "A reason is required — it goes on the case's audit trail.",
  reason_too_short: `Say a little more (at least ${REASON_MIN_CHARS} characters).`,
};

/**
 * Which chip a case row shows under its number. Extracted on 2026-09-11 when
 * the "Visited ×N" chip was removed: the count was history, not progress, and
 * on a book where every open case has been worked it decorated every row.
 * Only two states are left, and both answer a question a manager scanning
 * the list is actually asking.
 */
export type RowChip = "resolved" | "visited_today" | null;

export function rowChip(c: {
  status: string;
  target_amount: number;
  collected_amount: number;
  is_visited_today?: boolean;
}): RowChip {
  const isPaid = c.status === "PAID" || (c.target_amount > 0 && c.collected_amount >= c.target_amount);
  if (isPaid) return "resolved";
  if (c.is_visited_today) return "visited_today";
  return null;
}
