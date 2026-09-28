import api from "./axios";
import type { LoginResponse } from "@/types";
import type { LoginResult } from "@/lib/authFlow";

// 2026-09-28 (A08, d4): totpCode once the user has enrolled; the answer may be
// a session or the step still owed (lib/authFlow.afterLogin decides where).
export async function login(email: string, password: string, deviceId: string,
                            totpCode?: string): Promise<LoginResult> {
  const { data } = await api.post<LoginResult>("/auth/login", {
    email,
    password,
    device_id: deviceId,
    ...(totpCode ? { totp_code: totpCode } : {}),
  });
  return data;
}

// collection_dashboard: exchanges a quick-login link token for a real session
export async function quickLogin(token: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/quick-login", { token });
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

export async function getMe() {
  const { data } = await api.get("/auth/me");
  return data;
}

// ── A06 invitations ──────────────────────────────────────────────────────────
export interface InvitePreview {
  email: string;
  full_name: string | null;
  role: string;
  organisation: string | null;
  expires_at: string;
}

export async function previewInvite(token: string): Promise<InvitePreview> {
  const { data } = await api.post<InvitePreview>("/auth/invites/preview", { token });
  return data;
}

export async function acceptInvite(token: string, password: string, deviceId: string): Promise<LoginResult> {
  const { data } = await api.post<LoginResult>("/auth/invites/accept", { token, password, device_id: deviceId });
  return data;
}

// ── A07 passwords ────────────────────────────────────────────────────────────
export async function forgotPassword(identifier: string): Promise<{ request_id: string; message: string }> {
  const { data } = await api.post("/auth/password/forgot", { identifier });
  return data;
}

export async function verifyResetCode(requestId: string, code: string): Promise<{ reset_token: string }> {
  const { data } = await api.post("/auth/password/forgot/verify", { request_id: requestId, code });
  return data;
}

export async function resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
  const { data } = await api.post("/auth/password/reset", { token, new_password: newPassword });
  return data;
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<{ message: string }> {
  const { data } = await api.post("/auth/password/change", {
    current_password: currentPassword, new_password: newPassword,
  });
  return data;
}

// ── A08 two-factor sign-in ───────────────────────────────────────────────────
export interface MfaStatus { enabled: boolean; allowed: boolean; required: boolean; configured: boolean }
export interface MfaSecret { secret: string; otpauth_uri: string }

export async function getMfaStatus(): Promise<MfaStatus> {
  const { data } = await api.get<MfaStatus>("/auth/mfa");
  return data;
}

export async function startMfaSetup(): Promise<MfaSecret> {
  const { data } = await api.post<MfaSecret>("/auth/mfa/setup");
  return data;
}

export async function confirmMfaSetup(code: string): Promise<{ message: string }> {
  const { data } = await api.post("/auth/mfa/confirm", { code });
  return data;
}

export async function disableMfa(code: string): Promise<{ message: string }> {
  const { data } = await api.post("/auth/mfa/disable", { code });
  return data;
}

/** The withheld-login path: login answered ENROLL_MFA with a ticket. */
export async function startMfaWithTicket(ticket: string): Promise<MfaSecret> {
  const { data } = await api.post<MfaSecret>("/auth/mfa/enroll/start", { enrollment_token: ticket });
  return data;
}

export async function confirmMfaWithTicket(ticket: string, code: string, deviceId: string): Promise<LoginResult> {
  const { data } = await api.post<LoginResult>("/auth/mfa/enroll/confirm", {
    enrollment_token: ticket, code, device_id: deviceId,
  });
  return data;
}
