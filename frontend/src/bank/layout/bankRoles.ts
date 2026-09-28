// The bank portal's view of the roles. The definitions live in src/lib/roles.ts
// (one place, shared with RootRedirect, ProtectedRoute and the login pages);
// they are re-exported here so bank modules keep one import path.
import type { BankPortalRole } from "@/lib/roles";

export { BANK_PORTAL_ROLES, isBankPortalRole, type BankPortalRole } from "@/lib/roles";

/** The persona card's role line (CC `ROLE_LABELS`, Sidebar.jsx:19-24). */
export const BANK_ROLE_LABELS: Record<BankPortalRole, string> = {
  BANK_ADMIN: "Bank Admin",
  BANK_ANALYST: "Bank Analyst",
  BANK_TECHOPS: "Tech Ops",
  PLATFORM_ADMIN: "Platform Admin",
};
