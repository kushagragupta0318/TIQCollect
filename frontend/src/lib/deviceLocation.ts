/**
 * The one door to the device's location.
 *
 * 2026-09-24 (standalone plan, task P0-02) — six components called
 * `navigator.geolocation` directly. That made the agent app impossible to drive
 * from the mobile simulator: a desktop browser's "GPS" is a Wi-Fi guess
 * (often the city centre, sometimes nothing), so a simulated agent could never
 * stand inside a borrower's 100 m geofence, and "play beat route" had nothing
 * to push positions into.
 *
 * `geo` has the same three methods and callback shapes as
 * `navigator.geolocation`, so every call site changed by one identifier.
 * (Distance maths stays in lib/geo.ts; this module is only the sensor.)
 *
 *   - Ordinary page: delegates straight to `navigator.geolocation`. Nothing
 *     about real devices changes.
 *   - Simulator frame (a session slot AND framed): positions come ONLY from
 *     the simulator, by `postMessage`, and the real sensor is never asked — so
 *     no permission prompt appears inside the phone frame and no laptop
 *     location leaks into a demo. A request made before the first fix waits
 *     for it (honouring `options.timeout`, exactly as the real API would).
 *
 * Messages are accepted only from `window.parent` on the SAME origin. This is
 * not a security boundary — the client is untrusted either way, and the server
 * re-checks every geofence from the submitted fix — it just stops an unrelated
 * embedding page from steering a frame it does not own.
 */
import { SESSION_SLOT } from "@/lib/sessionSlot";

/** postMessage protocol between the simulator and a framed agent app. */
export const SIM_GEO_FIX = "tiq-sim:geo";
export const SIM_GEO_ERROR = "tiq-sim:geo-error";
export const SIM_FRAME_READY = "tiq-sim:ready";

export interface SimFix {
  lat: number;
  lon: number;
  accuracy?: number;
  altitude?: number | null;
  heading?: number | null;
  speed?: number | null;
}

type Success = (pos: GeolocationPosition) => void;
type Failure = (err: GeolocationPositionError) => void;

function framed(): boolean {
  try {
    return window.parent !== window;
  } catch {
    return true; // cross-origin parent: still a frame
  }
}

export const IS_SIMULATED_GEO: boolean =
  typeof window !== "undefined" && SESSION_SLOT !== null && framed();

/** Build a GeolocationPosition-shaped object from a simulator fix. */
export function toPosition(fix: SimFix, timestamp: number = Date.now()): GeolocationPosition {
  const coords = {
    latitude: fix.lat,
    longitude: fix.lon,
    accuracy: fix.accuracy ?? 8,
    altitude: fix.altitude ?? null,
    altitudeAccuracy: null,
    heading: fix.heading ?? null,
    speed: fix.speed ?? null,
  };
  return {
    coords: { ...coords, toJSON: () => coords },
    timestamp,
    toJSON: () => ({ coords, timestamp }),
  } as GeolocationPosition;
}

function toError(code: 1 | 2 | 3, message: string): GeolocationPositionError {
  return {
    code,
    message,
    PERMISSION_DENIED: 1,
    POSITION_UNAVAILABLE: 2,
    TIMEOUT: 3,
  } as GeolocationPositionError;
}

export function isSimFix(v: unknown): v is SimFix {
  if (typeof v !== "object" || v === null) return false;
  const f = v as Record<string, unknown>;
  return (
    typeof f.lat === "number" && Number.isFinite(f.lat) && Math.abs(f.lat) <= 90 &&
    typeof f.lon === "number" && Number.isFinite(f.lon) && Math.abs(f.lon) <= 180
  );
}

// ── Simulated source ─────────────────────────────────────────────────────────

type Pending = { ok: Success; fail?: Failure; timer?: ReturnType<typeof setTimeout> };

let latest: GeolocationPosition | null = null;
let nextId = 1;
const watchers = new Map<number, { ok: Success; fail?: Failure }>();
const pending = new Set<Pending>();

function deliver(pos: GeolocationPosition) {
  latest = pos;
  for (const w of watchers.values()) w.ok(pos);
  for (const p of pending) {
    if (p.timer) clearTimeout(p.timer);
    p.ok(pos);
  }
  pending.clear();
}

function fail(err: GeolocationPositionError) {
  for (const w of watchers.values()) w.fail?.(err);
  for (const p of pending) {
    if (p.timer) clearTimeout(p.timer);
    p.fail?.(err);
  }
  pending.clear();
}

if (IS_SIMULATED_GEO) {
  window.addEventListener("message", (e: MessageEvent) => {
    if (e.source !== window.parent || e.origin !== window.location.origin) return;
    const data = e.data as { type?: unknown; fix?: unknown; code?: unknown } | null;
    if (!data || typeof data !== "object") return;
    if (data.type === SIM_GEO_FIX && isSimFix(data.fix)) {
      deliver(toPosition(data.fix));
    } else if (data.type === SIM_GEO_ERROR) {
      const code = data.code === 1 || data.code === 3 ? data.code : 2;
      fail(toError(code, "Simulated GPS failure"));
    }
  });
  // Tell the simulator this frame (re)loaded so it pushes the current fix at
  // once — a reload inside the phone would otherwise wait for the next tick.
  try {
    window.parent.postMessage({ type: SIM_FRAME_READY, slot: SESSION_SLOT }, window.location.origin);
  } catch {
    /* parent on another origin: nothing to tell */
  }
}

const simulated: Pick<Geolocation, "getCurrentPosition" | "watchPosition" | "clearWatch"> = {
  getCurrentPosition(ok, failCb, options) {
    if (latest) {
      const pos = latest;
      setTimeout(() => ok(pos), 0);
      return;
    }
    const entry: Pending = { ok, fail: failCb ?? undefined };
    const timeout = options?.timeout;
    if (timeout !== undefined && Number.isFinite(timeout)) {
      entry.timer = setTimeout(() => {
        pending.delete(entry);
        entry.fail?.(toError(3, "Timed out waiting for a simulated fix"));
      }, timeout);
    }
    pending.add(entry);
  },
  watchPosition(ok, failCb) {
    const id = nextId++;
    watchers.set(id, { ok, fail: failCb ?? undefined });
    if (latest) {
      const pos = latest;
      setTimeout(() => { if (watchers.has(id)) ok(pos); }, 0);
    }
    return id;
  },
  clearWatch(id) {
    watchers.delete(id);
  },
};

// ── Public API ───────────────────────────────────────────────────────────────

/** True when a location source exists (simulated, or the real sensor). */
export function geoAvailable(): boolean {
  if (IS_SIMULATED_GEO) return true;
  return typeof navigator !== "undefined" && "geolocation" in navigator;
}

/** Drop-in for `navigator.geolocation`. Call `geoAvailable()` first where the
 *  real API might be missing, exactly as the old `"geolocation" in navigator`
 *  checks did. */
export const geo: Pick<Geolocation, "getCurrentPosition" | "watchPosition" | "clearWatch"> =
  IS_SIMULATED_GEO
    ? simulated
    : {
        getCurrentPosition: (ok, failCb, options) =>
          navigator.geolocation.getCurrentPosition(ok, failCb, options),
        watchPosition: (ok, failCb, options) =>
          navigator.geolocation.watchPosition(ok, failCb, options),
        clearWatch: (id) => navigator.geolocation.clearWatch(id),
      };
