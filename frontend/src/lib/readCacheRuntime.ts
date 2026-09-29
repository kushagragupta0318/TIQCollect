/**
 * The read cache's live wiring (lib/readCache.ts holds the rules): the store,
 * the "showing a saved copy" state for the banner, invalidation on a
 * successful mutation, and the purges at logout, login change and unbind.
 */
import type { AxiosInstance } from "axios";
import { IdbReadStore } from "@/lib/outboxIdb";
import { MemoryReadStore, cacheOwner, caseKey, mutatedCase, type ReadStore } from "@/lib/readCache";
import { SESSION_SLOT } from "@/lib/sessionSlot";
import { useAuthStore } from "@/store/authStore";

let store: ReadStore | null = null;
export function readStore(): ReadStore {
  if (!store) store = typeof indexedDB === "undefined" ? new MemoryReadStore() : new IdbReadStore();
  return store;
}

export function currentUserId(): string | null {
  const { isAuthenticated, user } = useAuthStore.getState();
  return isAuthenticated ? user?.id ?? null : null;
}

// ── "showing a copy saved at HH:MM" ──────────────────────────────────────────
let servedAt: number | null = null;
const listeners = new Set<() => void>();

export function subscribeReadCache(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function readCacheServedAt(): number | null {
  return servedAt;
}

/** Record where the last read came from: a saved copy (its time) or live (undefined). */
export function noteServed(cachedAt?: number): void {
  const next = cachedAt ?? null;
  if (next === servedAt) return;
  servedAt = next;
  listeners.forEach((fn) => fn());
}

// ── invalidation and purges ──────────────────────────────────────────────────
export async function dropCase(caseId: string): Promise<void> {
  const userId = currentUserId();
  if (userId) await readStore().remove(caseKey(userId, caseId)).catch(() => {});
}

export async function purgeReadCache(): Promise<void> {
  await readStore().clear().catch(() => {});
  noteServed(undefined);
}

/** Any successful change to a case drops its saved copy (coordinator, 2026-09-29). */
export function installReadCacheInvalidation(instance: AxiosInstance): void {
  instance.interceptors.response.use((res) => {
    const caseId = mutatedCase(res.config?.method, res.config?.url);
    if (caseId) void dropCase(caseId);
    return res;
  });
}

// Any change of owner (logout, another login, a new device id) drops every saved copy.
let lastOwner = cacheOwner(useAuthStore.getState(), SESSION_SLOT);
useAuthStore.subscribe((s) => {
  const now = cacheOwner(s, SESSION_SLOT);
  if (now !== lastOwner) {
    lastOwner = now;
    void purgeReadCache();
  }
});
