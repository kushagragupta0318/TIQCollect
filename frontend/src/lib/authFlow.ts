// ─── CHANGELOG ───────────────────────────────────────────────────────────────
// 2026-09-28 — NEW (P1 A11, d4). The decisions the auth pages share, as pure
//   functions so they can be tested without mounting a page:
//   - which requests the 401 interceptor must NOT treat as "session expired"
//     (a wrong TOTP code at /auth/login is a 401 about the ATTEMPT, not the
//     session — refreshing and retrying it, or hanging on a stuck refresh
//     flag, is wrong: see api/axios.ts);
//   - where a login response sends the person (a session, or the step the
//     server says is still owed: CHANGE_PASSWORD / ENROLL_MFA);
//   - the password rule, mirroring backend services/credentials.py.
// ─────────────────────────────────────────────────────────────────────────────
import { homeFor } from "@/lib/roles";

/** Endpoints that are ANSWERS about credentials, never about an expired session. */
const PUBLIC_AUTH_PATHS = [
  "/auth/login",
  "/auth/quick-login",
  "/auth/refresh",
  "/auth/invites/",
  "/auth/password/forgot",
  "/auth/password/forgot/verify",
  "/auth/password/reset",
  "/auth/mfa/enroll/",
];

export function isPublicAuthRequest(url: string | undefined): boolean {
  if (!url) return false;
  const path = url.replace(/^https?:\/\/[^/]+/, "").replace(/^\/api\/v1/, "").split("?")[0];
  return PUBLIC_AUTH_PATHS.some((p) => (p.endsWith("/") ? path.startsWith(p) : path === p));
}

export interface SessionResponse {
  access_token: string;
  refresh_token: string;
  role: string;
  user_id: string;
  full_name: string;
  /** A09b: sent once, when the server has just bound a field agent's device. */
  device_secret?: string;
}

export interface NextStepResponse {
  next: "CHANGE_PASSWORD" | "ENROLL_MFA";
  message: string;
  reset_token?: string | null;
  enrollment_token?: string | null;
}

export type LoginResult = SessionResponse | NextStepResponse;

export function isNextStep(r: LoginResult): r is NextStepResponse {
  return typeof (r as NextStepResponse).next === "string";
}

/**
 * Where to go after a login-shaped response. Tokens owed a next step travel
 * in router STATE, never in the URL, so they stay out of history and logs.
 */
export function afterLogin(r: LoginResult): { to: string; state?: Record<string, string> } {
  if (!isNextStep(r)) return { to: homeFor(r.role) };
  if (r.next === "CHANGE_PASSWORD" && r.reset_token) {
    return { to: "/reset-password", state: { token: r.reset_token, reason: "first-login" } };
  }
  if (r.next === "ENROLL_MFA" && r.enrollment_token) {
    return { to: "/mfa-setup", state: { ticket: r.enrollment_token } };
  }
  return { to: "/login" };
}

// ── the password rule (backend: services/credentials.py) ─────────────────────
export const PASSWORD_MIN_LENGTH = 10;
export const PASSWORD_MAX_LENGTH = 128;
export const COMMON_PASSWORDS: readonly string[] = [
  "password", "passw", "welcome", "qwerty", "qwertyuiop", "admin", "letmein", "iloveyou",
  "tiqcollect", "collection", "collections", "manager", "agent", "abcdef", "abcdefgh",
];

/** null when acceptable, else the reason — the same words the server uses. */
export function passwordProblem(password: string, email?: string | null): string | null {
  const p = password ?? "";
  if (p.length < PASSWORD_MIN_LENGTH) return `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
  if (p.length > PASSWORD_MAX_LENGTH) return `Use at most ${PASSWORD_MAX_LENGTH} characters.`;
  if (!/[A-Za-z]/.test(p) || !/\d/.test(p)) return "Use at least one letter and one number.";
  if (COMMON_PASSWORDS.includes(p.toLowerCase().replace(/[^a-z]/g, ""))) return "That password is too common.";
  const local = (email ?? "").split("@")[0].toLowerCase();
  if (local.length >= 4 && p.toLowerCase().includes(local)) return "Don't use your email address in your password.";
  return null;
}

/**
 * The token a credential page works with: from router state (handed over by
 * login or the forgot flow — never in the URL) or from `?token=` (a link an
 * admin or an SMS delivered). Pure: the page strips the query afterwards.
 */
export function tokenFrom(search: string, state: unknown): string | null {
  const fromState = (state as { token?: unknown } | null)?.token;
  if (typeof fromState === "string" && fromState) return fromState;
  return new URLSearchParams(search).get("token");
}
