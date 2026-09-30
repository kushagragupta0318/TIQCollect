# Native mobile app plan (Android + iOS)

Status: reference plan, not scheduled work. Written 2026-09-30 against the code on
`TIQCollect-app`. Keep it honest: it is here so that when the owner decides to build the
native apps, the decision and the first steps are already reasoned from the actual codebase,
not from a generic tutorial. Where it cites effort, the assumptions are stated; adjust them,
don't trust the number.

Related: `docs/STANDALONE-TASKS.md` I01/I02/I03, `docs/adr/0011-offline-outbox.md`,
`frontend/src/lib/pwaConfig.ts`.

---

## 1. The honest starting point: this is already a PWA

TIQCollect is a plain React 19 + Vite SPA that was made an **installable PWA** in I01
(`frontend/src/lib/pwaConfig.ts`, `vite.config.ts`, `registerServiceWorker.tsx`,
`swLifecycle.ts`). There is **no Capacitor, Cordova, React Native or Expo** in the tree today.

What "install to home screen" already buys, on both platforms:

- **Add-to-home-screen** with the TIQCollect icon and a standalone (no browser chrome) window.
  Manifest: `display: "standalone"`, icons at 192/512 + maskable (`pwaConfig.ts`).
- **The app shell opens offline.** The service worker precaches the built JS/CSS/HTML and
  build-time assets and falls back to `index.html` for navigations (`pwaConfig.ts` workbox
  block). It deliberately does **no** runtime API caching — no stale API answers.
- **Offline field capture already works** without any native code. The outbox
  (`frontend/src/lib/outbox.ts`, ADR 0011) queues visits, photos, signature, PTP and call logs
  in IndexedDB and replays them in capture order with a per-device monotonic sequence, resuming
  mid-item after a crash. Payments are never queued (`outbox.ts` line 11: the borrower OTP must
  reach the server live). This is genuinely good and survives the move to native untouched.
- **A GPS trail while the app is foregrounded.** `locationReporter.ts` watches position, queues
  on 50 m of movement or every 15 s, heartbeats a stationary phone, and batches uploads to the
  live map.

### The limits — and why they matter for field agents

The product's core job is that a **field agent walks a beat with the phone in a pocket**, and
the manager's live map must keep seeing them. A PWA cannot do that. Concretely:

- **No background geolocation.** `locationReporter.ts` is built entirely on the browser
  `watchPosition` seam (`deviceLocation.ts` → `navigator.geolocation`). Every browser suspends
  `watchPosition` when the tab is hidden or the screen locks. The code already fights this:
  it heartbeats, flushes on `visibilitychange: hidden` because "the tab being hidden is the most
  likely moment for the OS to discard it" (`locationReporter.ts` ~line 247), and caps the
  heartbeat with `STALE_FIX_MS` so it stops fabricating freshness once GPS goes quiet. Those are
  mitigations for a limitation that cannot be removed in a PWA. **STANDALONE-TASKS I03 is
  correct: "a PWA cannot track GPS with the screen off."** On **iOS this is absolute** — Safari
  gives a web app no background location at all. On **Android** an installed PWA gets a little
  more leeway but still no reliable pocket/screen-off tracking. This alone is the reason to go
  native.
- **iOS PWA storage eviction.** IndexedDB / localStorage in an iOS home-screen web app can be
  evicted by the OS under storage pressure or after ~7 days of non-use. The outbox holds up to
  60 items / 200 MB of unsent evidence (`outbox.ts` `MAX_ITEMS`/`MAX_BYTES`) and the location
  queue holds up to 48 h (`locationReporter.ts` `QUEUE_HOURS`). Eviction means **silent loss of
  captured field work**. On native, this storage is the app's own and is not evicted.
- **iOS push is weak.** Web push on iOS requires the PWA to be installed and is unreliable; the
  06:00 morning beat push and 09:00 PTP reminders (see CLAUDE.md scheduled work) cannot depend
  on it. Native gives real APNs/FCM.
- **No true background sync.** The Background Sync API is Chromium-only; the outbox flush is
  driven by app foreground/online events (`outboxRunner.ts`, `locationReporter.ts`). A native
  app can flush from a background task.
- **iOS install friction.** Add-to-home-screen on iOS is a manual Safari "Share → Add to Home
  Screen" flow; there is no install prompt and no store presence. For a paid pilot handed to an
  agency, "download from the App Store" is what people expect.
- **Camera/mic quality.** `RecordVisitPage.tsx` / `AgentHomePage.tsx` use `getUserMedia` /
  `MediaRecorder` for photos and the voice recording. These work in the browser but the recorder
  codec/format differs across iOS Safari and Chrome; native plugins give one predictable path.

**Verdict:** the PWA is a real, useful artifact and a good demo/fallback, but it **cannot meet
the background-GPS requirement**, and on iOS it is fragile for offline evidence. That gap, not
polish, is what forces a native build.

---

## 2. Conversion routes compared (for *this* codebase)

### Route A — Capacitor (wrap the existing React app in a native shell)

Capacitor loads the already-built Vite bundle inside a native WebView and exposes native
capabilities (geolocation, camera, filesystem, secure storage, push) as JS plugins.

- **What survives:** essentially **all** of `frontend/src` — every page (`pages/agent`,
  `manager`, `bank`, `auth`, `simulator`), the whole `api/` layer, `lib/`, `components/`,
  `store/`. The build output is what Capacitor ships.
- **What changes:** a handful of browser seams, and the codebase is already structured so those
  seams are few and centralised (see §5). The API base URL must become absolute
  (`axios.ts` uses relative `/api/v1`); geolocation swaps behind the existing `deviceLocation.ts`
  seam; token/device-id storage moves off localStorage.
- **Trade-off:** it is still a WebView, so it looks like the web app (which for this product is
  fine — the UI is already the agent's whole world). Not "native feel," but native *capability*.
- **Effort:** lowest. The `deviceLocation.ts` / outbox / `locationReporter.ts` seams were
  designed for exactly this kind of substitution.

### Route B — React Native / Expo (rewrite the UI natively)

- **What survives:** the **backend unchanged**, the API contracts, and the *logic* in `lib/`
  that is pure and framework-agnostic (`outbox.ts` is explicitly "pure logic over two injected
  seams … tested without IndexedDB or a network" — it could be reused almost verbatim; same for
  the geo maths). **What does not survive:** every screen. All of `pages/`, `components/`, the
  Tailwind styling, the bank Command-Center port (`src/bank/*`), the simulator — all rewritten
  in RN primitives. That is thousands of lines of working, tested UI thrown away.
- **Trade-off:** best native feel and smoothest maps/animations; highest cost and a second UI
  codebase to keep in step with the web app forever.
- **When it's worth it:** only if the product later needs deep native UX (fluid offline-first
  map interactions, heavy camera/AR, per-platform design) that a WebView genuinely can't carry.
  Nothing in the current requirements demands it.

### Route C — TWA / PWABuilder (Android-only quick win)

A Trusted Web Activity wraps the deployed PWA URL as an Android app for the Play Store.

- **What survives:** everything; it's the live PWA in an Android shell.
- **The catch:** a TWA is still the PWA — it **inherits every PWA limit above, including no
  background GPS.** It gets you a Play Store listing, not the capability the product needs.
  Useful only as a stopgap Android listing, and **useless for iOS** (no TWA equivalent).

---

## 3. Recommendation

**Route A (Capacitor), for both platforms.** Justified from the code:

1. The app is a **working, tested React PWA** — Capacitor reuses it wholesale, where RN would
   discard the entire `pages/`+`components/`+`src/bank/` UI.
2. The browser seams that must change are **already isolated**: one geolocation door
   (`deviceLocation.ts`), one storage seam per concern (outbox store, auth store), one API client
   (`axios.ts`), one media path. Capacitor's plugin model drops in behind these.
3. The one hard requirement the PWA fails — **background GPS with the screen off** — is exactly
   what a Capacitor background-geolocation plugin provides, feeding the existing
   `locationReporter.ts` queue-and-upload machinery without rewriting it.
4. One codebase keeps serving web + Android + iOS.

**Choose RN instead only if** a future need for true native UX appears; revisit then. Do **not**
ship a TWA as the answer — it does not solve background GPS.

---

## 4. Prerequisites before starting

- **Apple Developer Program — $99/year, recurring.** Required to build, sign, TestFlight and ship
  any iOS app. Lapsing it pulls the app from the store.
- **macOS + Xcode.** iOS builds and signing **cannot be done on Windows** (the team works on
  Windows 11 per the environment). Need a Mac (or a hosted mac-in-cloud / a CI mac runner) for
  every iOS build and every store submission.
- **Google Play Developer — $25 one-time.** Android signing key must be generated and backed up
  (loss means you can't update the listing); prefer Play App Signing.
- **App Store review realities for a debt-collection app.** Both stores scrutinise
  financial/collections apps. Expect to declare and justify: the business relationship (this is a
  B2B tool for licensed agencies, not consumer lending), background location use (Apple requires a
  clear in-app justification and a "why always-location" screen), and data handling. Apple's
  guidelines restrict apps that could be seen as harassment/collections; be ready to explain the
  legitimate-agency model and that borrowers are not app users. This can add review cycles — plan
  for rejection-and-resubmit, not a one-shot approval.
- **Permission declarations** (native manifests / Info.plist):
  - Location — **foreground and background/always** (the whole point); iOS purpose strings.
  - Camera — visit photos and ID capture.
  - Microphone — the voice recording feature (`RecordVisitPage.tsx` MediaRecorder).
  - Notifications — morning beat push / PTP reminders.
- **DEMO_OTP_ECHO must stay off** on any build reachable by store reviewers or outside the team
  (CLAUDE.md): it hands the borrower's payment OTP to the agent.

---

## 5. Phased plan

### Phase 0 — Capacitor wrapper that runs the existing bundle
Add Capacitor to `frontend/`, point it at the Vite `dist/`, generate the `android/` and `ios/`
projects. Goal: the current app running inside both shells, talking to a real backend.

- **API base URL (required change).** `frontend/src/api/axios.ts` uses `baseURL: "/api/v1"` and
  the refresh call uses the relative `/api/v1/auth/refresh`. Inside a Capacitor WebView the origin
  is `capacitor://localhost` / `https://localhost`, so relative `/api` reaches nothing. Introduce
  an env-driven absolute base URL (`VITE_API_BASE_URL`) used when running in the native shell, and
  make the refresh path use the same base. This is the single most load-bearing change.
- **SSE.** `/api/v1/events/stream` (the live feed) must use the same absolute base and be exempt
  from the service worker (already handled: `SW_NAVIGATION_DENYLIST` in `pwaConfig.ts`). Confirm
  EventSource works to the absolute origin from the WebView.
- **Service worker.** Inside Capacitor the SW is redundant (the shell is bundled natively) and can
  conflict; disable PWA registration when running native (`registerServiceWorker.tsx` already
  registers by hand, so gate it on a "is native" check).

### Phase 1 — Native plugins that close the PWA gaps
- **Background geolocation (the reason we're here).** Replace the real branch of
  `deviceLocation.ts`'s `geo` object with a Capacitor background-geolocation plugin, keeping the
  **same `getCurrentPosition`/`watchPosition`/`clearWatch` shape** so `locationReporter.ts` and
  every call site are unchanged. The simulator branch (`IS_SIMULATED_GEO`) stays exactly as is —
  the simulator keeps working. Register a foreground service (Android) / background location mode
  (iOS) so fixes flow with the screen off. Verify `locationReporter.ts` flush still runs from a
  background task.
- **Camera & mic** via Capacitor Camera / a media plugin behind the existing `getUserMedia`/
  `MediaRecorder` call sites in `RecordVisitPage.tsx` / `AgentHomePage.tsx`, for a predictable
  cross-platform capture format.
- **Secure storage for JWT + device binding.** Today `authStore.ts` persists `accessToken`,
  `refreshToken` and the generated `deviceId` (`tiq_device_id`) to **localStorage** (zustand
  persist). Device binding (`core/security.py`, `jti` + device) depends on that id being stable
  and private. Move the tokens and the device id to a Keychain/Keystore-backed secure storage
  plugin. On iOS especially this also protects them from the storage-eviction problem. The device
  id can now be a real per-install identifier instead of a random UUID in web storage.
- **Push** via FCM/APNs to back the morning-beat and PTP notifications reliably.

### Phase 2 — Offline outbox on native
The outbox needs **little change**: it is pure logic over an injected `OutboxStore`
(`outbox.ts`), with the IndexedDB adapter isolated in `outboxIdb.ts`. IndexedDB works in the
WebView, so the existing adapter can stay initially; if eviction or size is a concern, swap the
adapter for a native filesystem/SQLite-backed store implementing the same `OutboxStore` interface
— **no change to `outbox.ts` or any call site.** The per-device `device_seq` and
`client_submission_id` replay guarantees carry over unchanged. This clean seam is a real asset.

### Backend changes — probably small
- **CORS.** `main.py` sets `allow_origins=settings.allowed_origins_list`. Add the Capacitor
  origins (`capacitor://localhost`, `https://localhost`, and `ionic://localhost` if used) to the
  allowed list for native builds.
- **Deep links / OAuth-style flows.** Invite acceptance, quick-login tokens and set-password use
  URLs (`api/auth.ts`, `SetPasswordPage.tsx`). For those to open the app from an SMS/email link,
  register Android App Links / iOS Universal Links and handle the deep link route. This is new
  config, not new backend logic.
- **Otherwise unchanged.** The API contracts, auth, device binding, geofence re-checks, capture
  time window and fraud sync checks (`capture_time.py`, `fraud_service`) all operate on submitted
  data and don't care whether the client is a browser or a WebView.
- **`FORWARDED_ALLOW_IPS`** still matters behind the proxy (CLAUDE.md); unchanged by native.

---

## 6. Effort estimate (developer-weeks, ranges)

Assumes one developer competent in React + mobile, a Mac available for iOS, backend mostly as-is,
and the app already being a working PWA (so no UI to build).

| Route | Estimate | Assumptions / what dominates |
|---|---|---|
| **A — Capacitor, both platforms** | **6–10 dev-weeks** | ~1 wk wrapper + API base URL + SW gating; ~2–3 wk background geolocation done *properly* on both platforms (the real cost — battery, screen-off, foreground service, iOS always-permission UX, field testing); ~1 wk secure storage + camera/mic plugins; ~1 wk push + deep links; **2–3 wk store submission, review cycles and rejection handling** for a collections app. Add buffer if there is no Mac/CI-mac yet. |
| A — Android only (Capacitor) | 4–6 dev-weeks | Same minus iOS build/review; still includes background GPS. |
| **C — TWA/PWABuilder (Android only)** | **~1 dev-week** | Cheap, but **does not deliver background GPS** — a listing, not the capability. Stopgap only. |
| **B — React Native / Expo, both platforms** | **16–28+ dev-weeks** | Full UI rewrite of `pages/` + `components/` + `src/bank/` + simulator, re-testing everything, plus all the native-capability and store work Route A also carries. Reuses backend and the pure `lib/` logic; loses all the UI. |

**Biggest single risk to the timeline is not code — it is App Store review** of a
debt-collection app requesting always-on background location. Budget for iteration there.

---

## Summary

Go **Capacitor** for Android + iOS. It reuses effectively the entire existing React PWA, and the
codebase's centralised seams — `deviceLocation.ts` (geolocation), `outbox.ts` (pure, injected
store), `axios.ts` (one API client), `authStore.ts` (one token/device store) — mean the native
substitutions are small and localised. The PWA cannot deliver screen-off background GPS, which is
the product's core field requirement, so native is genuinely necessary, not cosmetic. Realistic
first cut: **6–10 dev-weeks** for both platforms, with **store review of a collections app** as
the dominant uncertainty.
