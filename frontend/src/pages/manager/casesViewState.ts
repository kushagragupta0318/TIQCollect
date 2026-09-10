/**
 * Which of the case table's three bodies to render.
 *
 * ─── WHY THIS IS ITS OWN FILE ───────────────────────────────────────────────
 * Extracted 2026-09-10, the same way `allocationReasons.ts` was, so the choice
 * can be tested without mounting a 1,300-line page. The bug below was invisible
 * to every other check the project has — it type-checks, it lints, and the page
 * ends up correct a few hundred milliseconds later.
 *
 * ─── THE DEFECT THIS EXISTS TO PIN ──────────────────────────────────────────
 * The React Query migration set `loading = casesQ.isFetching`, and the cases
 * query is `enabled: datesReady`. On a plain visit to /manager/cases (no
 * date_from/date_to in the URL) `datesReady` starts FALSE while
 * GET /manager/cases/date-range resolves, and a DISABLED query has
 * `fetchStatus: 'idle'` — so `isFetching` is false, `cases` is `[]`, and the
 * table fell straight through to
 *
 *     "No cases match the current filters"
 *
 * for the length of that round trip, before 877 cases appeared. Not a flicker
 * of the wrong shape: a false statement about the manager's own book, telling
 * them their filters excluded everything when nothing had been asked yet.
 *
 * The hand-rolled code it replaced held `useState(true)` and showed the
 * skeleton for exactly that window, so this was a regression, not a pre-existing
 * fault.
 *
 * ─── THE RULE ───────────────────────────────────────────────────────────────
 * "Empty" is a claim about a completed answer. It may only be made once the
 * question has actually been asked and answered — which means BOTH that the
 * date range has resolved (so the query is allowed to run) AND that no fetch is
 * in flight. Anything before that is "loading", because we do not yet know.
 */

export type CasesView = "loading" | "empty" | "list";

export interface CasesViewInput {
  /** False while GET /manager/cases/date-range is still resolving. The cases
   *  query is `enabled: datesReady`, so until this flips the query is idle and
   *  reports `isFetching === false` despite nothing having been fetched. */
  datesReady: boolean;
  /** `casesQ.isFetching` — true only when a request is actually in flight. */
  isFetching: boolean;
  /** Rows after the client-side bucket/search filter, i.e. what would render. */
  rowCount: number;
}

export function casesView({ datesReady, isFetching, rowCount }: CasesViewInput): CasesView {
  // `!datesReady` is the half that was missing. It is FIRST because it is the
  // stronger statement: while it holds, `isFetching` is false and `rowCount` is
  // 0 for reasons that say nothing about the data.
  if (!datesReady || isFetching) return "loading";
  return rowCount === 0 ? "empty" : "list";
}
