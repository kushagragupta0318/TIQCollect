// The bank portal's Bank Users admin API (P3 K01 — Admin > Bank Users).
//
// PLACEMENT. In src/api/ alongside bank.ts / manager.ts / agent.ts, this
// repo's one home for typed wrappers around `api` from api/axios — the same
// reasoning api/bank.ts's own header gives.
//
// CONTRACT SOURCE. Every shape below is read off
// backend/app/api/v1/endpoints/bank_users_admin.py and
// backend/app/services/bank/bank_user_service.py (K01, this lane). InviteResult
// mirrors api/bank.ts's InviteMasterLoginResult because both are
// invite_service.create_invite's return value.
import api from "./axios";
import type { InviteSummary } from "./bank";

/** The staff roles a bank user can hold — backend/app/models/user.py BANK_ROLES. */
export const BANK_USER_ROLES = ["BANK_ADMIN", "BANK_ANALYST", "BANK_TECHOPS"] as const;
export type BankUserRole = (typeof BANK_USER_ROLES)[number];

/** The two roles a bank admin may move a user between (change_role's own set).
 *  BANK_ADMIN is deliberately absent — promotion to admin is by invite, not a
 *  live-account role flip. */
export const ROLE_CHANGE_CHOICES = ["BANK_ANALYST", "BANK_TECHOPS"] as const;

export const BANK_USER_ROLE_LABELS: Record<string, string> = {
  BANK_ADMIN: "Administrator",
  BANK_ANALYST: "Analyst",
  BANK_TECHOPS: "Tech Ops",
};

export interface BankUser {
  user_id: string;
  full_name: string;
  email: string;
  phone: string;
  role: BankUserRole;
  is_active: boolean;
  status: "ACTIVE" | "DEACTIVATED";
  mfa_enabled: boolean;
  mfa_required: boolean;
  last_login_at: string | null;
}

export interface InviteBankUserBody {
  full_name: string;
  email: string;
  phone: string;
  role: BankUserRole;
  channel?: "LINK" | "SMS";
}

/** invite_service.create_invite's return value — the invite summary, whether
 *  it was delivered, and (channel LINK only) the one-time link, shown once. */
export interface InviteBankUserResult {
  invite: InviteSummary;
  delivered: boolean | null;
  token?: string;
  path?: string;
}

export async function listBankUsers(): Promise<BankUser[]> {
  const { data } = await api.get<BankUser[]>("/bank/users");
  return data;
}

export async function inviteBankUser(body: InviteBankUserBody): Promise<InviteBankUserResult> {
  const { data } = await api.post<InviteBankUserResult>("/bank/users/invite", body);
  return data;
}

export async function changeBankUserRole(userId: string, role: BankUserRole): Promise<BankUser> {
  const { data } = await api.patch<BankUser>(`/bank/users/${userId}/role`, { role });
  return data;
}

export async function deactivateBankUser(userId: string, reason: string): Promise<BankUser> {
  const { data } = await api.post<BankUser>(`/bank/users/${userId}/deactivate`, { reason });
  return data;
}

export async function reactivateBankUser(userId: string): Promise<BankUser> {
  const { data } = await api.post<BankUser>(`/bank/users/${userId}/reactivate`, {});
  return data;
}

/** Texts a single-use set-password link to the user's own phone and ends their
 *  sessions — the admin never sees the link. */
export async function resetBankUserLogin(userId: string): Promise<{ sent: boolean; expires_at: string }> {
  const { data } = await api.post<{ sent: boolean; expires_at: string }>(`/bank/users/${userId}/reset-login`, {});
  return data;
}
