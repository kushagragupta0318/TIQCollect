// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-24 — NEW (I01). Registers the app-shell service worker and, when a
//   new build is waiting, ASKS before reloading. A field agent can be
//   halfway through Record Visit when a deploy lands; an automatic reload
//   would throw the half-filled form away. So the waiting version sits
//   behind a toast with a Reload button, and nothing reloads until it is
//   tapped. Options (registerType "prompt") in lib/pwaConfig.ts.
//
//   In `vite dev` the plugin resolves `virtual:pwa-register` to a no-op, so
//   calling this there registers nothing.
// ─────────────────────────────────────────────────────────────────────────────
/// <reference types="vite-plugin-pwa/vanillajs" />
import { registerSW } from "virtual:pwa-register";
import { toast } from "react-hot-toast";

/** One toast however many times the waiting version is announced. */
const UPDATE_TOAST_ID = "sw-update-ready";

export function registerServiceWorker(): void {
  const updateSW = registerSW({
    onNeedRefresh() {
      toast(
        (t) => (
          <span className="flex items-center gap-3">
            <span>
              A new version of TIQCollect is ready.
              <span className="block text-[12px] font-normal text-muted-foreground">Save anything you are filling in first.</span>
            </span>
            <button
              type="button"
              onClick={() => {
                toast.dismiss(t.id);
                // Tells the waiting worker to take over; the page reloads
                // once it controls it — and only then.
                void updateSW();
              }}
              className="tap-target flex-shrink-0 rounded-xl px-3 py-1.5 text-xs font-semibold text-white"
              style={{ background: "#2563EB" }}
            >
              Reload
            </button>
          </span>
        ),
        { id: UPDATE_TOAST_ID, duration: Infinity },
      );
    },
    onRegisterError(error: unknown) {
      // Not fatal: the app works exactly as before without a worker. The
      // usual cause on a phone is an untrusted self-signed certificate.
      console.error("service worker registration failed", error);
    },
  });
}
