// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file. ONE answer to "where does this role live", used by
//   every place that sends a signed-in user somewhere: RootRedirect,
//   ProtectedRoute, the bank tree's guard, LoginPage and QuickLoginPage.
//
//   WHY. Each of those decided it for itself as `role === "FIELD_AGENT" ?
//   "/agent/home" : "/manager/overview"`, which was right while there were two
//   portals. The bank portal made it wrong in four places at once: a bank user
//   who logged in was sent to /manager/overview, ProtectedRoute rejected them
//   there and sent them to /manager/overview again — a redirect loop, and the
//   same for any /manager/* or /agent/* deep link. Only RootRedirect had been
//   taught about the bank roles. Four copies of one rule, one of them updated:
//   the shape CLAUDE.md's "one definition, one place" rule exists to prevent.
//
//   Plain strings, not `UserRole`: the bank roles are added to the backend in
//   a parallel branch and to `src/types` after it, and a role this build does
//   not know must still land somewhere that cannot loop (`/login`).
// ─────────────────────────────────────────────────────────────────────────────

export const AGENT_ROLES = ["FIELD_AGENT"] as const;
export const MANAGER_ROLES = ["AGENCY_MANAGER", "AGENCY_ADMIN"] as const;
/** The roles that may open the bank portal (standalone plan §2.1). */
export const BANK_PORTAL_ROLES = ["BANK_ADMIN", "BANK_ANALYST", "BANK_TECHOPS", "PLATFORM_ADMIN"] as const;

export type BankPortalRole = (typeof BANK_PORTAL_ROLES)[number];

export const AGENT_HOME = "/agent/home";
export const MANAGER_HOME = "/manager/overview";
/** Must equal the bank shell's BANK_HOME_PATH (pinned by a test there). */
export const BANK_HOME = "/bank/overview";
/** Where a signed-in role this build does not recognise goes: a page with no guard, so it cannot loop. */
export const UNKNOWN_ROLE_HOME = "/login";

const includes = (roles: readonly string[], role: string | null | undefined): boolean =>
  role != null && roles.includes(role);

export function isBankPortalRole(role: string | null | undefined): role is BankPortalRole {
  return includes(BANK_PORTAL_ROLES, role);
}

/** The landing page for a role. */
export function homeFor(role: string | null | undefined): string {
  if (includes(AGENT_ROLES, role)) return AGENT_HOME;
  if (includes(MANAGER_ROLES, role)) return MANAGER_HOME;
  if (isBankPortalRole(role)) return BANK_HOME;
  return UNKNOWN_ROLE_HOME;
}

/**
 * Where the collection_dashboard quick-login link lands: a manager on
 * Analytics (the link's purpose, unchanged), everyone else at their home.
 */
export function quickLoginLanding(role: string | null | undefined): string {
  const home = homeFor(role);
  return home === MANAGER_HOME ? "/manager/analytics" : home;
}

/**
 * The one guard decision: where a visitor to a route that admits
 * `allowedRoles` goes instead, or null to let them in. Signed out → /login;
 * signed in with a role the route does not admit → that role's own home,
 * which by construction admits it, so a redirect never lands on another
 * rejection.
 */
export function guardRedirect(
  user: { role: string } | null | undefined,
  isAuthenticated: boolean,
  allowedRoles: readonly string[],
): string | null {
  if (!isAuthenticated || !user) return "/login";
  if (allowedRoles.includes(user.role)) return null;
  return homeFor(user.role);
}

/** Which guard each portal's route tree sits behind — the table App.tsx mounts. */
export const PORTAL_GUARDS: readonly { prefix: string; roles: readonly string[] }[] = [
  { prefix: "/agent", roles: AGENT_ROLES },
  { prefix: "/manager", roles: MANAGER_ROLES },
  { prefix: "/bank", roles: BANK_PORTAL_ROLES },
];
