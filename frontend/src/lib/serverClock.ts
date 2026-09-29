/**
 * The server's clock, as seen from this phone (I02).
 *
 * A capture time is judged by the server (services/capture_time.py), which
 * refuses one more than 2 minutes in its future. A phone whose clock runs fast
 * would then be refused even on a live submit. So every API response's `Date`
 * header records the offset between the two clocks, and a capture is stamped
 * in server time. Offline, the last offset seen is used: clocks drift slowly.
 */
import type { AxiosInstance } from "axios";

let offsetMs = 0;

/** Pure: the offset a response's Date header implies, or null if unusable. */
export function offsetFrom(dateHeader: unknown, localNowMs: number): number | null {
  if (typeof dateHeader !== "string") return null;
  const server = Date.parse(dateHeader);
  return Number.isFinite(server) ? server - localNowMs : null;
}

export function noteServerDate(dateHeader: unknown, localNowMs = Date.now()): void {
  const o = offsetFrom(dateHeader, localNowMs);
  if (o !== null) offsetMs = o;
}

/** Now, on the server's clock (to the second: the header's resolution). */
export function serverNow(localNowMs = Date.now()): Date {
  return new Date(localNowMs + offsetMs);
}

export function installServerClock(instance: AxiosInstance): void {
  instance.interceptors.response.use((res) => {
    noteServerDate(res.headers?.["date"]);
    return res;
  });
}
