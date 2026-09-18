import { describe, expect, it } from "vitest";
import { chartMonths, keptTrend, openNote, type PtpOutcomeMonth, type PtpOutcomes } from "./ptpOutcomes";

const m = (month: string, o: Partial<PtpOutcomeMonth> = {}): PtpOutcomeMonth => ({
  month, total: 0, honored: 0, partly: 0, broken: 0, rescheduled: 0, open: 0,
  promised_amount: 0, paid_amount: 0, kept_rate_pct: null, is_current: false, is_future: false, ...o,
});

describe("keptTrend", () => {
  it("uses the first and last CLOSED months with a decided rate", () => {
    const t = keptTrend([
      m("2026-04", { kept_rate_pct: 41 }), m("2026-05"), m("2026-06", { kept_rate_pct: 34 }),
      m("2026-08", { kept_rate_pct: 54 }), m("2026-09", { kept_rate_pct: 28, is_current: true }),
    ]);
    expect(t?.from.month).toBe("2026-04");
    expect(t?.to.month).toBe("2026-08");   // not the in-progress September
  });
  it("falls back to the current month when fewer than two closed months exist", () => {
    const t = keptTrend([m("2026-08", { kept_rate_pct: 54 }), m("2026-09", { kept_rate_pct: 28, is_current: true })]);
    expect([t?.from.month, t?.to.month]).toEqual(["2026-08", "2026-09"]);
  });
  it("is null with fewer than two decided months", () => {
    expect(keptTrend([m("2026-09", { kept_rate_pct: 50 })])).toBeNull();
    expect(keptTrend([m("2026-08"), m("2026-09")])).toBeNull();
  });
  it("never uses a future month as a trend point", () => {
    expect(keptTrend([m("2026-08", { kept_rate_pct: 54 }), m("2026-10", { kept_rate_pct: 100, is_future: true })])).toBeNull();
  });
});

describe("openNote", () => {
  it("names this month's open promises and next month's", () => {
    expect(openNote([m("2026-09", { open: 56, is_current: true }), m("2026-10", { open: 84, is_future: true })]))
      .toBe("56 of this month's promises are still open and not in its rate · 84 more fall due next month");
  });
  it("is null when nothing is open", () => {
    expect(openNote([m("2026-09", { is_current: true, honored: 3 })])).toBeNull();
  });
});

describe("chartMonths", () => {
  it("sorts oldest first and includes months beyond the window", () => {
    const d: PtpOutcomes = { months: [m("2026-10"), m("2026-08"), m("2026-09")], window: ["2026-08", "2026-09"],
      agent_id: null, effective_month: "2026-09", definition: "" };
    expect(chartMonths(d).map((x) => x.month)).toEqual(["2026-08", "2026-09", "2026-10"]);
    expect(chartMonths(null)).toEqual([]);
  });
});
