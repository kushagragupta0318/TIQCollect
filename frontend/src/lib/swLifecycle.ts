// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-24 — NEW (I01, after the coordinator's audit of 5f89265). The
//   decisions of the service-worker lifecycle, pure, so they can be tested
//   without a browser; lib/registerServiceWorker.tsx wires them to the
//   plugin and to toasts.
//
//   Three defects this exists to prevent, each found by reading
//   vite-plugin-pwa 1.3.0's dist/client/build/register.js:
//   - ONE Reload reloaded EVERY tab. register.js adds a reload-on-
//     `controlling` listener in each tab that showed the update prompt, and a
//     skip-waiting in any one tab hands every tab to the new worker. The
//     simulator's manager frame tapping Reload would reload the agent frame
//     mid-visit. Now only the tab that asked reloads; the others are told.
//   - A tab open all day never looked for a new build: nothing called
//     registration.update(). Now on an interval and when the tab comes back
//     to the foreground, throttled.
//   - A worker installed by `vite preview` on the DEV origin was never
//     evicted: in dev nothing registers /sw.js, so nothing replaces it, and
//     every dev load got the preview's cached shell and old JS. Now the dev
//     build unregisters whatever it finds on its own origin.
// ─────────────────────────────────────────────────────────────────────────────

/** Minimum gap between two update checks from one tab. */
export const UPDATE_CHECK_MIN_GAP_MS = 5 * 60 * 1000;
/** A tab left open checks at least this often. */
export const UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000;

export interface SwLifecycleDeps {
  /** Show "a new version is ready"; `apply` is what its Reload button runs. */
  promptUpdate: (apply: () => void) => void;
  /** Another tab applied the update; this tab is now on new code with an old page. */
  warnUpdatedElsewhere: (reload: () => void) => void;
  reload: () => void;
  now: () => number;
}

export interface SwLifecycle {
  /** The plugin's updateServiceWorker(), once registerSW has returned it. */
  setApplyUpdate: (fn: () => Promise<void> | void) => void;
  /** registerSW's onNeedRefresh: a new build is waiting. */
  onNeedRefresh: () => void;
  /** registerSW's onNeedReload: the new worker now controls this tab. */
  onNeedReload: () => void;
  /** Ask the registration to look for a new build, unless one did so recently. */
  maybeCheckForUpdate: (reg: Pick<ServiceWorkerRegistration, "update">) => boolean;
}

export function createSwLifecycle(deps: SwLifecycleDeps): SwLifecycle {
  let applyUpdate: (() => Promise<void> | void) | null = null;
  let initiatedHere = false;
  let lastCheck = Number.NEGATIVE_INFINITY;

  return {
    setApplyUpdate(fn) {
      applyUpdate = fn;
    },
    onNeedRefresh() {
      deps.promptUpdate(() => {
        initiatedHere = true;
        void applyUpdate?.();
      });
    },
    onNeedReload() {
      // Only the tab whose Reload was tapped reloads. Every other tab keeps
      // its page — and whatever half-filled form is on it — and is told.
      if (initiatedHere) deps.reload();
      else deps.warnUpdatedElsewhere(deps.reload);
    },
    maybeCheckForUpdate(reg) {
      const t = deps.now();
      if (t - lastCheck < UPDATE_CHECK_MIN_GAP_MS) return false;
      lastCheck = t;
      void reg.update().catch(() => undefined); // offline: try again next time
      return true;
    },
  };
}

/**
 * Remove every service worker (and Workbox cache) on this origin. Run by the
 * DEV build only: the dev origin must never be served by a worker, and one
 * left behind by a `vite preview` on the same host:port would otherwise stay
 * forever. Returns how many registrations were removed.
 */
export async function unregisterAllWorkers(
  container: Pick<ServiceWorkerContainer, "getRegistrations"> | undefined,
  cacheStorage?: Pick<CacheStorage, "keys" | "delete">,
): Promise<number> {
  if (!container) return 0;
  const regs = await container.getRegistrations();
  await Promise.all(regs.map((r) => r.unregister()));
  if (cacheStorage) {
    const keys = await cacheStorage.keys();
    await Promise.all(keys.filter((k) => k.startsWith("workbox-")).map((k) => cacheStorage.delete(k)));
  }
  return regs.length;
}
