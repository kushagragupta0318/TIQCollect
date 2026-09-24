import { useEffect, useRef, useSyncExternalStore } from "react";
import api from "@/api/axios";
import { subscribeEvents, type LiveEvent, type StreamStatus } from "@/lib/eventStream";
import { useAuthStore } from "@/store/authStore";

/**
 * Live field events for manager pages — one stream per tab, shared.
 *
 * 2026-09-24 (standalone plan, P0-07). The layout (SOS bell), the live map and
 * the overview all want the same events; three streams would be three Redis
 * subscriptions and three reconnect loops for one manager. Same shape as
 * useLiveLocation: a module-level source, listeners added and removed by
 * components, and a short grace period before tearing down so a route change
 * (one page unmounts a tick before the next mounts) does not reconnect.
 *
 * Polling stays everywhere it was. Events make a page refresh NOW; the poll is
 * what still works if the stream never connects.
 */

type Listener = (e: LiveEvent) => void;

const listeners = new Set<Listener>();
const statusListeners = new Set<() => void>();
let status: StreamStatus = "closed";
let stop: (() => void) | null = null;
let stopTimer: ReturnType<typeof setTimeout> | undefined;

function start() {
  if (stopTimer !== undefined) {
    clearTimeout(stopTimer);
    stopTimer = undefined;
  }
  if (stop) return;
  stop = subscribeEvents({
    getToken: () => useAuthStore.getState().accessToken,
    onEvent: (e) => listeners.forEach((l) => l(e)),
    onStatus: (s) => {
      status = s;
      statusListeners.forEach((f) => f());
    },
    // Refresh through the axios instance so the ONE refresh path (with its
    // queue and token-reuse handling) stays the only one: a 401 on /auth/me
    // makes the interceptor rotate the tokens into the store, and the stream
    // reconnects reading the new one.
    onUnauthorized: async () => {
      try {
        await api.get("/auth/me");
        return true;
      } catch {
        return false;
      }
    },
  });
}

function scheduleStop() {
  if (listeners.size > 0 || stopTimer !== undefined) return;
  stopTimer = setTimeout(() => {
    stopTimer = undefined;
    if (listeners.size === 0 && stop) {
      stop();
      stop = null;
    }
  }, 5_000);
}

/** Call `handler` for every live event while mounted. */
export function useLiveEvents(handler: Listener, enabled = true): void {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => {
    if (!enabled) return;
    const l: Listener = (e) => ref.current(e);
    listeners.add(l);
    start();
    return () => {
      listeners.delete(l);
      scheduleStop();
    };
  }, [enabled]);
}

/** "live" | "polling" | "connecting" | "closed" — for a status dot. */
export function useLiveStatus(): StreamStatus {
  return useSyncExternalStore(
    (cb) => {
      statusListeners.add(cb);
      return () => statusListeners.delete(cb);
    },
    () => status,
  );
}

/** Event types that change what a manager's charts and tables show. */
export const WORK_EVENTS = new Set(["visit.recorded", "payment.submitted", "payment.verified", "ptp.set"]);
