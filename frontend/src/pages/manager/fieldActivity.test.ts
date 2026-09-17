/**
 * The funnel's pure half: links carry the window, drop-offs read the server's
 * figures, the empty state is explicit, and REVISIT is never a not-met word.
 */
import { describe, expect, it } from "vitest";
import {
  DEFAULT_WINDOW, STAGE_DEFS, casesLink, dropOffLabel, emptyVisitsMessage,
  outcomeWords, reasonRows, shareOfPlanned, stageCounts, type FieldActivity,
} from "./fieldActivity";

const TODAY_EMPTY: FieldActivity = {
  window: "today", window_start: "2026-09-17", window_end: "2026-09-17",
  visits_from: "2026-09-17T00:00:00+00:00", visits_to: "2026-09-17T23:59:59.999999+00:00",
  planned: 214, visited: 0, met: 0, paid_or_promised: 0, not_met: 0, met_no_money: 0,
  drop_offs: { planned_to_visited: 100, visited_to_met: null, met_to_paid_or_promised: null },
  not_met_reasons: {}, met_no_money_reasons: {},
  definitions: { not_met_outcomes: ["ADDRESS_ISSUE", "NOT_AVAILABLE"], paid_or_promised_outcomes: ["PAID_FULL", "PART_PAID", "PART_PAID_PTP", "PTP"] },
};

describe("defaults and stages", () => {
  it("defaults to today", () => {
    expect(DEFAULT_WINDOW).toBe("today");
  });
  it("has the four stages in funnel order", () => {
    expect(STAGE_DEFS.map((s) => s.key)).toEqual(["planned", "visited", "met", "paid_or_promised"]);
    expect(stageCounts({ planned: 10, visited: 7, met: 5, paid_or_promised: 2 }).map((s) => s.count)).toEqual([10, 7, 5, 2]);
  });
  it("names revisit as met, never as a not-met word", () => {
    expect(outcomeWords("REVISIT")).toBe("Revisit needed");
    expect(STAGE_DEFS.find((s) => s.key === "met")!.hint).toMatch(/revisit counts as met/i);
  });
});

describe("casesLink carries the window", () => {
  it("stage links", () => {
    expect(casesLink("today", "visited")).toBe("/manager/cases?activity=visited&activity_window=today");
    expect(casesLink("7d", "paid_or_promised")).toBe("/manager/cases?activity=paid_or_promised&activity_window=7d");
  });
  it("reason links add the outcome and keep the window", () => {
    expect(casesLink("today", "not_met", "NOT_AVAILABLE"))
      .toBe("/manager/cases?activity=not_met&activity_window=today&visit_outcome=NOT_AVAILABLE");
  });
  it("never filters by case status or allocation date", () => {
    for (const w of ["today", "7d", "30d"] as const) {
      const href = casesLink(w, "met");
      expect(href).not.toMatch(/status=|date_from=|date_to=|bucket=/);
    }
  });
});

describe("drop-offs and shares", () => {
  it("labels a drop-off as a rounded negative percent, null when there is nothing to drop from", () => {
    expect(dropOffLabel(28.57)).toBe("−29%");
    expect(dropOffLabel(0)).toBe("−0%");
    expect(dropOffLabel(null)).toBeNull();
    expect(dropOffLabel(undefined)).toBeNull();
  });
  it("sizes every bar against planned and never over 100%", () => {
    expect(shareOfPlanned(214, 214)).toBe(1);
    expect(shareOfPlanned(107, 214)).toBe(0.5);
    expect(shareOfPlanned(0, 214)).toBe(0);
    expect(shareOfPlanned(5, 0)).toBe(0);
    expect(shareOfPlanned(300, 214)).toBe(1);
  });
});

describe("reasons", () => {
  it("orders largest first with words and links", () => {
    const rows = reasonRows("today", "not_met", { ADDRESS_ISSUE: 48, NOT_AVAILABLE: 137 });
    expect(rows.map((r) => r.label)).toEqual(["Not available", "Address issue"]);
    expect(rows[0].href).toContain("visit_outcome=NOT_AVAILABLE");
    expect(rows[0].href).toContain("activity_window=today");
  });
  it("drops zeros and survives an absent map", () => {
    expect(reasonRows("7d", "met_no_money", { REVISIT: 0 })).toEqual([]);
    expect(reasonRows("7d", "met_no_money", undefined)).toEqual([]);
  });
});

describe("empty today is explicit", () => {
  it("says no visits recorded yet today when planned > 0 and visited = 0", () => {
    expect(emptyVisitsMessage(TODAY_EMPTY)).toBe("No visits recorded yet today.");
  });
  it("says no routes when nothing was planned", () => {
    expect(emptyVisitsMessage({ ...TODAY_EMPTY, planned: 0 })).toBe("No routes planned for today.");
    expect(emptyVisitsMessage({ ...TODAY_EMPTY, planned: 0, window: "7d" })).toBe("No routes planned in the last 7 days.");
  });
  it("is silent once anything was visited", () => {
    expect(emptyVisitsMessage({ ...TODAY_EMPTY, visited: 1 })).toBeNull();
  });
});
