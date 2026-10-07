import { useCallback, useSyncExternalStore } from "react";

/**
 * Subscribe to a CSS media query from React.
 *
 * Use this ONLY when a breakpoint changes *behaviour* — rendering a bottom
 * sheet instead of a centred modal, or handing Recharts a different height.
 * Never use it for styling: Tailwind's `sm:`/`lg:` prefixes do that with no
 * re-render and no first-paint flash.
 *
 * Built on useSyncExternalStore rather than useState + useEffect. matchMedia
 * is exactly the "external store" that API exists for, so there is no effect
 * to mistime, no cascading render, and the value is correct on first paint.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onStoreChange);
      return () => mql.removeEventListener("change", onStoreChange);
    },
    [query],
  );

  const getSnapshot = useCallback(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  }, [query]);

  // No window during SSR/prerender — assume the desktop layout, which is what
  // the markup already describes before any breakpoint class applies.
  const getServerSnapshot = useCallback(() => false, []);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

// Breakpoints mirror tailwind.config.js so there is one source of truth for the
// ladder. Keep in sync if the config changes.
export const useIsMobile  = () => useMediaQuery("(max-width: 639px)");   // below sm
export const useIsBelowLg = () => useMediaQuery("(max-width: 1023px)");  // below lg — nav + tables switch here

// Recharts animates in JS (its own Animate component), not CSS — the
// motion-safe:/motion-reduce: Tailwind variants and bank.css's global
// `@media (prefers-reduced-motion: reduce)` guard (which forces
// animation-duration: 1ms !important) never reach it, so a chart needs this
// read directly and passed to isAnimationActive. A genuine behaviour change
// (animate or don't), which is exactly what this hook is for.
export const usePrefersReducedMotion = () => useMediaQuery("(prefers-reduced-motion: reduce)");
