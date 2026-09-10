/**
 * The case table must not claim a manager's filters excluded everything before
 * it has asked.
 *
 * WHAT WENT WRONG. The React Query migration set `loading = casesQ.isFetching`,
 * and the cases query is `enabled: datesReady`. On a plain visit to
 * /manager/cases with no date_from/date_to in the URL, `datesReady` starts false
 * while GET /manager/cases/date-range resolves. A DISABLED query sits at
 * `fetchStatus: 'idle'`, so `isFetching` is false and `cases` is `[]` — and the
 * table rendered
 *
 *     "No cases match the current filters"
 *
 * for the length of that round trip, on a book of 877 cases. The hand-rolled
 * code it replaced held `useState(true)` and showed the skeleton, so this was a
 * regression introduced by the migration.
 *
 * The property held here is that "empty" is a claim about a COMPLETED answer:
 * it may only be made once the query was allowed to run and has finished.
 */
import { describe, expect, it } from "vitest";
import { casesView } from "./casesViewState";

describe("the initial date-range window", () => {
  it("does NOT report empty while the date range is still resolving", () => {
    // The exact bug: disabled query, so isFetching is false and there are no
    // rows — for reasons that say nothing whatever about the data.
    const view = casesView({ datesReady: false, isFetching: false, rowCount: 0 });
    expect(view).not.toBe("empty");
    expect(view).toBe("loading");
  });

  it("stays loading even if a stale page of rows is still in the cache", () => {
    // keepPreviousData can leave rows behind. Not our answer yet either.
    expect(casesView({ datesReady: false, isFetching: false, rowCount: 50 }))
      .toBe("loading");
  });

  it("is loading while the date range resolves AND a fetch is in flight", () => {
    expect(casesView({ datesReady: false, isFetching: true, rowCount: 0 }))
      .toBe("loading");
  });
});

describe("once the query has actually answered", () => {
  it("DOES report empty on a completed zero-case response", () => {
    // The message is correct here and must not have been suppressed by the fix.
    expect(casesView({ datesReady: true, isFetching: false, rowCount: 0 }))
      .toBe("empty");
  });

  it("renders the list when rows came back", () => {
    expect(casesView({ datesReady: true, isFetching: false, rowCount: 50 }))
      .toBe("list");
  });
});

describe("fetches after the first one", () => {
  it("shows the skeleton on a page change rather than the previous rows", () => {
    // `loading` was deliberately `isFetching`, not `isPending`, so paging shows
    // the skeleton exactly as the old `setLoading(true)` on every call did.
    expect(casesView({ datesReady: true, isFetching: true, rowCount: 50 }))
      .toBe("loading");
  });

  it("does not flash 'no cases' between a filter change and its response", () => {
    // The filter changed, the query key changed, the new response has not
    // landed. Claiming "no cases match" here would be the same lie, later.
    expect(casesView({ datesReady: true, isFetching: true, rowCount: 0 }))
      .toBe("loading");
  });
});

describe("the truth table is total", () => {
  it("covers every combination and never says empty without an answer", () => {
    for (const datesReady of [false, true]) {
      for (const isFetching of [false, true]) {
        for (const rowCount of [0, 1, 50]) {
          const view = casesView({ datesReady, isFetching, rowCount });
          if (view === "empty") {
            // The invariant, stated once: "empty" implies we asked and finished.
            expect(datesReady).toBe(true);
            expect(isFetching).toBe(false);
            expect(rowCount).toBe(0);
          }
          expect(["loading", "empty", "list"]).toContain(view);
        }
      }
    }
  });
});
