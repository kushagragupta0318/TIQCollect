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

/**
 * A selector that can only match inside the bank tree: it STARTS with the
 * scope — `.bank-root`, `:where(.bank-root)`, or the portal container
 * `.bank-root[data-bank-portal]` — followed by nothing or a DESCENDANT or
 * CHILD step. `includes(".bank-root")` (the first version of this test)
 * would also have passed `.bank-root ~ div` and `.bank-root + *`, which style
 * the bank root's siblings, and `body:has(.bank-root)`, which styles <body>.
 */
const SCOPED = /^(?:\.bank-root|:where\(\.bank-root\))(?:\[data-bank-portal\])?(?:$|\s+(?![~+]))/;

let root: Root;
let tiqDefaults: Map<string, Map<string, string>>;

// `--tw-*` declarations by selector group, from the `*, ::before, ::after`
// and `::backdrop` rules.
function twDefaults(r: Root): Map<string, Map<string, string>> {
  const out = new Map<string, Map<string, string>>();
  r.walkRules((rule) => {
    if (!rule.selectors.every((s) => TAILWIND_DEFAULTS.has(s))) return;
    const key = rule.selectors.join(", ");
    const decls = out.get(key) ?? new Map<string, string>();
    rule.walkDecls((d) => void decls.set(d.prop, d.value));
    out.set(key, decls);
  });
  return out;
}

beforeAll(async () => {
  const result = await postcss([tailwindcss()]).process(readFileSync(CSS_PATH, "utf8"), { from: CSS_PATH });
  root = result.root;
  // The main build's base layer, from TIQCollect's own config — the defaults
  // the bank's copy must not disagree with, since both are global.
  const tiqConfig = join(__dirname, "..", "..", "tailwind.config.js");
  const tiq = await postcss([tailwindcss({ config: tiqConfig })]).process("@tailwind base;", { from: undefined });
  tiqDefaults = twDefaults(tiq.root);
}, 60_000);

describe("bank.css is scoped to .bank-root", () => {
  it("every rule outside a keyframe block starts with the .bank-root scope and only descends from it", () => {
    const leaks: string[] = [];
    root.walkRules((rule) => {
      const parent = rule.parent;
      if (parent?.type === "atrule" && /keyframes$/.test((parent as AtRule).name)) return;
      for (const selector of rule.selectors) {
        if (SCOPED.test(selector.trim()) || TAILWIND_DEFAULTS.has(selector)) continue;
        leaks.push(selector);
      }
    });
    expect(leaks).toEqual([]);
  });

  it("the anchored check rejects the selectors a substring check let through", () => {
    for (const bad of [".bank-root ~ div", ".bank-root + *", ".bank-root~div", "body:has(.bank-root)", "html .bank-root", ".x, .bank-root .y"]) {
      expect(SCOPED.test(bad), bad).toBe(false);
    }
    for (const good of [".bank-root", ".bank-root .x", ".bank-root > .x", ":where(.bank-root) *", ".bank-root[data-bank-portal]", ".bank-root ::-webkit-scrollbar"]) {
      expect(SCOPED.test(good), good).toBe(true);
    }
  });

  it("the tw-* defaults declare only custom properties", () => {
    root.walkRules((rule) => {
      if (!rule.selectors.every((s) => TAILWIND_DEFAULTS.has(s))) return;
      rule.walkDecls((decl) => expect(decl.prop.startsWith("--tw-")).toBe(true));
    });
  });

  it("every tw-* default the bank emits equals the main build's, so loading it changes nothing global", () => {
    const bank = twDefaults(root);
    // Not vacuous: the universal block carries Tailwind's full default set.
    expect(bank.get("*, ::before, ::after")?.size ?? 0).toBeGreaterThan(20);
    const mismatches: string[] = [];
    for (const [group, decls] of bank) {
      const theirs = tiqDefaults.get(group);
      if (!theirs) {
        mismatches.push(`${group}: the main build has no such rule`);
        continue;
      }
      for (const [prop, value] of decls) {
        if (theirs.get(prop) !== value) mismatches.push(`${group} ${prop}: bank "${value}" vs main "${theirs.get(prop)}"`);
      }
    }
    expect(mismatches).toEqual([]);
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
