// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-18 — New file. Uploads the on-duty location trail that
//   Agent.last_known_latitude always claimed to carry ("updated via heartbeat
//   every 30s when on duty") but which nothing ever produced — see
//   backend/app/models/agent_location.py.
//
//   A module singleton rather than a hook: the queue has to survive route
//   changes and component unmounts, and there must be exactly one uploader per
//   tab regardless of how many components ask for tracking.
// 2026-09-14 — Cadence 60s → 15s on both the queue and the upload, and a
//   stationary heartbeat. Measured on the live map: a manager saw agents at
//   "28 min ago" and "6 d ago" while they were logged in, because (a) a fix
//   was queued at most once a minute and uploaded at most once a minute, and
//   (b) watchPosition only fires when the phone MOVES, so a parked agent
//   produced nothing at all. The heartbeat re-queues the last known fix with a
//   fresh timestamp when nothing has been queued for MAX_INTERVAL_MS, so a
//   still phone is "still here", not "silent". Battery cost is one small
//   request every 15s; the 50 m movement gate is unchanged.
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
// stationary agent produces one point every 15 seconds, not one a second.
const MIN_MOVE_M = 50;
const MAX_INTERVAL_MS = 15_000;

// Upload cadence. Batching still applies (a burst of movement is one request,
// not twenty), but the timer is what bounds how stale the manager's map can be.
const FLUSH_INTERVAL_MS = 15_000;
const FLUSH_AT_SIZE = 20;

// Hard ceiling on the offline queue. Beyond this the OLDEST fixes are dropped:
// during a long outage the recent trail is what matters for finding someone.
//
// 2026-09-15 — was 500, which doubled as the per-request ceiling because it
// matched MAX_BATCH server-side. At the old 60 s cadence 500 fixes buffered
// ~8 h of dead zone; at 15 s it buffers ~2 h, so the 15 s change quietly cut
// offline endurance fourfold. The queue is sized in TIME now (8 h) and the
// request ceiling is its own constant, which is what MAX_BATCH always was.
const QUEUE_HOURS = 8;
const MAX_QUEUE = (QUEUE_HOURS * 3_600_000) / MAX_INTERVAL_MS;   // 1,920 at 15 s
// Must not exceed MAX_BATCH in app/services/location_service.py — the server
// rejects an oversized batch outright, which would strand the whole backlog.
const MAX_BATCH_SEND = 500;

// localStorage, not memory: a phone that reloads, crashes or is backgrounded
// out of memory mid-shift must not lose the queue. The payload is small —
// 500 fixes is roughly 60KB, well inside the ~5MB budget.
const STORAGE_KEY = "tiq.location.queue.v1";

let queue: QueuedPing[] = [];
let unsubscribe: (() => void) | null = null;
let flushTimer: ReturnType<typeof setInterval> | undefined;
let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
// The most recent raw fix, queued or not — what the heartbeat re-sends.
let lastFix: RawFix | null = null;
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
  lastFix = fix;
  if (lastQueued) {
    const moved = metresBetween(lastQueued.lat, lastQueued.lon, fix.lat, fix.lon);
    const elapsed = fix.at - lastQueued.at;
    if (moved < MIN_MOVE_M && elapsed < MAX_INTERVAL_MS) return;
  }
  enqueue(fix);
}

function enqueue(fix: RawFix): void {
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

// A phone that has not moved gets no watchPosition callback, so onFix never
// runs and the trail simply stops. Re-queue the last fix, stamped now, once
// MAX_INTERVAL_MS has passed with nothing queued. Goes straight to enqueue,
// not through onFix's gate: two setIntervals on the same period drift by a
// few ms, and a heartbeat that landed at 14,995 ms was being refused by a
// `< 15_000` check — measured as uploads at 15 s, 30 s, then nothing at 45 s.
// The slack absorbs that.
const HEARTBEAT_SLACK_MS = 1_000;

// How old the last real fix may be before the heartbeat stops re-sending it.
//
// 2026-09-15 — there was no bound, and `lastFix` is never invalidated, so a
// phone whose GPS was revoked or that walked into a basement went on
// reporting its last known point stamped `now`, every 15 s, for as long as
// the app stayed open. The server advances the agent's heard-from time on
// those, so the manager's map read a confident "just now" at a position that
// could be hours old. On the screen used to find a lone worker, an honest
// "40 min ago" is worth more than a fresh-looking lie. The watcher retries on
// its own 20 s timeout, so two minutes of silence is a real outage, not a gap
// between fixes.
const STALE_FIX_MS = 120_000;

function heartbeat(): void {
  if (!lastFix) return;
  const now = Date.now();
  if (now - lastFix.at > STALE_FIX_MS) return;     // we no longer know where they are
  if (lastQueued && now - lastQueued.at < MAX_INTERVAL_MS - HEARTBEAT_SLACK_MS) return;
  enqueue({ ...lastFix, at: now });
}

/**
 * Queue the current position immediately and upload it, bypassing the movement
 * gate. The agent asked to be seen — the refresh button — so "you have not
 * moved 50 m" is not a reason to stay silent. Returns false when there is no
 * fix recent enough to stand behind, and the caller must not claim otherwise.
 */
export function reportNow(): boolean {
  const now = Date.now();
  if (!lastFix || now - lastFix.at > STALE_FIX_MS) return false;
  enqueue({ ...lastFix, at: now });
  void flush();
  return true;
}

// ── upload ──────────────────────────────────────────────────────────────────
export async function flush(): Promise<void> {
  if (flushing || queue.length === 0) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) return;

  flushing = true;
  // Take a snapshot and clear optimistically, so fixes arriving mid-request are
  // not lost to the splice. On failure the snapshot is put back in front.
  const batch = queue.slice(0, MAX_BATCH_SEND);
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
  heartbeatTimer = setInterval(heartbeat, MAX_INTERVAL_MS);
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
  if (heartbeatTimer !== undefined) { clearInterval(heartbeatTimer); heartbeatTimer = undefined; }
  window.removeEventListener("online", flush);
  document.removeEventListener("visibilitychange", onVisibility);
  lastQueued = null;
  // Dropped with it: a logout/login in the same tab would otherwise let the
  // first heartbeat of the new session upload the previous session's position
  // stamped now — the same fabricated freshness STALE_FIX_MS guards against.
  lastFix = null;
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
