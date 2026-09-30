import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

// N1 (docs/business/PRIORITIES.md). The check-in screen told every agent
// "Location: Mumbai, Maharashtra" and "Liveness check: Passed", and showed a
// stand-in picture as a captured selfie when the camera was refused. None of it
// was computed. A bank auditor reads the screens, so a claim the code does not
// back is the finding that ends the pilot.
//
// Tripwire, not proof: a textual scan of src/. It cannot see a claim assembled
// at runtime, and it only knows the shapes that were actually in this tree.

const SRC = __dirname;
const SELF = "noFalseEvidenceClaims.test.ts";
const SCANNED = /\.(ts|tsx)$/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return SCANNED.test(name) && name !== SELF ? [p] : [];
  });
}

// `paths` limits a rule to files it has been decided for (matched against src/<path>).
const RULES: { name: string; re: RegExp; paths?: RegExp }[] = [
  // The app has no liveness detection, so nothing may say it checked or passed one.
  { name: "liveness claim", re: /liveness\s+(?:check|detection|passed)/i },
  // A fixed place printed as where the agent is: "Location: <City>, <State>".
  { name: "fixed location", re: /Location:\s*[A-Z][a-z]+,\s*[A-Z][a-z]+/ },
  // A captured-evidence state set from a literal picture instead of the camera.
  { name: "stand-in capture", re: /setCaptured\w*\(\s*["'`]data:image/ },
  // The check-in takes no selfie and keeps none (deliberately, until B07 brings attendance with
  // a retention rule and a consent basis). Nothing may say it does. "Agent Selfie", the photo
  // taken at the premises, is a different thing and is saved.
  { name: "selfie check-in claim",
    re: /selfie[\s-]+(?:check-?in|attendance)|selfie (?:and|\+) gps|check[\s-]?in (?:with|by)(?: a)? selfie|selfie for attendance/i },
  // The receipt and the ID card are handed to a borrower; nobody can substantiate a regulatory
  // claim on them. Only components/: the landing and login pages say it too, which is marketing
  // copy and the owner's call (asked of the coordinator), so they are not in this rule yet.
  { name: "RBI compliance claim", re: /RBI[\s-]+compliant/i, paths: /^src\/components\// },
  // A static UPI QR has no callback: no screen may show the app waiting for, or securing, a payment.
  { name: "payment process claim", re: /Waiting for payment|>\s*Secure Pay\s*</ },
];

function scan(): string[] {
  const hits: string[] = [];
  for (const f of files(SRC)) {
    const rel = relative(SRC, f).split(sep).join("/");
    readFileSync(f, "utf8")
      .split("\n")
      .forEach((line, i) => {
        for (const { name, re, paths } of RULES) {
          if ((!paths || paths.test(`src/${rel}`)) && re.test(line)) hits.push(`src/${rel}:${i + 1} ${name}`);
        }
      });
  }
  return hits;
}

describe("no claim about evidence that the code does not back", () => {
  it("finds none in src/", () => {
    expect(scan()).toEqual([]);
  });

  // Each rule must bite on the line it was written against, or an empty scan
  // above means nothing. These are the removed lines, verbatim.
  it.each([
    ['<p className="flex items-center gap-1.5"><ShieldCheck /> Liveness check: Passed</p>', "liveness claim"],
    ['  "Selfie attendance check-in with liveness detection",', "liveness claim"],
    ['<p className="flex items-center gap-1.5"><MapPin /> Location: Mumbai, Maharashtra</p>', "fixed location"],
    ['        setCapturedSelfie("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAw");', "stand-in capture"],
    ['              <h3 className="font-semibold text-slate-900">Selfie Check-In</h3>', "selfie check-in claim"],
    ['<p className="text-xs">Align your face with the circle and take selfie for attendance</p>', "selfie check-in claim"],
    ['  { time: "9:30 AM", event: "Agents check in with selfie + GPS stamp — duty status goes live" },', "selfie check-in claim"],
    ['    desc: "Agents check in with a selfie, receive their optimised beat map, navigate case-to-case" },', "selfie check-in claim"],
    ['  "Selfie and GPS check-in at the start of the day",', "selfie check-in claim"],
    ['desc: "ID card number on every visit record. Selfie check-in required before field work." },', "selfie check-in claim"],
    ['TIQCollect · RBI Compliant · {verified ? "Borrower-verified (OTP)" : "Awaiting borrower OTP"}', "RBI compliance claim"],
    ['\\n─────────────────\\nTIQCollect · RBI Compliant`;', "RBI compliance claim"],
    ['<p className="text-xs font-medium">Waiting for payment…</p>', "payment process claim"],
    ['<p className="text-white text-xs font-semibold">Secure Pay</p>', "payment process claim"],
  ])("rule catches %s", (line, rule) => {
    const re = RULES.find((r) => r.name === rule)!.re;
    expect(re.test(line)).toBe(true);
  });

  it.each([
    'setCapturedSelfie(canvasRef.current.toDataURL("image/jpeg"));',
    '<p>GPS fix: {describeFix(checkInFix)}</p>',
    'const loc = `Location: ${city}`;',
    '"GPS check-in at the start of the day",',
    'AGENT_SELFIE: "Agent Selfie", BORROWER: "Borrower Photo", VEHICLE_ASSET: "Vehicle / Asset",',
    'TIQCollect · {verified ? "Borrower-verified (OTP)" : "Awaiting borrower OTP"}',
    '"Contact hour indicator (RBI 8AM–7PM enforcement)",',
    'When the customer has paid, enter the transaction ID from their confirmation below.',
  ])("does not flag ordinary code: %s", (line) => {
    expect(RULES.filter((r) => r.re.test(line)).map((r) => r.name)).toEqual([]);
  });
});
