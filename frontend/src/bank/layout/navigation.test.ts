import { describe, expect, it } from "vitest";
import { BANK_HOME_PATH, BANK_NAV_ITEMS, BANK_SECTIONS, bankHref, findNavItem, isBuiltPath } from "./navigation";
import { BANK_PORTAL_ROLES, BANK_ROLE_LABELS, isBankPortalRole } from "./bankRoles";
import { PAGE_ENTRIES, searchEntries } from "./searchIndex";

describe("bank navigation — plan §5.1", () => {
  it("has the six sections, in order, with the planned pages", () => {
    expect(BANK_SECTIONS.map((s) => [s.label, s.items.map((i) => i.name)])).toEqual([
      ["Command Center", ["Overview", "Analytics", "Alerts"]],
      ["AI Strategy", ["Monte Carlo Simulator", "Cash Forecast", "Scenario Lab", "Board Reports"]],
      ["Agencies", ["Directory", "Performance", "Placement", "Onboard Agency", "Messaging"]],
      ["Governance", ["Models"]],
      ["Tech Ops", ["AI Agents", "MLOps", "Data Quality", "Usage & Cost"]],
      ["Admin", ["Bank Users", "Regions", "Settings", "Audit"]],
    ]);
  });

  it("every path is unique, relative, and names the tasks that build it", () => {
    const paths = BANK_NAV_ITEMS.map((i) => i.path);
    expect(new Set(paths).size).toBe(paths.length);
    for (const item of BANK_NAV_ITEMS) {
      expect(item.path.startsWith("/")).toBe(false);
      expect(item.tasks).toMatch(/^[A-Z]\d{2}/);
      expect(item.summary.length).toBeGreaterThan(10);
    }
  });

  it("the gallery route cannot collide with a page", () => {
    expect(BANK_NAV_ITEMS.some((i) => i.path === "_gallery")).toBe(false);
  });

  it("home is the Overview", () => {
    expect(BANK_HOME_PATH).toBe("/bank/overview");
  });

  it("findNavItem matches exact paths and sub-routes, not prefixes of other words", () => {
    expect(findNavItem("/bank/analytics")?.name).toBe("Analytics");
    expect(findNavItem("/bank/analytics/")?.name).toBe("Analytics");
    expect(findNavItem("/bank/agencies/directory/ag-104")?.name).toBe("Directory");
    expect(findNavItem("/bank/analyticsX")).toBeUndefined();
    expect(findNavItem("/manager/overview")).toBeUndefined();
    expect(bankHref({ path: "admin/audit" })).toBe("/bank/admin/audit");
  });
});

describe("bank portal roles — plan §2.1", () => {
  it("admits the four bank-side roles and nobody else", () => {
    for (const role of BANK_PORTAL_ROLES) expect(isBankPortalRole(role)).toBe(true);
    for (const role of ["FIELD_AGENT", "AGENCY_MANAGER", "AGENCY_ADMIN", "SERVICE", "", null, undefined]) {
      expect(isBankPortalRole(role)).toBe(false);
    }
  });

  it("labels every role", () => {
    for (const role of BANK_PORTAL_ROLES) expect(BANK_ROLE_LABELS[role]).toBeTruthy();
  });
});

describe("top-bar search", () => {
  // 2026-10-01: the index is BUILT pages only. It indexed every nav item,
  // which after the rail started hiding unbuilt items meant search was the one
  // way left to reach "Not built yet".
  it("indexes every built page, and nothing that has no page", () => {
    expect(PAGE_ENTRIES).toHaveLength(BANK_NAV_ITEMS.filter((i) => isBuiltPath(i.path)).length);
    expect(PAGE_ENTRIES.length).toBeLessThan(BANK_NAV_ITEMS.length);
    expect(PAGE_ENTRIES.every((e) => isBuiltPath((e.path ?? "").replace(/^\/bank\//, "")))).toBe(true);
  });

  it("matches title or subtitle case-insensitively, capped at five, nothing for a blank query", () => {
    expect(searchEntries(PAGE_ENTRIES, "models").map((r) => r.title)).toEqual(["Models"]);
    expect(searchEntries(PAGE_ENTRIES, "/bank/agencies").map((r) => r.title))
      .toEqual(["Directory", "Performance", "Placement", "Onboard Agency", "Messaging"]);
    expect(searchEntries(PAGE_ENTRIES, "bank")).toHaveLength(5);
    expect(searchEntries(PAGE_ENTRIES, "   ")).toEqual([]);
    expect(searchEntries(PAGE_ENTRIES, "scenario")).toEqual([]);   // built pages only (Scenario Lab unbuilt)
  });
});
