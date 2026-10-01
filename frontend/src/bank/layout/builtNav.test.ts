/**
 * The rail and the router must agree (navigation.BUILT_NAV_PATHS).
 *
 * The failure this prevents: a page is built and routed but never appears in the
 * rail, or an item sits in the rail and lands on "Not built yet". Both read as a
 * broken product. The list is checked against BankApp.tsx's own routing, read as
 * source — so adding a page without listing it fails here.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BANK_NAV_ITEMS, BUILT_BANK_SECTIONS, BUILT_NAV_PATHS, isBuiltPath } from "./navigation";
import { PAGE_ENTRIES } from "./searchIndex";

const APP = readFileSync(join(__dirname, "..", "BankApp.tsx"), "utf8");

/** Nav paths BankApp gives a real page: the BUILT_PAGES map plus the explicit routes. */
function routedPaths(): Set<string> {
  const out = new Set<string>();
  // The object literal only: keys are quoted ("agencies/placement") or bare
  // identifiers (overview), so match both rather than one spelling.
  const decl = APP.indexOf("const BUILT_PAGES");
  const body = APP.slice(APP.indexOf("{", decl), APP.indexOf("};", decl));
  for (const m of body.matchAll(/(?:"([a-z0-9/-]+)"|([a-z][a-z0-9-]*))\s*:/g)) {
    const key = m[1] ?? m[2];
    if (key) out.add(key);
  }
  // The explicitly routed screens, declared as path constants.
  for (const m of APP.matchAll(/const [A-Z_]+_PATH = "([a-z0-9/-]+)"/g)) out.add(m[1]);
  return out;
}

describe("the rail and the router agree", () => {
  it("every built path is a real nav item", () => {
    const navPaths = new Set(BANK_NAV_ITEMS.map((i) => i.path));
    for (const p of BUILT_NAV_PATHS) expect(navPaths.has(p), `${p} is not a nav item`).toBe(true);
  });

  it("every path BankApp routes to a real page is listed as built", () => {
    const navPaths = new Set(BANK_NAV_ITEMS.map((i) => i.path));
    for (const p of routedPaths()) {
      if (!navPaths.has(p)) continue;          // e.g. "customers": reached from a row, not the rail
      expect(isBuiltPath(p), `${p} is routed to a real page but missing from BUILT_NAV_PATHS`).toBe(true);
    }
  });

  it("nothing in the rail lands on the placeholder", () => {
    const routed = routedPaths();
    for (const section of BUILT_BANK_SECTIONS) {
      for (const item of section.items) {
        expect(routed.has(item.path), `${item.path} is in the rail but has no page`).toBe(true);
      }
    }
  });

  it("hides the unbuilt items and drops a section left empty", () => {
    const shown = BUILT_BANK_SECTIONS.flatMap((s) => s.items.map((i) => i.path));
    expect(shown).not.toContain("tech-ops/mlops");
    expect(shown).not.toContain("admin/users");
    expect(shown).not.toContain("alerts");
    expect(BUILT_BANK_SECTIONS.map((s) => s.label)).not.toContain("Tech Ops");   // every item unbuilt
    expect(BUILT_BANK_SECTIONS.every((s) => s.items.length > 0)).toBe(true);
  });

  it("keeps the routes registered, so a deep link still resolves", () => {
    // The placeholder route is still emitted for every nav item.
    expect(APP).toMatch(/BANK_NAV_ITEMS\s*\.filter/);
    expect(APP).toContain("BankPlaceholderPage");
  });

  it("offers only built pages in the top-bar search, for the same reason", () => {
    const paths = PAGE_ENTRIES.map((e) => (e.path ?? "").replace(/^\/bank\//, ""));
    for (const p of paths) expect(isBuiltPath(p), `${p} is searchable but has no page`).toBe(true);
    expect(paths).toContain("overview");
  });

  it("the borrower page is reached from a row, never the rail", () => {
    expect(BANK_NAV_ITEMS.some((i) => i.path.startsWith("customers"))).toBe(false);
    expect(BUILT_NAV_PATHS.has("customers")).toBe(false);
  });
});
