// Compiles src/bank/bank.css exactly as Vite does (PostCSS + Tailwind, its own
// `@config`) and checks the one property the bank portal's CSS promises: it
// cannot style anything outside `.bank-root` (spec §7.2). A new rule written
// without the prefix, a keyframe without `bank-`, or a `:root`/`body` rule
// fails here instead of silently restyling the agency and agent views.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import postcss, { type AtRule, type Root } from "postcss";
import tailwindcss from "tailwindcss";
import { beforeAll, describe, expect, it } from "vitest";

const CSS_PATH = join(__dirname, "bank.css");

// The only unprefixed rules Tailwind emits with preflight off: the `--tw-*`
// custom-property defaults. They are byte-identical to TIQCollect's own.
const TAILWIND_DEFAULTS = new Set(["*", "::before", "::after", "::backdrop"]);
// Tailwind's built-in keyframes, emitted when animate-spin/ping/pulse are
// used — the same definitions TIQCollect's build already emits.
const TAILWIND_KEYFRAMES = new Set(["spin", "ping", "pulse", "bounce"]);

let root: Root;

beforeAll(async () => {
  const result = await postcss([tailwindcss()]).process(readFileSync(CSS_PATH, "utf8"), { from: CSS_PATH });
  root = result.root;
}, 60_000);

describe("bank.css is scoped to .bank-root", () => {
  it("every rule outside a keyframe block names .bank-root", () => {
    const leaks: string[] = [];
    root.walkRules((rule) => {
      const parent = rule.parent;
      if (parent?.type === "atrule" && /keyframes$/.test((parent as AtRule).name)) return;
      for (const selector of rule.selectors) {
        if (selector.includes(".bank-root") || TAILWIND_DEFAULTS.has(selector)) continue;
        leaks.push(selector);
      }
    });
    expect(leaks).toEqual([]);
  });

  it("the tw-* defaults declare only custom properties", () => {
    root.walkRules((rule) => {
      if (!rule.selectors.every((s) => TAILWIND_DEFAULTS.has(s))) return;
      rule.walkDecls((decl) => expect(decl.prop.startsWith("--tw-")).toBe(true));
    });
  });

  it("every keyframe is bank- prefixed or one of Tailwind's own", () => {
    const names: string[] = [];
    root.walkAtRules(/keyframes$/, (at) => {
      names.push(at.params);
    });
    expect(names.length).toBeGreaterThan(0);
    expect(names.filter((name) => !name.startsWith("bank-") && !TAILWIND_KEYFRAMES.has(name))).toEqual([]);
  });

  it("declares CC's effective tokens on .bank-root, never on :root", () => {
    let rootRules = 0;
    const primaries: string[] = [];
    root.walkRules((rule) => {
      if (rule.selectors.some((s) => /(^|\s|,):root\b|^html\b|^body\b/.test(s))) rootRules++;
      if (rule.selector === ".bank-root") rule.walkDecls("--primary", (d) => void primaries.push(d.value));
    });
    expect(rootRules).toBe(0);
    // Declared (Apple Glass) first, effective (Soft Card) last — the last one wins.
    expect(primaries.at(-1)).toBe("243 75% 59%");
  });

  it("utilities come out prefixed, with CC's sizes rather than ours", () => {
    const css = root.toString();
    expect(css).toMatch(/\.bank-root \.text-2xl\s*\{\s*font-size:\s*2rem;\s*line-height:\s*2\.5rem/);
    expect(css).toMatch(/\.bank-root \.rounded-md\s*\{\s*border-radius:\s*0\.75rem/);
    // The Soft Card override that makes rounded-card render 16px, not 22px.
    expect(css).toMatch(/\.bank-root \.rounded-card\s*\{\s*border-radius:\s*16px !important/);
  });
});
