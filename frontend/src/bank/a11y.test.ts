// Accessibility of the bank shell and overlays. The focus trap and the row
// activation are tested as logic; the wiring is held by textual tripwires
// (there is no DOM test library in this repo — see lib/roles.test.ts).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { KeyboardEvent } from "react";
import { describe, expect, it, vi } from "vitest";
import { nextTrappedFocus } from "./lib/useModalFocus";
import { rowActivation } from "./components/rowActivation";

const el = (name: string) => ({ name }) as unknown as HTMLElement;
const [a, b, c] = [el("a"), el("b"), el("c")];

describe("focus trap: where Tab goes", () => {
  it("wraps from the last element to the first, and back on Shift+Tab", () => {
    expect(nextTrappedFocus([a, b, c], c, false)).toBe(a);
    expect(nextTrappedFocus([a, b, c], a, true)).toBe(c);
  });

  it("leaves Tab to the browser in the middle of the surface", () => {
    expect(nextTrappedFocus([a, b, c], a, false)).toBeNull();
    expect(nextTrappedFocus([a, b, c], c, true)).toBeNull();
  });

  it("pulls focus back in when it has escaped the surface", () => {
    const outside = el("page");
    expect(nextTrappedFocus([a, b, c], outside, false)).toBe(a);
    expect(nextTrappedFocus([a, b, c], outside, true)).toBe(c);
    expect(nextTrappedFocus([a, b, c], null, false)).toBe(a);
  });

  it("does nothing on a surface with no focusable element", () => {
    expect(nextTrappedFocus([], a, false)).toBeNull();
  });
});

describe("clickable rows answer the keyboard like a button", () => {
  const key = (k: string, onTarget = true) => {
    const target = {};
    return {
      key: k,
      target,
      currentTarget: onTarget ? target : {},
      preventDefault: vi.fn(),
    } as unknown as KeyboardEvent<HTMLElement>;
  };

  it("is focusable and opens on Enter and Space, without scrolling on Space", () => {
    const open = vi.fn();
    const props = rowActivation(open, "Open 31-60 DPD");
    expect(props.tabIndex).toBe(0);
    expect(props["aria-label"]).toBe("Open 31-60 DPD");
    const enter = key("Enter");
    props.onKeyDown(enter);
    const space = key(" ");
    props.onKeyDown(space);
    expect(open).toHaveBeenCalledTimes(2);
    expect(space.preventDefault).toHaveBeenCalled();
  });

  it("ignores other keys and keys aimed at something inside the row", () => {
    const open = vi.fn();
    const props = rowActivation(open);
    props.onKeyDown(key("ArrowDown"));
    props.onKeyDown(key("Enter", false));
    expect(open).not.toHaveBeenCalled();
    props.onClick();
    expect(open).toHaveBeenCalledOnce();
  });
});

describe("wiring (tripwires)", () => {
  const read = (rel: string) => readFileSync(join(__dirname, rel), "utf8");

  it.each([
    ["ui/dialog.tsx", "DialogContent"],
    ["components/DrillPanel.tsx", "DrillPanel"],
    ["components/WorkspaceModal.tsx", "WorkspaceModal"],
  ])("%s: %s uses the one focus hook, is labelled by its title, and has no Escape listener of its own", (file) => {
    const src = read(file);
    expect(src).toMatch(/useModalFocus\(/);
    expect(src).toMatch(/aria-labelledby=\{/);
    expect(src).not.toMatch(/addEventListener\(\s*["']keydown/);
  });

  it("the sidebar is a landmark, marks the current page, and its rail flyouts are keyboard disclosures", () => {
    const src = read("layout/BankSidebar.tsx");
    expect(src).toMatch(/role="navigation"/);
    expect(src).toMatch(/aria-current=\{active \? "page" : undefined\}/);
    expect(src).toMatch(/aria-expanded=\{open\}/);
    expect(src).toMatch(/aria-controls=\{flyoutId\}/);
    // Closed, the flyout is invisible — its links leave the tab order.
    expect(src).toMatch(/pointer-events-none invisible/);
  });

  it("clickable table rows go through rowActivation", () => {
    for (const file of ["components/DataTable.tsx", "components/portfolioVisuals.tsx"]) {
      const src = read(file);
      expect(src, file).toMatch(/rowActivation\(/);
      expect(src, file).not.toMatch(/<tr[^>]*\bonClick=/);
    }
  });
});
