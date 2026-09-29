/**
 * Offline read cache for today's beat and case detail (I02, ADR 0011 §9).
 *
 * Without it an agent who loses signal before opening a case never reaches the
 * visit form, and the outbox has nothing to hold. In-app, not the service
 * worker's runtime caching: Workbox keys by URL, not by login, and one worker
 * serves the whole origin, so the simulator's frames or the next login on the
 * same phone would read the last agent's borrowers (coordinator, 2026-09-29).
 *
 * Rules:
 *   - written on every successful read, used only when the server cannot be
 *     reached (no response, or 5xx), never for a 401/403/404;
 *   - keyed by login, and an entry of another login is never served;
 *   - good until the end of the IST day it was fetched on;
 *   - a case's entry is dropped by any successful mutation of that case;
 *   - everything is dropped at logout, on a change of login, and on unbind.
 * Not encrypted by the app: the pilot's stance is MDM-enforced device
 * encryption (ADR 0011 §8, docs/PILOT-PLAN.md).
 */
import { errorStatus } from "@/lib/apiError";

export interface ReadEntry {
  userId: string;
  day: string;          // IST calendar day the data was fetched on
  savedAt: number;
  data: unknown;
}

export interface ReadStore {
  get(key: string): Promise<ReadEntry | undefined>;
  put(key: string, entry: ReadEntry): Promise<void>;
  remove(key: string): Promise<void>;
  clear(): Promise<void>;
}

const IST_OFFSET_MS = 330 * 60_000;

/** The IST calendar day of an instant, as YYYY-MM-DD. */
function istDay(ms: number): string {
  return new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 10);
}

export const beatKey = (userId: string) => `${userId}|beat`;
export const caseKey = (userId: string, caseId: string) => `${userId}|case:${caseId}`;

/** A failure the cache may stand in for: the server did not answer, or broke. */
export function unreachable(err: unknown): boolean {
  const status = errorStatus(err);
  return status === undefined || status >= 500;
}

export interface Served<T> {
  data: T;
  /** When the copy was fetched; absent when the data is live. */
  cachedAt?: number;
}

export async function cachedRead<T>(
  store: ReadStore, userId: string | null, key: string | null, fetcher: () => Promise<T>, nowMs = Date.now(),
): Promise<Served<T>> {
  try {
    const data = await fetcher();
    if (userId && key) {
      await store.put(key, { userId, day: istDay(nowMs), savedAt: nowMs, data }).catch(() => {});
    }
    return { data };
  } catch (err) {
    if (!userId || !key || !unreachable(err)) throw err;
    const hit = await store.get(key).catch(() => undefined);
    if (!hit || hit.userId !== userId || hit.day !== istDay(nowMs)) throw err;
    return { data: hit.data as T, cachedAt: hit.savedAt };
  }
}

const CASE_PATH = /\/agent\/cases\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:[/?]|$)/i;

/** The case a request changes, for a successful non-GET, else null. */
export function mutatedCase(method: string | undefined, url: string | undefined): string | null {
  if (!url || !method || method.toLowerCase() === "get") return null;
  return CASE_PATH.exec(url)?.[1] ?? null;
}

/** Whose copies these are: the slot, the login and the device together. A
 *  change of any one drops every saved copy (Opus audit of 1a85ffd). */
export function cacheOwner(s: { isAuthenticated: boolean; user: { id: string } | null; deviceId: string },
                           slot: string | null): string {
  return `${slot ?? "main"}|${s.isAuthenticated ? s.user?.id ?? "" : ""}|${s.deviceId}`;
}

/** The in-memory store: tests, and the fallback when IndexedDB is unavailable. */
export class MemoryReadStore implements ReadStore {
  private m = new Map<string, ReadEntry>();
  async get(key: string) { return this.m.get(key); }
  async put(key: string, entry: ReadEntry) { this.m.set(key, entry); }
  async remove(key: string) { this.m.delete(key); }
  async clear() { this.m.clear(); }
  get size(): number { return this.m.size; }
}
