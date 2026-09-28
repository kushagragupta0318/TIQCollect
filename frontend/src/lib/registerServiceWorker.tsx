// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-24 — NEW (I01). Registers the app-shell service worker and, when a
//   new build is waiting, ASKS before reloading. A field agent can be
//   halfway through Record Visit when a deploy lands; an automatic reload
//   would throw the half-filled form away. So the waiting version sits
//   behind a toast with a Reload button, and nothing reloads until it is
//   tapped. Options (registerType "prompt") in lib/pwaConfig.ts.
//
// 2026-09-24 — Corrected after the coordinator's audit of 5f89265. This file
//   used to claim "nothing reloads until it is tapped", and that was true only
//   of the tab where it was tapped: vite-plugin-pwa's register.js reloads
//   EVERY tab that showed the prompt once the new worker takes over. Now
//   onNeedReload reloads only the tab that asked, and any other tab gets a
//   toast instead (lib/swLifecycle.ts, tested there). Also new: an update
//   check hourly and whenever the tab returns to the foreground (a tab open
//   all day never looked before), and in the DEV build the removal of any
//   worker left on the dev origin — the previous comment said "a no-op under
//   vite dev", which was true of registering and not of a worker a `vite
//   preview` had already installed on the same host:port.
// ─────────────────────────────────────────────────────────────────────────────
/// <reference types="vite-plugin-pwa/vanillajs" />
import { registerSW } from "virtual:pwa-register";
import { toast } from "react-hot-toast";
import {
  UPDATE_CHECK_INTERVAL_MS, createSwLifecycle, unregisterAllWorkers,
} from "@/lib/swLifecycle";

/** One toast however many times the waiting version is announced. */
const UPDATE_TOAST_ID = "sw-update-ready";

function ToastWithAction({ title, note, action, onAction }: {
  title: string; note: string; action: string; onAction: () => void;
}) {
  return (
    <span className="flex items-center gap-3">
      <span>
        {title}
        <span className="block text-[12px] font-normal text-muted-foreground">{note}</span>
      </span>
      <button
        type="button"
        onClick={onAction}
        className="tap-target flex-shrink-0 rounded-xl px-3 py-1.5 text-xs font-semibold text-white"
        style={{ background: "#2563EB" }}
      >
        {action}
      </button>
    </span>
  );
}

export function registerServiceWorker(): void {
  if (import.meta.env.DEV) {
    // The dev origin is never served by a worker. Remove any a `vite preview`
    // on this same host:port left behind, or it keeps serving its cached shell.
    void unregisterAllWorkers(
      "serviceWorker" in navigator ? navigator.serviceWorker : undefined,
      "caches" in window ? caches : undefined,
    ).then((n) => {
      if (n > 0) console.warn(`removed ${n} service worker(s) left on the dev origin — reload once`);
    });
    return;
  }

  const life = createSwLifecycle({
    promptUpdate: (apply) =>
      toast(
        (t) => (
          <ToastWithAction
            title="A new version of TIQCollect is ready."
            note="Save anything you are filling in first."
            action="Reload"
            onAction={() => { toast.dismiss(t.id); apply(); }}
          />
        ),
        { id: UPDATE_TOAST_ID, duration: Infinity },
      ),
    warnUpdatedElsewhere: (reload) =>
      toast(
        (t) => (
          <ToastWithAction
            title="TIQCollect was updated in another tab."
            note="Finish what you are doing here, then reload."
            action="Reload"
            onAction={() => { toast.dismiss(t.id); reload(); }}
          />
        ),
        { id: UPDATE_TOAST_ID, duration: Infinity },
      ),
    reload: () => window.location.reload(),
    now: () => Date.now(),
  });

  const updateSW = registerSW({
    onNeedRefresh: life.onNeedRefresh,
    onNeedReload: life.onNeedReload,
    onRegisteredSW(_url, reg) {
      if (!reg) return;
      window.setInterval(() => life.maybeCheckForUpdate(reg), UPDATE_CHECK_INTERVAL_MS);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") life.maybeCheckForUpdate(reg);
      });
    },
    onRegisterError(error: unknown) {
      // Not fatal: the app works exactly as before without a worker. The
      // usual cause on a phone is an untrusted self-signed certificate.
      console.error("service worker registration failed", error);
    },
  });
  life.setApplyUpdate(updateSW);
}
