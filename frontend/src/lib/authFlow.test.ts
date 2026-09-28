import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  COMMON_PASSWORDS, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, afterLogin, isNextStep,
  isPublicAuthRequest, passwordProblem, tokenFrom, type LoginResult,
} from "./authFlow";

// P1 A11 (d4, 2026-09-28): the decisions the credential pages share.

const session: LoginResult = {
  access_token: "a", refresh_token: "r", role: "AGENCY_MANAGER", user_id: "u", full_name: "Kavita Menon",
};

describe("the 401 interceptor leaves credential answers alone", () => {
  it.each([
    "/auth/login", "/api/v1/auth/login", "https://fieldops.example.in/api/v1/auth/login",
    "/auth/quick-login", "/auth/refresh", "/auth/invites/accept", "/auth/invites/preview",
    "/auth/password/forgot", "/auth/password/forgot/verify", "/auth/password/reset",
    "/auth/mfa/enroll/start", "/auth/mfa/enroll/confirm",
  ])("%s is a credentials answer", (url) => {
    expect(isPublicAuthRequest(url)).toBe(true);
  });

  it.each(["/auth/me", "/auth/logout", "/auth/password/change", "/auth/mfa", "/auth/mfa/confirm",
           "/manager/overview", "/auth/login-history", undefined])(
    "%s is a session request (refresh on 401)", (url) => {
      expect(isPublicAuthRequest(url)).toBe(false);
    });

  it("axios.ts consults it and no longer leaves the refresh flag stuck", () => {
    const src = readFileSync(join(__dirname, "..", "api", "axios.ts"), "utf8");
    expect(src).toMatch(/!isPublicAuthRequest\(originalRequest\.url\)/);
    const noToken = src.split("if (!refreshToken) {")[1].split("}")[0];
    expect(noToken).toMatch(/isRefreshing = false/);
  });
});

describe("where a login answer goes", () => {
  it("a session goes home by role", () => {
    expect(afterLogin(session)).toEqual({ to: "/manager/overview" });
    expect(afterLogin({ ...session, role: "FIELD_AGENT" })).toEqual({ to: "/agent/home" });
    expect(afterLogin({ ...session, role: "BANK_ANALYST" })).toEqual({ to: "/bank/overview" });
  });

  it("a first-login change goes to the reset page with the token in state, not the URL", () => {
    const r = afterLogin({ next: "CHANGE_PASSWORD", message: "m", reset_token: "tok-123" });
    expect(r).toEqual({ to: "/reset-password", state: { token: "tok-123", reason: "first-login" } });
    expect(r.to).not.toContain("tok-123");
  });

  it("a required enrollment goes to setup with the ticket in state", () => {
    expect(afterLogin({ next: "ENROLL_MFA", message: "m", enrollment_token: "tk" }))
      .toEqual({ to: "/mfa-setup", state: { ticket: "tk" } });
  });

  it("a next step with no token falls back to login", () => {
    expect(afterLogin({ next: "ENROLL_MFA", message: "m" })).toEqual({ to: "/login" });
    expect(isNextStep(session)).toBe(false);
  });
});

describe("the token a credential page works with", () => {
  it("prefers router state, then ?token=", () => {
    expect(tokenFrom("?token=from-url", { token: "from-state" })).toBe("from-state");
    expect(tokenFrom("?token=from-url", null)).toBe("from-url");
    expect(tokenFrom("", null)).toBeNull();
    expect(tokenFrom("", { token: 7 })).toBeNull();
  });
});

describe("the password rule is the server's", () => {
  it.each([
    ["short1A", "Use at least 10 characters."],
    ["lettersonlypassword", "Use at least one letter and one number."],
    ["1234567890123", "Use at least one letter and one number."],
    ["Password2026!", "That password is too common."],
    ["Harbour-Lights-2026", null],
  ])("%s -> %s", (p, want) => {
    expect(passwordProblem(p)).toBe(want);
  });

  it("refuses the email's local part", () => {
    expect(passwordProblem("neha.kapoor2026x", "neha.kapoor@example.in"))
      .toBe("Don't use your email address in your password.");
  });

  it("uses the same numbers and common list as services/credentials.py", () => {
    const py = readFileSync(join(__dirname, "..", "..", "..", "backend", "app", "services", "credentials.py"), "utf8");
    expect(Number(py.match(/PASSWORD_MIN_LENGTH = (\d+)/)![1])).toBe(PASSWORD_MIN_LENGTH);
    expect(Number(py.match(/PASSWORD_MAX_LENGTH = (\d+)/)![1])).toBe(PASSWORD_MAX_LENGTH);
    const block = py.split("_COMMON = frozenset({")[1].split("})")[0];
    const words = [...block.matchAll(/"([a-z]+)"/g)].map((m) => m[1]).sort();
    expect(words).toEqual([...COMMON_PASSWORDS].sort());
  });
});
