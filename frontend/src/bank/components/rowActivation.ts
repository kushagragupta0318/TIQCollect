// Clickable table rows, operable from a keyboard. CC's drill rows are a bare
// `<tr onClick>`: a mouse can open the drill, a keyboard cannot reach it. A
// recorded deviation (UI spec §9) that changes no pixel of the resting state —
// only a focus ring appears, and only on keyboard focus.
//
// 2026-09-24 — Accessibility re-audit: this returned tabIndex and the
// Enter/Space handlers but no `role`, so a screen reader announced the row as
// plain, inert text — focusable and clickable, but nameless as a control.
// `role` now travels with the rest of the props, defaulting to "button" (both
// current callers, DataTable and HeatGrid, open a drill panel in place rather
// than navigate); a caller whose row navigates instead passes `"link"`. Rows
// that are not clickable are unaffected — the caller never calls this
// function for them (`onRowClick ? rowActivation(...) : {}`), so they get no
// role and no tabIndex, exactly as before.
import type { KeyboardEvent } from "react";

/** The focus ring a keyboard-focused row shows (the row keeps CC's classes otherwise). */
export const ROW_FOCUS_CLASS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/30";

/** "button" opens something in place (a drill panel); "link" navigates away. */
export type RowActivationRole = "button" | "link";

export interface RowActivationProps {
  role: RowActivationRole;
  tabIndex: 0;
  onClick: () => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
  "aria-label"?: string;
}

/**
 * Props that make a row focusable, name its role for assistive tech, and open
 * it on Enter or Space — the keys a button answers to. A key pressed on
 * something inside the row (a link, a button) is left to that element.
 *
 * `role` defaults to `"button"`; pass `"link"` for a row whose activation
 * navigates rather than opening something in place.
 */
export function rowActivation(onActivate: () => void, label?: string, role: RowActivationRole = "button"): RowActivationProps {
  return {
    role,
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault(); // Space would otherwise scroll the page
        onActivate();
      }
    },
    ...(label ? { "aria-label": label } : {}),
  };
}
