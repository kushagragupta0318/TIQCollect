import { describe, expect, it } from "vitest";
import { CASH_ATTENTION_SHARE_PCT, cashCallout, modeWords, orderedModes, shareOfLargest, type PaymentModeRow } from "./paymentModes";

const row = (mode: string, amount: number, count = 1): PaymentModeRow => ({
  mode, amount, count, share_pct: 0, avg_ticket: count ? amount / count : 0,
  is_cash: mode === "CASH", is_digital: ["UPI", "NEFT", "RTGS"].includes(mode),
});

describe("orderedModes", () => {
  it("lists largest first regardless of wire order, ties by name", () => {
    const rows = [row("DD", 10), row("CASH", 50), row("UPI", 50), row("RTGS", 0)];
    expect(orderedModes(rows).map((r) => r.mode)).toEqual(["CASH", "UPI", "DD", "RTGS"]);
  });
  it("survives an absent list", () => {
    expect(orderedModes(undefined)).toEqual([]);
  });
});

describe("shareOfLargest", () => {
  it("makes the largest bar full width and scales the rest", () => {
    const rows = [row("CASH", 200), row("UPI", 100), row("DD", 0)];
    expect(shareOfLargest(200, rows)).toBe(1);
    expect(shareOfLargest(100, rows)).toBe(0.5);
    expect(shareOfLargest(0, rows)).toBe(0);
  });
  it("is zero when nothing was collected", () => {
    expect(shareOfLargest(0, [row("CASH", 0)])).toBe(0);
  });
});

describe("cashCallout", () => {
  it("calls out cash at or above the attention share", () => {
    expect(cashCallout({ cash_share_pct: CASH_ATTENTION_SHARE_PCT, cash_amount: 1 })).toMatch(/Cash is 25% of collections/);
    expect(cashCallout({ cash_share_pct: 30.4, cash_amount: 864734 })).toMatch(/^Cash is 30%/);
  });
  it("stays quiet below it, or when there is no cash at all", () => {
    expect(cashCallout({ cash_share_pct: 21.6, cash_amount: 2038829 })).toBeNull();
    expect(cashCallout({ cash_share_pct: 100, cash_amount: 0 })).toBeNull();
  });
});

describe("modeWords", () => {
  it("reads modes as a manager says them", () => {
    expect(modeWords("DD")).toBe("Demand draft");
    expect(modeWords("UPI")).toBe("UPI");
    expect(modeWords("SOMETHING_NEW")).toBe("SOMETHING NEW");
  });
});
