// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-18 — New file. Uploads the on-duty location trail that
//   Agent.last_known_latitude always claimed to carry ("updated via heartbeat
//   every 30s when on duty") but which nothing ever produced — see
//   backend/app/models/agent_location.py.
//
//   A module singleton rather than a hook: the queue has to survive route
//   changes and component unmounts, and there must be exactly one uploader per
//   tab regardless of how many components ask for tracking.
// ────────────────────────────────────────────────────────────────────────────
import { sendLocationBatch } from "@/api/agent";
import { subscribeToFixes, type RawFix } from "@/hooks/useLiveLocation";

export interface QueuedPing {
  latitude: number;
  longitude: number;
  accuracy_metres: number | null;
  recorded_at: string;          // ISO 8601, device clock
  source: "HEARTBEAT" | "CHECK_IN" | "VISIT" | "SOS";
  battery_pct: number | null;
}

// Queue a fix once the agent has moved this far, OR once this long has passed —
// whichever comes first. Distance-first is what keeps battery drain sane: a
// stationary agent produces one point a minute, not one a second.
const MIN_MOVE_M = 50;
const MAX_INTERVAL_MS = 60_000;

// Upload cadence. Batching means a 30-minute walk is ~30 rows in one request
// rather than 30 requests.
const FLUSH_INTERVAL_MS = 60_000;
const FLUSH_AT_SIZE = 20;

// Hard ceiling on the offline queue. Matches MAX_BATCH server-side. Beyond
// this the OLDEST fixes are dropped: during a long outage the recent trail is
// what matters for finding someone.
const MAX_QUEUE = 500;

// localStorage, not memory: a phone that reloads, crashes or is backgrounded
// out of memory mid-shift must not lose the queue. The payload is small —
// 500 fixes is roughly 60KB, well inside the ~5MB budget.
const STORAGE_KEY = "tiq.location.queue.v1";

let queue: QueuedPing[] = [];
let unsubscribe: (() => void) | null = null;
let flushTimer: ReturnType<typeof setInterval> | undefined;
let lastQueued: { lat: number; lon: number; at: number } | null = null;
let flushing = false;
let running = false;

// ── persistence ─────────────────────────────────────────────────────────────
function load(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    queue = raw ? (JSON.parse(raw) as QueuedPing[]) : [];
  } catch {
    queue = [];
  }
}

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
  } catch {
    // Quota exceeded, or storage disabled in a private window. Losing
    // persistence is survivable; losing the in-memory queue is not, so this
    // deliberately does not clear `queue`.
  }
}

// ── battery ─────────────────────────────────────────────────────────────────
// getBattery() is Chromium-only and returns a live object, so it is read once
// and cached rather than awaited per fix. A lone worker's battery level is a
// safety signal, but not one worth blocking a position on.
let batteryPct: number | null = null;
function watchBattery(): void {
  const nav = navigator as Navigator & { getBattery?: () => Promise<{ level: number; addEventListener: (e: string, cb: () => void) => void }> };
  if (typeof nav.getBattery !== "function") return;
  nav.getBattery().then((b) => {
    const read = () => { batteryPct = Math.round(b.level * 100); };
    read();
    b.addEventListener("levelchange", read);
  }).catch(() => { /* unsupported — battery stays null */ });
}

// ── queueing ────────────────────────────────────────────────────────────────
function metresBetween(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const R = 6_371_000;
  const dLat = ((bLat - aLat) * Math.PI) / 180;
  const dLon = ((bLon - aLon) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((aLat * Math.PI) / 180) * Math.cos((bLat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
}

function onFix(fix: RawFix): void {
  if (lastQueued) {
    const moved = metresBetween(lastQueued.lat, lastQueued.lon, fix.lat, fix.lon);
    const elapsed = fix.at - lastQueued.at;
    if (moved < MIN_MOVE_M && elapsed < MAX_INTERVAL_MS) return;
  }

  queue.push({
    latitude: fix.lat,
    longitude: fix.lon,
    accuracy_metres: fix.accuracy,
    recorded_at: new Date(fix.at).toISOString(),
    source: "HEARTBEAT",
    battery_pct: batteryPct,
  });
  if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE);

  lastQueued = { lat: fix.lat, lon: fix.lon, at: fix.at };
  persist();

  if (queue.length >= FLUSH_AT_SIZE) void flush();
}

// ── upload ──────────────────────────────────────────────────────────────────
export async function flush(): Promise<void> {
  if (flushing || queue.length === 0) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) return;

  flushing = true;
  // Take a snapshot and clear optimistically, so fixes arriving mid-request are
  // not lost to the splice. On failure the snapshot is put back in front.
  const batch = queue.slice(0, MAX_QUEUE);
  queue = queue.slice(batch.length);
  persist();

  try {
    await sendLocationBatch(batch);
  } catch {
    // Network or server error — restore, oldest first, and let the next tick
    // retry. Dropping here is what would silently lose a dead-zone trail.
    queue = [...batch, ...queue].slice(-MAX_QUEUE);
    persist();
  } finally {
    flushing = false;
  }
}

// ── lifecycle ───────────────────────────────────────────────────────────────
export function startLocationReporting(): void {
  if (running) return;
  running = true;
  load();
  watchBattery();
  unsubscribe = subscribeToFixes(onFix);
  flushTimer = setInterval(() => { void flush(); }, FLUSH_INTERVAL_MS);
  window.addEventListener("online", flush);
  // The tab being hidden is the most likely moment for the OS to discard it,
  // and is also when the agent has just pocketed the phone — flush what we have.
  document.addEventListener("visibilitychange", onVisibility);
  void flush();
}

export function stopLocationReporting(): void {
  if (!running) return;
  running = false;
  unsubscribe?.();
  unsubscribe = null;
  if (flushTimer !== undefined) { clearInterval(flushTimer); flushTimer = undefined; }
  window.removeEventListener("online", flush);
  document.removeEventListener("visibilitychange", onVisibility);
  lastQueued = null;
  // The queue is intentionally NOT cleared: fixes captured before going off
  // duty are still that day's trail and should upload on the next start.
  void flush();
}

function onVisibility(): void {
  if (document.visibilityState === "hidden") void flush();
}

/** Fixes waiting to upload — surfaced in the agent header as an offline hint. */
export function pendingCount(): number {
  return queue.length;
}
