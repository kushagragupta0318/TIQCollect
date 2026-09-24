// Clickable table rows, operable from a keyboard. CC's drill rows are a bare
// `<tr onClick>`: a mouse can open the drill, a keyboard cannot reach it. A
// recorded deviation (UI spec §9) that changes no pixel of the resting state —
// only a focus ring appears, and only on keyboard focus.
import type { KeyboardEvent } from "react";

/** The focus ring a keyboard-focused row shows (the row keeps CC's classes otherwise). */
export const ROW_FOCUS_CLASS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/30";

export interface RowActivationProps {
  tabIndex: 0;
  onClick: () => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
  "aria-label"?: string;
}

/**
 * Props that make a row focusable and open it on Enter or Space — the keys a
 * button answers to. A key pressed on something inside the row (a link, a
 * button) is left to that element.
 */
export function rowActivation(onActivate: () => void, label?: string): RowActivationProps {
  return {
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
