import { useEffect, type RefObject } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Everything an open modal owes the user, in one hook:
 *
 *  1. Body scroll lock — the page behind must not scroll. Uses the
 *     `position: fixed` technique rather than `overflow: hidden`, because
 *     iOS Safari ignores the latter on `body` and happily scrolls the page
 *     under the sheet. Scroll position is captured and restored exactly.
 *  2. Scrollbar compensation — going `position: fixed` removes the desktop
 *     scrollbar, which shifts the whole layout sideways. Pad it back.
 *  3. Focus trap — Tab and Shift+Tab cycle inside the modal instead of
 *     escaping to the page behind it.
 *  4. Focus restore — on close, focus returns to whatever opened the modal.
 *  5. Escape to close.
 *
 * Pass `onClose: null` to keep the trap and the lock but handle Escape
 * yourself (CaseDetailModal already has its own Escape listener).
 */
export function useModalA11y(
  active: boolean,
  containerRef: RefObject<HTMLElement | null>,
  onClose?: (() => void) | null,
) {
  // ── Scroll lock ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!active) return;
    const { body, documentElement } = document;
    const scrollY = window.scrollY;
    const scrollbar = window.innerWidth - documentElement.clientWidth;

    const prev = {
      position: body.style.position,
      top: body.style.top,
      width: body.style.width,
      paddingRight: body.style.paddingRight,
      overscroll: documentElement.style.overscrollBehavior,
    };

    body.style.position = "fixed";
    body.style.top = `-${scrollY}px`;
    body.style.width = "100%";
    if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`;
    documentElement.style.overscrollBehavior = "contain";

    return () => {
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      body.style.paddingRight = prev.paddingRight;
      documentElement.style.overscrollBehavior = prev.overscroll;
      window.scrollTo(0, scrollY);
    };
  }, [active]);

  // ── Focus trap, focus restore, Escape ──────────────────────────────────────
  useEffect(() => {
    if (!active) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    // Move focus into the modal so the very first Tab lands inside it.
    const focusFirst = () => {
      const node = containerRef.current;
      if (!node) return;
      const first = node.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? node).focus({ preventScroll: true });
    };
    const raf = requestAnimationFrame(focusFirst);

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && onClose) {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;

      const node = containerRef.current;
      if (!node) return;
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }

      const first = items[0];
      const last = items[items.length - 1];
      const activeEl = document.activeElement as HTMLElement | null;

      // Wrap at both ends, and pull focus back in if it has escaped entirely.
      if (e.shiftKey && (activeEl === first || !node.contains(activeEl))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (activeEl === last || !node.contains(activeEl))) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown, true);
      previouslyFocused?.focus?.({ preventScroll: true });
    };
  }, [active, containerRef, onClose]);
}
