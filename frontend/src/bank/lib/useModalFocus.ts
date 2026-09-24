// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file. The one focus behaviour for every bank overlay:
//   Dialog, DrillPanel and WorkspaceModal.
//
//   WHY. CC's three overlays portal a modal surface over the page and do
//   nothing else: focus stays on the button behind the scrim, Tab walks the
//   hidden page, only two of the three close on Escape, and the workspace does
//   not lock the page scroll. That is invisible in a screenshot and unusable
//   from a keyboard or a screen reader. A recorded deviation (UI spec §9): it
//   changes no pixel of the resting state.
//
//   On open: remember the element that had focus, lock the body scroll, move
//   focus to the first focusable element inside the surface (or the surface
//   itself). While open: Tab and Shift+Tab wrap inside it; Escape calls
//   onClose. On close: restore the scroll and return focus to where it was.
//   Overlays stack — a dialog opened over the workspace owns Tab and Escape
//   until it closes — so only the topmost one reacts.
//
// 2026-09-24 (later) — Accessibility re-audit: Tab did not trap on a surface
//   with zero focusable descendants (e.g. a drill panel still in its loading
//   state). `nextTrappedFocus([], ...)` already, correctly, returns null —
//   there is nowhere to send focus — but the caller read that null as "let
//   the browser handle it" and never called `preventDefault()`, so Tab walked
//   out to the hidden page behind the overlay. The keydown handler now checks
//   `items.length === 0` itself and blocks Tab outright in that case, rather
//   than asking `nextTrappedFocus` to say both "nowhere to go" and "don't
//   leave" with the same null.
// ─────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Focusable, visible descendants in DOM order. */
export function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("inert") && el.getClientRects().length > 0,
  );
}

/** The element Tab should move to, or null to let the browser handle it. */
export function nextTrappedFocus(items: HTMLElement[], active: Element | null, backwards: boolean): HTMLElement | null {
  if (items.length === 0) return null;
  const first = items[0];
  const last = items[items.length - 1];
  const inside = active != null && items.includes(active as HTMLElement);
  if (backwards) return !inside || active === first ? last : null;
  return !inside || active === last ? first : null;
}

// Open overlays, innermost last. Only the top one handles Tab and Escape.
const stack: symbol[] = [];

export interface ModalFocusOptions {
  /** Lock the page scroll while open (default true). */
  lockScroll?: boolean;
}

export function useModalFocus(
  ref: RefObject<HTMLElement | null>,
  onClose: (() => void) | undefined,
  { lockScroll = true }: ModalFocusOptions = {},
): void {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const surface = ref.current;
    if (!surface) return;
    const id = Symbol("bank-modal");
    stack.push(id);
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const previousOverflow = document.body.style.overflow;
    if (lockScroll) document.body.style.overflow = "hidden";

    const [first] = focusableWithin(surface);
    if (first) first.focus();
    else {
      if (!surface.hasAttribute("tabindex")) surface.setAttribute("tabindex", "-1");
      surface.focus();
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== id) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onCloseRef.current?.();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusableWithin(surface);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const target = nextTrappedFocus(items, document.activeElement, e.shiftKey);
      if (target) {
        e.preventDefault();
        target.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const at = stack.indexOf(id);
      if (at >= 0) stack.splice(at, 1);
      if (lockScroll) document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus();
    };
  }, [ref, lockScroll]);
}
