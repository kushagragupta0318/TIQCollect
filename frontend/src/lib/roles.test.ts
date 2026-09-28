// Routing and role guards, every role against every portal.
//
// There is no DOM test library in this repo, so this drives the SAME functions
// the components call — RootRedirect (homeFor), ProtectedRoute and the bank
// tree's guard (guardRedirect), LoginPage (homeFor), QuickLoginPage
// (quickLoginLanding) — through a small model of App.tsx's route table, and
// follows redirects hop by hop the way the router would. The textual checks at
// the bottom tie that model to App.tsx and the pages, so the two cannot drift
// without a test failing.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AGENT_HOME,
  BANK_HOME,
  BANK_PORTAL_ROLES,
  MANAGER_HOME,
  PORTAL_GUARDS,
  UNKNOWN_ROLE_HOME,
  guardRedirect,
  homeFor,
  isBankPortalRole,
  quickLoginLanding,
} from "./roles";
import { BANK_HOME_PATH } from "../bank/layout/navigation";

type User = { role: string } | null;

/** One hop of App.tsx's routing: the redirect a path produces, or null if it renders. */
function hop(path: string, user: User): string | null {
  const signedIn = user != null;
  if (path === "/") return signedIn ? homeFor(user.role) : null; // RootRedirect (landing page when signed out)
  if (path === "/login") return null; // unguarded
  const guard = PORTAL_GUARDS.find((g) => path === g.prefix || path.startsWith(`${g.prefix}/`));
  if (!guard) return "/"; // <Route path="*"> → "/"
  return guardRedirect(user, signedIn, guard.roles);
}

/** Follow redirects until a page renders; throw on a loop. */
function land(path: string, user: User): { at: string; hops: string[] } {
  const hops = [path];
  for (let i = 0; i < 8; i++) {
    const next = hop(hops[hops.length - 1], user);
    if (next == null) return { at: hops[hops.length - 1], hops };
    if (hops.includes(next)) throw new Error(`redirect loop: ${[...hops, next].join(" → ")}`);
    hops.push(next);
  }
  throw new Error(`no landing after 8 hops: ${hops.join(" → ")}`);
}

const ROLES: [string, string][] = [
  ["FIELD_AGENT", AGENT_HOME],
  ["AGENCY_MANAGER", MANAGER_HOME],
  ["AGENCY_ADMIN", MANAGER_HOME],
  ["BANK_ADMIN", BANK_HOME],
  ["BANK_ANALYST", BANK_HOME],
  ["BANK_TECHOPS", BANK_HOME],
  ["PLATFORM_ADMIN", BANK_HOME],
];

// A deep link into every portal, including the one that looped before the fix.
const DEEP_LINKS = ["/agent/home", "/agent/cases/42", "/agent/visit/42", "/manager/overview", "/manager/analytics", "/bank/overview", "/bank/admin/audit", "/nowhere"];

describe.each(ROLES)("%s", (role, home) => {
  const user = { role };

  it("logs in to its own home, and that home admits it", () => {
    expect(homeFor(role)).toBe(home);
    expect(land(homeFor(role), user).at).toBe(home);
  });

  it("'/' sends it home", () => {
    expect(land("/", user).at).toBe(home);
  });

  it.each(DEEP_LINKS)("a deep link to %s lands inside its own portal without looping", (path) => {
    const { at } = land(path, user);
    const own = PORTAL_GUARDS.find((g) => home.startsWith(`${g.prefix}/`))!;
    expect(at === home || at.startsWith(`${own.prefix}/`)).toBe(true);
    // Another portal's route always resolves to exactly the home page.
    if (!path.startsWith(`${own.prefix}/`) && path !== "/nowhere") expect(at).toBe(home);
  });
});

describe("signed out and unknown roles", () => {
  it.each(DEEP_LINKS)("signed out, %s goes to /login (or the landing page)", (path) => {
    const { at } = land(path, null);
    expect(["/login", "/"]).toContain(at);
  });

  it("a role this build does not know lands on /login rather than looping", () => {
    for (const path of ["/", ...DEEP_LINKS]) {
      expect(land(path, { role: "SERVICE" }).at).toBe(UNKNOWN_ROLE_HOME);
    }
  });

  it("ProtectedRoute lets an admitted role through", () => {
    expect(guardRedirect({ role: "AGENCY_ADMIN" }, true, ["AGENCY_MANAGER", "AGENCY_ADMIN"])).toBeNull();
    expect(guardRedirect({ role: "AGENCY_ADMIN" }, false, ["AGENCY_ADMIN"])).toBe("/login");
  });
});

describe("the quick-login link", () => {
  it("still lands a manager on Analytics, and everyone else at home", () => {
    expect(quickLoginLanding("AGENCY_MANAGER")).toBe("/manager/analytics");
    expect(quickLoginLanding("FIELD_AGENT")).toBe(AGENT_HOME);
    expect(quickLoginLanding("BANK_ANALYST")).toBe(BANK_HOME);
  });
});

describe("the definitions are the ones the app uses", () => {
  const src = join(__dirname, "..");
  const read = (rel: string) => readFileSync(join(src, rel), "utf8");

  it("the bank home is the bank shell's home", () => {
    expect(BANK_HOME).toBe(BANK_HOME_PATH);
    for (const r of BANK_PORTAL_ROLES) expect(isBankPortalRole(r)).toBe(true);
  });

  it("App.tsx guards each portal with the shared role groups, and RootRedirect uses homeFor", () => {
    const app = read("App.tsx");
    expect(app.match(/allowedRoles=\{AGENT_ROLES\}/g)).toHaveLength(2);
    expect(app.match(/allowedRoles=\{MANAGER_ROLES\}/g)).toHaveLength(1);
    expect(app).not.toMatch(/allowedRoles=\{\[/); // no literal role lists
    expect(app).toMatch(/<Navigate to=\{homeFor\(user\.role\)\}/);
    expect(app).toMatch(/path="\/bank\/\*"/);
  });

  it("the bank tree, ProtectedRoute and both login pages call the shared rule", () => {
    expect(read("bank/BankApp.tsx")).toMatch(/guardRedirect\(user, isAuthenticated, BANK_PORTAL_ROLES\)/);
    expect(read("components/layout/ProtectedRoute.tsx")).toMatch(/guardRedirect\(user, isAuthenticated, allowedRoles\)/);
    expect(read("pages/auth/LoginPage.tsx")).toMatch(/navigate\(homeFor\(data\.role\)/);
    expect(read("pages/auth/QuickLoginPage.tsx")).toMatch(/navigate\(quickLoginLanding\(data\.role\)/);
  });
});
