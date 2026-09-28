import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

// A10 (2026-09-24). Until then the login page shipped two "Quick demo login"
// buttons that typed agent002 / manager1 and their passwords into the form,
// LandingPage sent a `prefill` state that triggered them, and /manager-bridge
// logged ANY visitor in as manager1 with a password compiled into the bundle —
// called from public/collection_dashboard/, a static page linking every agency
// to that one manager. Demo accounts now live in the demo fixture only, listed
// once in backend/fixtures/README.md.
//
// Tripwire, not proof: a textual scan of what the browser is served — src/ and
// public/ — plus e2e/, whose acceptance script defaulted to the same two
// logins until this change. It reads comments too, because a password in a
// comment is still a password in the repository. It cannot see a credential
// assembled at runtime.

const FRONTEND = join(__dirname, "..");
const ROOTS = ["src", "public", "e2e"].map((d) => join(FRONTEND, d));
const SELF = "noHardcodedCredentials.test.ts";

const SCANNED = /\.(ts|tsx|js|jsx|mjs|html|css|json|py)$/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return SCANNED.test(name) && name !== SELF ? [p] : [];
  });
}

// Each rule is the shape of a credential that was actually in this tree.
const RULES: { name: string; re: RegExp }[] = [
  // The seeded passwords: Agent@123, Manager@123 — a capitalised word, "@",
  // digits. Nothing legitimate in a UI has that shape.
  { name: "seeded password", re: /\b[A-Z][a-z]+@\d{3,}\b/ },
  // Seeded logins: agent002@…, manager1@… — a role word, digits, "@".
  { name: "seeded login", re: /\b(?:agent|manager|admin)\d+@[a-z0-9.-]+\b/i },
  // A literal assigned to anything called password: `MANAGER_PASSWORD = "…"`,
  // `{ password: "…" }`. A type annotation (`password: string`) has no quote.
  { name: "password literal", re: /password\s*[:=]\s*["'`][^"'`\s]+["'`]/i },
  // A literal typed into the form or passed straight to the login call.
  { name: "literal into setPassword", re: /setPassword\(\s*["'`][^"'`]+["'`]/ },
  // No \b: the removed bridge called `apiLogin(`, where "login" has no word
  // boundary in front of it — a \b version passed this file's own self-test
  // on `login(` and missed the one call that actually existed.
  { name: "literal login call", re: /login\(\s*["'`][^"'`]+["'`]\s*,\s*["'`]/i },
  // The auto-login bridge itself.
  { name: "manager-bridge route", re: /["'`]\/manager-bridge\b/ },
];

// Plus the served entry page and any env file at the frontend root (the audit
// of 4dcd9dc: a credential in .env.local or index.html is just as served).
function rootFiles(): string[] {
  return readdirSync(FRONTEND)
    .filter((n) => n === "index.html" || n.startsWith(".env"))
    .map((n) => join(FRONTEND, n))
    .filter((p) => statSync(p).isFile());
}

function scan(): string[] {
  const hits: string[] = [];
  for (const root of ROOTS) {
    for (const f of [...files(root), ...(root === ROOTS[0] ? rootFiles() : [])]) {
      const rel = relative(FRONTEND, f).split(sep).join("/");
      readFileSync(f, "utf8")
        .split("\n")
        .forEach((line, i) => {
          for (const { name, re } of RULES) {
            if (re.test(line)) hits.push(`${rel}:${i + 1} ${name}`);
          }
        });
    }
  }
  return hits;
}

describe("no hardcoded credentials in what the browser is served", () => {
  it("finds none in src/, public/, e2e/, index.html or .env*", () => {
    expect(scan()).toEqual([]);
  });

  // The rules must bite on the lines they were written against, or an empty
  // scan above means nothing. Each is the removed code, verbatim.
  it.each([
    ['    setPassword(type === "agent" ? "Agent@123" : "Manager@123");', "seeded password"],
    ['    setEmail(type === "agent" ? "agent002@tiqcollect.in" : "manager1@tiqcollect.in");', "seeded login"],
    ['const MANAGER_PASSWORD = "Manager@123";', "password literal"],
    ['    setPassword("hunter2");', "literal into setPassword"],
    ['    apiLogin("someone@bank.in", "hunter2", deviceId)', "literal login call"],
    ['            <Route path="/manager-bridge" element={<ManagerBridgePage />} />', "manager-bridge route"],
  ])("rule catches %s", (line, rule) => {
    const re = RULES.find((r) => r.name === rule)!.re;
    expect(re.test(line)).toBe(true);
  });

  it.each([
    'export async function login(email: string, password: string, deviceId: string) {',
    '                      type={showPassword ? "text" : "password"}',
    '                      aria-label={showPassword ? "Hide password" : "Show password"}',
    '                  placeholder="you@tiqcollect.in"',
    '                      autoComplete="current-password"',
    '    const data = await apiLogin(email, password, deviceId);',
  ])("does not flag ordinary form code: %s", (line) => {
    expect(RULES.filter((r) => r.re.test(line)).map((r) => r.name)).toEqual([]);
  });
});
