// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-18 — NEW. The manager's charts used to be frozen from page load
//   until the next navigation: every query had `staleTime: 0` and
//   `refetchOnWindowFocus: false`, nothing polled except the overview's two
//   headline queries, the Agents page and the Live Map, and nothing is pushed
//   (no WebSocket, no SSE — CLAUDE.md, feature 12). Measured: a visit recorded
//   through the agent API while the overview was open left the Field Activity
//   funnel at 174 for 45 s and it read 175 only after a reload.
//
//   ONE definition of "live" for every manager chart, so the cadence is a
//   number in one place:
//     - poll every LIVE_REFRESH_MS while the tab is visible (never in the
//       background — a manager with the tab parked overnight should not hit
//       the API 1,440 times);
//     - refetch when the tab regains focus or the network comes back, which
//       is when a manager actually looks.
//   Spread `...LIVE` AFTER a page's own options so it overrides the old
//   `refetchOnWindowFocus: false`. `useLiveRefresh` gives the same behaviour
//   to a page that still loads through useEffect + useState.
// ─────────────────────────────────────────────────────────────────────────────

import { useEffect, useRef } from "react";

/** How often a visible manager page re-reads its charts. */
export const LIVE_REFRESH_MS = 60_000;

export const LIVE = {
  refetchInterval: LIVE_REFRESH_MS,
  refetchIntervalInBackground: false,
  refetchOnWindowFocus: true,
  refetchOnReconnect: true,
} as const;

/**
 * Call `refresh` every LIVE_REFRESH_MS while the document is visible, and once
 * whenever it becomes visible again. `refresh` is read through a ref so the
 * caller can pass a fresh closure on every render without re-arming the timer.
 */
export function useLiveRefresh(refresh: () => void, enabled = true): void {
  const fn = useRef(refresh);
  useEffect(() => { fn.current = refresh; });
  useEffect(() => {
    if (!enabled) return;
    const tick = () => { if (document.visibilityState === "visible") fn.current(); };
    const id = setInterval(tick, LIVE_REFRESH_MS);
    const onVisible = () => { if (document.visibilityState === "visible") fn.current(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
    };
  }, [enabled]);
}
