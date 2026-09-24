// ─── CHANGELOG ───────────────────────────────────────────────────────────────
// 2026-09-24 — NEW (board ML-1, option A). The borrower's stance on paying —
//   BorrowerDisposition on the backend — recorded on the visit form (Borrower
//   path, replacing "Borrower Tone") and on the call-log form (answered
//   calls). recovery_risk 2.2.0's strongest behavioural feature reads it, and
//   until now nothing in the product wrote it: 0 of 2,404 visits.
//
//   The stance is the AGENT'S READ, not a copy of the outcome: the model
//   already has the outcome (last_visit_outcome). So only four outcomes, and
//   hardship reasons, pre-select one — the pairs where the outcome and the
//   stance mean the same thing — and the agent's last tap wins. A PTP does NOT
//   pre-select WILL_PAY (coordinator, 2026-09-24).
// ─────────────────────────────────────────────────────────────────────────────

/** backend/app/models/call_log.py BorrowerDisposition, best to worst. A
 *  vitest reads that file and fails if the two lists drift. */
export const BORROWER_STANCES = [
  "WILL_PAY", "MAY_PAY", "NO_COMMITMENT", "HARDSHIP", "DISPUTE", "REFUSES",
] as const;
export type BorrowerStance = (typeof BORROWER_STANCES)[number];

export const STANCE_OPTIONS: readonly { value: BorrowerStance; label: string; hint: string }[] = [
  { value: "WILL_PAY",      label: "Will pay",       hint: "Named a date or an amount" },
  { value: "MAY_PAY",       label: "May pay",        hint: "Wants to pay, won't commit" },
  { value: "NO_COMMITMENT", label: "No commitment",  hint: "Said nothing either way" },
  { value: "HARDSHIP",      label: "Can't pay",      hint: "No money, not unwilling" },
  { value: "DISPUTE",       label: "Disputes",       hint: "Contests the debt" },
  { value: "REFUSES",       label: "Refuses",        hint: "Will not pay" },
];

/** Outcomes that already say the stance. Only these four; everything else
 *  leaves it to the agent. */
const STANCE_FROM_OUTCOME: Readonly<Record<string, BorrowerStance>> = {
  RTP: "REFUSES",
  BROKEN_PTP: "REFUSES",
  DISPUTE: "DISPUTE",
};

/** Default reasons that are a capacity statement: "cannot", not "will not". */
export const HARDSHIP_REASONS: ReadonlySet<string> = new Set([
  "JOB_LOSS", "SALARY_CUT", "BUSINESS_FAILURE", "MEDICAL", "DEATH_IN_FAMILY", "OVER_LEVERAGED",
]);

export interface StanceState {
  stance: BorrowerStance | null;
  /** Who chose it: the outcome, the reason (pre-selections), or the agent's
   *  own tap. A pre-selection follows the field that made it; the agent's
   *  choice stays until a later outcome or reason says otherwise. */
  source: "outcome" | "reason" | "agent" | null;
}

export const NO_STANCE: StanceState = { stance: null, source: null };

export type StanceEvent =
  | { kind: "outcome"; value: string | null }
  | { kind: "reason"; value: string | null }
  | { kind: "tap"; value: BorrowerStance };

/** The stance after one of the agent's taps: the last tap wins.
 *  - tapping a stance chooses it (tapping the chosen one again clears it);
 *  - an outcome or reason that implies a stance chooses it;
 *  - an outcome or reason that implies none clears the stance only if that
 *    same field chose it (a stale pre-selection is worse than none), and
 *    leaves the agent's own tap and the other field's choice alone. */
export function nextStance(state: StanceState, event: StanceEvent): StanceState {
  if (event.kind === "tap") {
    return state.stance === event.value ? NO_STANCE : { stance: event.value, source: "agent" };
  }
  const implied: BorrowerStance | null = event.value == null ? null
    : event.kind === "outcome" ? STANCE_FROM_OUTCOME[event.value] ?? null
    : HARDSHIP_REASONS.has(event.value) ? "HARDSHIP" : null;
  if (implied) return { stance: implied, source: event.kind };
  return state.source === event.kind ? NO_STANCE : state;
}
