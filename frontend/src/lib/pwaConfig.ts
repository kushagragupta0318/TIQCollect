// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-24 — NEW (I01). The PWA's options, in a module of their own so
//   vite.config.ts and a test read the same object: the test pins the
//   properties that would break the running app if they drifted.
//
//   What the service worker is for here: install to a phone's home screen and
//   open the app shell without the network. NOTHING ELSE. There is no runtime
//   caching and no offline data queue (that is I02, the outbox) — so it never
//   serves a stale API answer, and it never stands between the app and:
//     /api   REST, and /api/v1/events/stream, the SSE feed. An event stream
//            proxied through a service worker stalls — the same failure P0-06
//            hit when GZip buffered it — so the SW must not so much as answer
//            it. No runtime route matches it, and the navigation fallback
//            denies it.
//     /ws    websockets (a SW cannot see them anyway; denied for clarity).
//     /docs  FastAPI's docs, and /redoc + /openapi.json beside them, which
//            the backend serves at the root in DEBUG.
//     /collection_dashboard  a static page in public/, not an app route —
//            without the deny the fallback would hand it the SPA's
//            index.html, whose "*" route redirects to "/". Also kept out of
//            the precache: it is not app shell.
//
//   registerType "prompt", not "autoUpdate": a field agent can be halfway
//   through Record Visit when a deploy lands, and an automatic reload throws
//   the form away. A waiting version shows a toast with a Reload button
//   instead (lib/registerServiceWorker.tsx).
//
//   Off in `vite dev` (devOptions.enabled false): the dev server, HMR and the
//   /simulator iframes behave exactly as before; the SW exists only in a
//   build. A `vite preview` must therefore run on its OWN port (4173, see
//   frontend/README.md): on the dev port it would install a worker that dev
//   never replaces. registerServiceWorker removes any it finds there.
//
//   ROLLBACK. Removing this plugin does not remove an installed worker: every
//   phone that installed it keeps serving the cached shell. To withdraw it,
//   ship ONE build with `selfDestroying: true` added to pwaOptions — the
//   generated sw.js then unregisters itself and clears its caches on the next
//   visit — and only after that remove the plugin. main.py answers a missing
//   *.js with 404 (not index.html), so a stale worker's update check fails
//   cleanly instead of "updating" to an HTML page.
//
//   Colours are the app's own, not a new palette: theme_color is index.html's
//   <meta name="theme-color"> (the TransOrg blue beside the icon), and
//   background_color is what `hsl(var(--background))` renders to, #F3F4F6 —
//   index.css's comment beside that variable says #F4F5F7, one step off
//   what 220 13% 96% actually computes to.
// ─────────────────────────────────────────────────────────────────────────────

import type { VitePWAOptions } from "vite-plugin-pwa";

/**
 * Navigations the service worker must leave to the network. Workbox tests
 * each pattern against `pathname + search`, so every pattern allows a `/`, a
 * `?` or the end of the string after the prefix — and nothing else, so an
 * app route that merely starts with the same letters (`/apiary`) is still the
 * app's.
 */
export const SW_NAVIGATION_DENYLIST: RegExp[] = [
  /^\/api(?:[/?]|$)/,
  /^\/ws(?:[/?]|$)/,
  /^\/docs(?:[/?]|$)/,
  /^\/redoc(?:[/?]|$)/,
  /^\/openapi\.json(?:\?|$)/,
  /^\/collection_dashboard(?:[/?]|$)/,
];

export const pwaOptions: Partial<VitePWAOptions> = {
  registerType: "prompt",
  // Registration is done by hand from main.tsx, so the update prompt is a
  // toast the app controls rather than a script the plugin injects.
  injectRegister: false,
  devOptions: { enabled: false },
  includeAssets: ["favicon.svg", "apple-touch-icon-180x180.png"],
  manifest: {
    id: "/",
    name: "TIQCollect",
    short_name: "TIQCollect",
    description: "Field collections: visits, payments and promises to pay, with the manager's live view.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    theme_color: "#2568B0",
    background_color: "#F3F4F6",
    icons: [
      { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
      { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
      { src: "maskable-icon-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  },
  workbox: {
    // The app shell: the built JS/CSS/HTML, the build's own images and fonts
    // under assets/ (the TransOrg logo on the login page, 58 KiB — without it
    // the offline shell showed a broken image), plus the icons above (added
    // by the plugin). Not the static dashboard in public/, and not the three
    // screenshots public/assets/ copies into assets/ (408 KiB, unhashed, no
    // page shell needs them). Largest chunk measured 366 KiB, far under
    // Workbox's 2 MiB per-file limit, so nothing is silently skipped.
    globPatterns: ["**/*.{js,css,html}", "assets/**/*.{svg,png,webp,woff2}"],
    globIgnores: ["collection_dashboard/**", "assets/Screenshot*"],
    navigateFallback: "index.html",
    navigateFallbackDenylist: SW_NAVIGATION_DENYLIST,
    // Deliberately empty: no API response is ever cached. Offline data is
    // the outbox's job (I02), with its own rules for what may be replayed.
    runtimeCaching: [],
    cleanupOutdatedCaches: true,
  },
};
