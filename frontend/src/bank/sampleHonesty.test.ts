// Tripwires, not proof: textual checks that keep the bank tree from claiming
// data it does not have. The gallery shows an invented book; production must
// not serve it, nothing may call it live, and every composite that can show
// it must be able to say so.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const BANK = __dirname;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

// Code only: strip // line comments and /* */ blocks, which may describe what CC does.
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("no live claims the bank tree cannot back", () => {
  it("no rendered string says 'Live System', 'Derived live' or 'live book'", () => {
    const offenders: string[] = [];
    for (const f of sources(BANK)) {
      code(readFileSync(f, "utf8"))
        .split("\n")
        .forEach((line, i) => {
          if (/Live System|Derived live|[Ll]ive book/.test(line)) offenders.push(`${relative(BANK, f)}:${i + 1}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("the gallery is gated like the simulator, and its chunk is imported only inside the flag's ternary", () => {
    const flag = code(readFileSync(join(BANK, "galleryFlag.ts"), "utf8"));
    expect(flag).toMatch(/import\.meta\.env\.DEV \|\| import\.meta\.env\.VITE_ENABLE_BANK_GALLERY === "1"/);
    // Same module as the condition, so a production build can drop the chunk.
    expect(flag).toMatch(/BANK_GALLERY_ENABLED \? lazy\(\(\) => import\("\.\/pages\/BankComponentGalleryPage"\)\) : null/);
    const importers = sources(BANK).filter((f) => /import\(["'][^"']*BankComponentGalleryPage["']\)/.test(code(readFileSync(f, "utf8"))));
    expect(importers.map((f) => relative(BANK, f))).toEqual(["galleryFlag.ts"]);
    // BankApp registers the route only when the lazy page exists.
    expect(code(readFileSync(join(BANK, "BankApp.tsx"), "utf8"))).toMatch(/\{BankComponentGalleryPage && \(/);
  });

  it("every link to the gallery is behind the flag", () => {
    for (const f of sources(BANK)) {
      const text = code(readFileSync(f, "utf8"));
      if (!/BANK_GALLERY_PATH|\/bank\/_gallery/.test(text) || /galleryFlag\.ts$/.test(f)) continue;
      expect(text, relative(BANK, f)).toMatch(/BANK_GALLERY_ENABLED/);
    }
  });

  it("the gallery marks every composite that shows sample figures", () => {
    const gallery = code(readFileSync(join(BANK, "pages", "BankComponentGalleryPage.tsx"), "utf8"));
    for (const tag of ["PulseKpiFlow", "DecisionAlerts", "DrillPanel", "WorkspaceModal"]) {
      const opening = gallery.match(new RegExp(`<${tag}\\b[\\s\\S]*?>`))?.[0] ?? "";
      expect(opening, tag).toMatch(/\bsampleData\b/);
    }
  });
});
