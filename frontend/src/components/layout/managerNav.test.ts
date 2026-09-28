/**
 * G06 — the manager's phone bar is four tabs and a More sheet, read from the
 * same single list as the desktop rail.
 */
import { describe, expect, it } from "vitest";
import {
  MANAGER_NAV, MOBILE_MORE, MOBILE_PRIMARY, activeMoreItem, isSheetOpen, isWithin, moreButtonActive,
} from "./managerNav";

const paths = (items: readonly { to: string }[]) => items.map((i) => i.to);

describe("the one nav list", () => {
  it("puts exactly four items on the phone bar", () => {
    expect(MOBILE_PRIMARY).toHaveLength(4);
  });

  it("splits into primary and More with nothing lost and nothing duplicated", () => {
    const all = paths(MANAGER_NAV);
    expect(new Set(all).size).toBe(all.length); // no duplicate destinations to begin with

    const primary = paths(MOBILE_PRIMARY);
    const more = paths(MOBILE_MORE);
    expect(primary.filter((p) => more.includes(p))).toEqual([]); // disjoint
    expect([...primary, ...more].sort()).toEqual([...all].sort()); // union is the whole list
    expect(primary.length + more.length).toBe(all.length);
  });

  it("keeps the desktop rail's order within each half", () => {
    const rank = (to: string) => paths(MANAGER_NAV).indexOf(to);
    const ascending = (xs: string[]) => xs.every((x, i) => i === 0 || rank(xs[i - 1]) < rank(x));
    expect(ascending(paths(MOBILE_PRIMARY))).toBe(true);
    expect(ascending(paths(MOBILE_MORE))).toBe(true);
  });
});

describe("the More button's active state", () => {
  it("is lit on every page that lives in the sheet, and on pages under it", () => {
    expect(MOBILE_MORE.length).toBeGreaterThan(0);
    for (const item of MOBILE_MORE) {
      expect(moreButtonActive(item.to, false)).toBe(true);
      expect(moreButtonActive(`${item.to}/some-id`, false)).toBe(true);
      expect(activeMoreItem(item.to)?.label).toBe(item.label);
    }
  });

  it("is dark on a primary tab's page, where that tab is lit instead", () => {
    for (const item of MOBILE_PRIMARY) {
      expect(moreButtonActive(item.to, false)).toBe(false);
      expect(activeMoreItem(item.to)).toBeNull();
    }
  });

  it("is lit while its sheet is open, whatever the page", () => {
    expect(moreButtonActive(MOBILE_PRIMARY[0].to, true)).toBe(true);
  });
});

describe("isWithin — NavLink's own matching rule", () => {
  it("matches the path, a trailing slash and anything under it, ignoring case", () => {
    expect(isWithin("/manager/agents", "/manager/agents")).toBe(true);
    expect(isWithin("/manager/agents/", "/manager/agents")).toBe(true);
    expect(isWithin("/manager/agents/42", "/manager/agents")).toBe(true);
    expect(isWithin("/Manager/Analytics", "/manager/analytics")).toBe(true);
  });

  it("does not match a sibling that merely shares a prefix", () => {
    expect(isWithin("/manager/agentsX", "/manager/agents")).toBe(false);
    expect(isWithin("/manager", "/manager/agents")).toBe(false);
  });
});

describe("isSheetOpen — the sheet closes on navigation", () => {
  const here = { pathname: "/manager/overview", key: "a" };

  it("is closed until opened", () => {
    expect(isSheetOpen(null, here)).toBe(false);
  });

  it("is open on the location it was opened on", () => {
    expect(isSheetOpen(here, here)).toBe(true);
  });

  it("closes when the router moves to a new location", () => {
    const next = { pathname: "/manager/analytics", key: "b" };
    expect(isSheetOpen(here, next)).toBe(false);
  });

  it("stays closed on a return to the same history entry (same key, new location)", () => {
    // Back then Forward hands the router a fresh location object carrying the
    // entry's old key. Keyed on `key`, the sheet would reopen here.
    const returned = { ...here };
    expect(isSheetOpen(here, returned)).toBe(false);
  });
});
