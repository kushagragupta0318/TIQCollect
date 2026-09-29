/**
 * The server's clock, as seen from this phone (I02).
 *
 * A capture time is judged by the server (services/capture_time.py), which
 * refuses one more than 2 minutes in its future. A phone whose clock runs fast
 * would then be refused even on a live submit. So every API response's `Date`
 * header records the offset between the two clocks, and a capture is stamped
 * in server time. Offline, the last offset seen is used: clocks drift slowly.
 *
 * Bounded (Opus audit of 1a85ffd): the offset is clamped to ±5 min, and a
 * response that took over 5 s is ignored (its Date is too stale to measure
 * with), so a hostile or slow hop cannot push captures far out of true.
 */
import type { AxiosInstance, InternalAxiosRequestConfig } from "axios";

export const MAX_OFFSET_MS = 300_000;
export const MAX_ROUND_TRIP_MS = 5_000;
let offsetMs = 0;

/** Pure: the offset a response's Date header implies, or null if unusable. */
export function offsetFrom(dateHeader: unknown, localNowMs: number): number | null {
  if (typeof dateHeader !== "string") return null;
  const server = Date.parse(dateHeader);
  return Number.isFinite(server) ? server - localNowMs : null;
}

export function noteServerDate(dateHeader: unknown, localNowMs = Date.now(), roundTripMs = 0): void {
  if (roundTripMs > MAX_ROUND_TRIP_MS) return;
  const o = offsetFrom(dateHeader, localNowMs);
  if (o !== null) offsetMs = Math.max(-MAX_OFFSET_MS, Math.min(MAX_OFFSET_MS, o));
}

/** Now, on the server's clock (to the second: the header's resolution). */
export function serverNow(localNowMs = Date.now()): Date {
  return new Date(localNowMs + offsetMs);
}

type Timed = InternalAxiosRequestConfig & { tiqSentAt?: number };

export function installServerClock(instance: AxiosInstance): void {
  instance.interceptors.request.use((config: Timed) => {
    config.tiqSentAt = Date.now();
    return config;
  });
  instance.interceptors.response.use((res) => {
    const now = Date.now();
    const sent = (res.config as Timed).tiqSentAt;
    noteServerDate(res.headers?.["date"], now, sent === undefined ? Infinity : now - sent);
    return res;
  });
}
