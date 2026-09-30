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

const RULES: { name: string; re: RegExp }[] = [
  // The app has no liveness detection, so nothing may say it checked or passed one.
  { name: "liveness claim", re: /liveness\s+(?:check|detection|passed)/i },
  // A fixed place printed as where the agent is: "Location: <City>, <State>".
  { name: "fixed location", re: /Location:\s*[A-Z][a-z]+,\s*[A-Z][a-z]+/ },
  // A captured-evidence state set from a literal picture instead of the camera.
  { name: "stand-in capture", re: /setCaptured\w*\(\s*["'`]data:image/ },
];

function scan(): string[] {
  const hits: string[] = [];
  for (const f of files(SRC)) {
    const rel = relative(SRC, f).split(sep).join("/");
    readFileSync(f, "utf8")
      .split("\n")
      .forEach((line, i) => {
        for (const { name, re } of RULES) {
          if (re.test(line)) hits.push(`src/${rel}:${i + 1} ${name}`);
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
  ])("rule catches %s", (line, rule) => {
    const re = RULES.find((r) => r.name === rule)!.re;
    expect(re.test(line)).toBe(true);
  });

  it.each([
    'setCapturedSelfie(canvasRef.current.toDataURL("image/jpeg"));',
    '<p>GPS fix: {describeFix(checkInFix)}</p>',
    'const loc = `Location: ${city}`;',
    '"Selfie and GPS check-in at the start of the day",',
  ])("does not flag ordinary code: %s", (line) => {
    expect(RULES.filter((r) => r.re.test(line)).map((r) => r.name)).toEqual([]);
  });
});
