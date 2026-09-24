// The roles that may open the bank portal (plan §2.1). The backend adds them in
// a parallel branch; until `UserRole` in src/types carries them, the bank tree
// compares plain strings so this module does not have to edit the shared type.
export const BANK_PORTAL_ROLES = ["BANK_ADMIN", "BANK_ANALYST", "BANK_TECHOPS", "PLATFORM_ADMIN"] as const;

export type BankPortalRole = (typeof BANK_PORTAL_ROLES)[number];

export function isBankPortalRole(role: string | null | undefined): role is BankPortalRole {
  return role != null && (BANK_PORTAL_ROLES as readonly string[]).includes(role);
}

/** The persona card's role line (CC `ROLE_LABELS`, Sidebar.jsx:19-24). */
export const BANK_ROLE_LABELS: Record<BankPortalRole, string> = {
  BANK_ADMIN: "Bank Admin",
  BANK_ANALYST: "Bank Analyst",
  BANK_TECHOPS: "Tech Ops",
  PLATFORM_ADMIN: "Platform Admin",
};
