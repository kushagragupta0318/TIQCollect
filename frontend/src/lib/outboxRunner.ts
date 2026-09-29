/**
 * When the offline outbox (lib/outbox.ts) sends, and who may send.
 *
 * Triggers: app start, `online`, the tab becoming visible, a 60 s tick while
 * anything is pending, a submit, and "Sync now". One flusher per session slot
 * across tabs (navigator.locks), so two tabs never replay the same item at once.
 * No Background Sync: it is Chrome-only and would put tokens in the service
 * worker (ADR 0011 §7). The queue drains while the app is open.
 */
import {
  getPhotoUploadUrl, logCall, queueVisitTranscription, recordVisit, setPTP,
} from "@/api/agent";
import {
  MemoryOutboxStore, discard, enqueueCall, enqueueVisit, flush, usage,
  type FlushReport, type NewVisit, type OutboxApi, type OutboxItem, type OutboxStore, type Usage, type Who,
} from "@/lib/outbox";
import { IdbOutboxStore } from "@/lib/outboxIdb";
import { purgeReadCache } from "@/lib/readCacheRuntime";
import { serverNow } from "@/lib/serverClock";
import { SESSION_SLOT } from "@/lib/sessionSlot";
import { useAuthStore } from "@/store/authStore";
import type { LogCallPayload } from "@/api/agent";

const TICK_MS = 60_000;

let store: OutboxStore | null = null;
export function outboxStore(): OutboxStore {
  if (!store) store = typeof indexedDB === "undefined" ? new MemoryOutboxStore() : new IdbOutboxStore();
  return store;
}

const realApi: OutboxApi = {
  photoUploadUrl: (caseId, subject, capture) => getPhotoUploadUrl(caseId, subject, capture),
  async putObject(url, blob, contentType) {
    const res = await fetch(url, { method: "PUT", body: blob, headers: { "Content-Type": contentType } });
    // An expired presigned URL is a 403 from MinIO: retry, a new URL is issued next time.
    if (!res.ok) throw Object.assign(new Error(`Upload failed (${res.status})`), { response: { status: 503 } });
  },
  recordVisit: (caseId, body) => recordVisit(caseId, body),
  setPTP: (caseId, body) => setPTP(caseId, body),
  logCall: (caseId, body) => logCall(caseId, body),
  transcribe: (visitId, recorder) => queueVisitTranscription(visitId, recorder),
};

export function currentWho(): Who | null {
  const { user, deviceId, isAuthenticated } = useAuthStore.getState();
  return isAuthenticated && user?.id ? { userId: user.id, deviceId } : null;
}

// ── counts for the header badge ──────────────────────────────────────────────
type Listener = (u: Usage) => void;
const listeners = new Set<Listener>();
let last: Usage = { pending: 0, attention: 0, bytes: 0, otherLogins: 0 };

export function subscribeOutbox(fn: Listener): () => void {
  listeners.add(fn);
  fn(last);
  return () => { listeners.delete(fn); };
}

export async function refreshOutboxUsage(): Promise<Usage> {
  try {
    last = await usage(outboxStore(), currentWho());
  } catch {
    // Storage unreadable (private window, quota): the badge keeps its last counts.
  }
  listeners.forEach((fn) => fn(last));
  return last;
}

export function outboxUsage(): Usage {
  return last;
}

// ── flushing ─────────────────────────────────────────────────────────────────
const EMPTY: FlushReport = { sent: [], refused: [], waiting: false };

async function withFlushLock(fn: () => Promise<FlushReport>): Promise<FlushReport> {
  const locks = (navigator as Navigator & { locks?: LockManager }).locks;
  if (!locks) return fn();
  return locks.request(`tiq-outbox:${SESSION_SLOT ?? "main"}`, fn) as Promise<FlushReport>;
}

/** Send what can be sent now. Never throws. */
export async function flushOutbox(): Promise<FlushReport> {
  const who = currentWho();
  if (!who) return EMPTY;
  let report = EMPTY;
  try {
    report = await withFlushLock(() => flush(outboxStore(), realApi, who));
    // Refused as another phone's: this device is no longer the agent's, so
    // the saved copies of their cases go (coordinator: purge on unbind).
    if (report.refused.length) {
      const refused = (await outboxStore().list()).filter((it) => report.refused.includes(it.id));
      if (refused.some((it) => it.error?.code === "CAPTURE_DEVICE_MISMATCH")) await purgeReadCache();
    }
  } catch {
    // Storage failed mid-flush; the next trigger tries again.
  }
  await refreshOutboxUsage();
  return report;
}

// ── capture ──────────────────────────────────────────────────────────────────
let persistAsked = false;
function askPersistence(): void {
  // Ask once for storage the browser will not evict under pressure.
  if (persistAsked) return;
  persistAsked = true;
  navigator.storage?.persist?.().catch(() => {});
}

function requireWho(): Who {
  const who = currentWho();
  if (!who) throw new Error("Log in again to record this.");
  return who;
}

export type SubmitResult =
  | { status: "sent" }
  | { status: "queued" }                                   // on the phone, will send later
  | { status: "refused"; message: string; code?: string };

/**
 * Store a visit on the phone, then try to send it at once. A refusal on the
 * spot hands the record back (removed from the queue), so the agent fixes the
 * form as before; anything else is the outbox's job from here.
 */
export async function submitVisit(v: NewVisit): Promise<SubmitResult> {
  askPersistence();
  const item = await enqueueVisit(outboxStore(), requireWho(), v);
  return settle(item);
}

export async function submitCall(c: { caseId: string; caseLabel: string; body: LogCallPayload }): Promise<SubmitResult> {
  askPersistence();
  const item = await enqueueCall(outboxStore(), requireWho(), { ...c, capturedAt: serverNow() });
  return settle(item);
}

async function settle(item: OutboxItem): Promise<SubmitResult> {
  const report = navigator.onLine ? await flushOutbox() : EMPTY;
  if (report.sent.includes(item.id)) return { status: "sent" };
  const now = await outboxStore().get(item.id);
  if (now?.state === "attention") {
    await discard(outboxStore(), item.id);
    await refreshOutboxUsage();
    return { status: "refused", message: now.error?.message ?? "Refused", code: now.error?.code };
  }
  await refreshOutboxUsage();
  return { status: "queued" };
}

export async function discardOutboxItem(id: string): Promise<void> {
  await discard(outboxStore(), id);
  await refreshOutboxUsage();
}

export async function outboxItems(): Promise<OutboxItem[]> {
  const who = currentWho();
  return (await outboxStore().list())
    .filter((it) => who && it.userId === who.userId && it.deviceId === who.deviceId)
    .sort((a, b) => a.capture.device_seq - b.capture.device_seq);
}

// ── lifecycle ────────────────────────────────────────────────────────────────
let running = false;
let timer: ReturnType<typeof setInterval> | undefined;

function onVisible(): void {
  if (document.visibilityState === "visible") void flushOutbox();
}
function onOnline(): void {
  void flushOutbox();
}

export function startOutbox(): void {
  if (running) return;
  running = true;
  window.addEventListener("online", onOnline);
  document.addEventListener("visibilitychange", onVisible);
  timer = setInterval(() => {
    if (last.pending > 0 && navigator.onLine) void flushOutbox();
  }, TICK_MS);
  void flushOutbox();
}

export function stopOutbox(): void {
  if (!running) return;
  running = false;
  window.removeEventListener("online", onOnline);
  document.removeEventListener("visibilitychange", onVisible);
  if (timer) clearInterval(timer);
}
