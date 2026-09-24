import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import { IS_SIMULATED_GEO, geoAvailable, isSimFix, toPosition } from "./deviceLocation";

describe("simulator fixes", () => {
  it("accepts a plausible fix and refuses junk", () => {
    expect(isSimFix({ lat: 28.45, lon: 77.07 })).toBe(true);
    expect(isSimFix({ lat: 91, lon: 77 })).toBe(false);
    expect(isSimFix({ lat: "28", lon: 77 })).toBe(false);
    expect(isSimFix({ lat: Number.NaN, lon: 77 })).toBe(false);
    expect(isSimFix(null)).toBe(false);
  });

  it("builds the GeolocationPosition shape the pages read", () => {
    const p = toPosition({ lat: 28.45, lon: 77.07 }, 1_000);
    expect(p.coords.latitude).toBe(28.45);
    expect(p.coords.longitude).toBe(77.07);
    // RecordVisitPage reads accuracy and altitude with `?? null`; a default
    // accuracy keeps the "GPS ±Xm" line honest-looking rather than NaN.
    expect(p.coords.accuracy).toBe(8);
    expect(p.coords.altitude).toBeNull();
    expect(p.timestamp).toBe(1_000);
  });

  it("is not simulated outside a framed session slot", () => {
    // Vitest runs without a window: the real-device path, which must stay the
    // default for every ordinary page load.
    expect(IS_SIMULATED_GEO).toBe(false);
    expect(geoAvailable()).toBe(false);
  });
});

// Tripwire, not proof: a textual scan. It exists because the simulator only
// works if EVERY position the agent app reads comes through deviceLocation —
// one direct call re-opens the gap silently (the phone frame shows a laptop's
// Wi-Fi location in exactly one screen).
describe("one door to the sensor", () => {
  const SRC = join(__dirname, "..");
  const ALLOWED = join("lib", "deviceLocation.ts");

  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) return files(p);
      return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
    });
  }

  it("has no navigator.geolocation call outside lib/deviceLocation.ts", () => {
    const offenders: string[] = [];
    for (const f of files(SRC)) {
      const rel = relative(SRC, f);
      if (rel === ALLOWED || rel.split(sep).join("/") === "lib/deviceLocation.ts") continue;
      readFileSync(f, "utf8").split("\n").forEach((line, i) => {
        const code = line.trim();
        if (code.startsWith("//") || code.startsWith("*")) return; // comments may name it
        if (/navigator\.geolocation|["']geolocation["']\s+in\s+navigator/.test(code)) {
          offenders.push(`${rel}:${i + 1}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
