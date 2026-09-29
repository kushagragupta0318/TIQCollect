/**
 * The offline outbox: field work captured without signal, sent when it returns.
 *
 * I02 (docs/adr/0011-offline-outbox.md). Visits (with their photos, signature
 * and promise to pay) and call logs are stored on the phone at the moment the
 * agent presses Submit, then replayed in capture order. Every item carries:
 *   - client_submission_id: the server returns the row it already made on a repeat;
 *   - captured_at: the server judges contact hours and the day's beat at this time;
 *   - device_seq: strictly increasing per device, so a replay cannot be backdated
 *     behind something already delivered.
 * Payments are never queued: the borrower's OTP must reach the server live.
 *
 * Pure logic over two injected seams (storage and the API), so it is tested
 * without IndexedDB or a network (outbox.test.ts). The IndexedDB adapter is
 * outboxIdb.ts; the triggers and the one-flusher lock are outboxRunner.ts.
 */
import type { LogCallPayload, OutboxCapture, PtpPayload, VisitPayload } from "@/api/agent";
import { errorCode, errorDetail, errorStatus } from "@/lib/apiError";

export type PhotoSubject = "agent" | "borrower" | "object" | "signature";

export interface MediaPart {
  subject: PhotoSubject;
  blobKey: string;
  contentType: string;
  bytes: number;
  sha256: string;
  /** The object key once uploaded; a retry never uploads it twice. */
  uploadedKey?: string;
}

export interface ItemError {
  message: string;
  code?: string;
  status?: number;
}

interface ItemBase {
  id: string;                 // = the item's client_submission_id
  userId: string;
  deviceId: string;
  caseId: string;
  caseLabel: string;          // shown in the pending list; a case number, no borrower data
  capture: OutboxCapture;
  /** pending: will be sent. attention: the server refused it; only the agent discards it. */
  state: "pending" | "attention";
  attempts: number;
  nextAttemptAt: number;
  error?: ItemError;
  bytes: number;
  createdAt: number;
}

export interface VisitItem extends ItemBase {
  kind: "visit";
  body: VisitPayload;         // everything but the photo keys, which the upload produces
  media: MediaPart[];
  ptp?: { capture: OutboxCapture; body: PtpPayload; done: boolean };
  visitId?: string;           // set once the visit is on the server
}

export interface CallItem extends ItemBase {
  kind: "call";
  body: LogCallPayload;
}

export type OutboxItem = VisitItem | CallItem;

export interface OutboxStore {
  /** The device's next sequence number, allocated atomically. */
  nextSeq(nowMs: number): Promise<number>;
  put(item: OutboxItem): Promise<void>;
  get(id: string): Promise<OutboxItem | undefined>;
  list(): Promise<OutboxItem[]>;
  remove(id: string): Promise<void>;
  putBlob(key: string, blob: Blob): Promise<void>;
  getBlob(key: string): Promise<Blob | undefined>;
  removeBlob(key: string): Promise<void>;
}

export interface OutboxApi {
  photoUploadUrl(caseId: string, subject: PhotoSubject, capture: OutboxCapture): Promise<{ upload_url: string; key: string }>;
  putObject(url: string, blob: Blob, contentType: string): Promise<void>;
  recordVisit(caseId: string, body: VisitPayload & Partial<OutboxCapture>): Promise<{ id: string }>;
  setPTP(caseId: string, body: PtpPayload & Partial<OutboxCapture>): Promise<unknown>;
  logCall(caseId: string, body: LogCallPayload & Partial<OutboxCapture>): Promise<unknown>;
  /** Best-effort: recordings are live-only, but a queued visit may carry keys uploaded live. */
  transcribe?(visitId: string, recorder: "agent" | "borrower" | "both"): Promise<unknown>;
}

export interface Who {
  userId: string;
  deviceId: string;
}

// Caps (coordinator, 2026-09-29): photos are large, and a phone that fills its
// storage fails in worse ways than a refused capture.
export const MAX_ITEMS = 60;
export const MAX_BYTES = 200 * 1024 * 1024;
const MAX_BACKOFF_MS = 5 * 60_000;

export class OutboxFullError extends Error {}

/**
 * The next sequence number: past the last one, and never below the clock.
 * Clock-based so that a phone whose storage was cleared (a fresh counter) does
 * not restart below what the server already holds for this device.
 */
export function nextSeqAfter(last: number, nowMs: number): number {
  return Math.max(last + 1, Math.floor(nowMs));
}

/** Retry later, or stop: the server has answered and will answer the same again. */
export function classify(err: unknown): "retry" | "refused" {
  const status = errorStatus(err);
  if (status === undefined) return "retry";            // no response: offline, timeout
  if (status >= 500 || status === 408 || status === 429 || status === 401) return "retry";
  return "refused";
}

export function backoffMs(attempts: number): number {
  return Math.min(MAX_BACKOFF_MS, 5_000 * 2 ** Math.max(0, attempts - 1));
}

const REFUSAL_HELP: Record<string, string> = {
  DO_NOT_CONTACT: "The borrower is on the Do Not Contact list now. Visits are checked against today's list, " +
    "so this one cannot be recorded. Tell your manager if it happened before they were added.",
};

function refusal(err: unknown): ItemError {
  const code = errorCode(err);
  const message = errorDetail(err, "The server refused this record.");
  return { message: code && REFUSAL_HELP[code] ? REFUSAL_HELP[code] : message, code, status: errorStatus(err) };
}

async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function newId(): string {
  return crypto.randomUUID();
}

export interface Usage {
  pending: number;
  attention: number;
  bytes: number;
  /** Items captured under another login on this phone; never sent under this one. */
  otherLogins: number;
}

export async function usage(store: OutboxStore, who: Who | null): Promise<Usage> {
  const items = await store.list();
  const u: Usage = { pending: 0, attention: 0, bytes: 0, otherLogins: 0 };
  for (const it of items) {
    u.bytes += it.bytes;
    if (!who || it.userId !== who.userId || it.deviceId !== who.deviceId) u.otherLogins += 1;
    else if (it.state === "attention") u.attention += 1;
    else u.pending += 1;
  }
  return u;
}

async function checkRoom(store: OutboxStore, addBytes: number): Promise<void> {
  const items = await store.list();
  const bytes = items.reduce((n, it) => n + it.bytes, 0);
  if (items.length >= MAX_ITEMS || bytes + addBytes > MAX_BYTES) {
    throw new OutboxFullError(
      `This phone is holding ${items.length} unsent records (${Math.round(bytes / 1048576)} MB). ` +
      "Find signal and let them send before recording more.",
    );
  }
}

async function capture(store: OutboxStore, who: Who, capturedAt: Date, nowMs: number): Promise<OutboxCapture> {
  return {
    client_submission_id: newId(),
    captured_at: capturedAt.toISOString(),
    device_seq: await store.nextSeq(nowMs),
    device_id: who.deviceId,
  };
}

export interface NewVisit {
  caseId: string;
  caseLabel: string;
  capturedAt: Date;
  body: VisitPayload;
  photos: Array<{ subject: PhotoSubject; blob: Blob }>;
  ptp?: PtpPayload;
}

export async function enqueueVisit(
  store: OutboxStore, who: Who, v: NewVisit, nowMs = Date.now(), hash = sha256Hex,
): Promise<VisitItem> {
  const bytes = v.photos.reduce((n, p) => n + p.blob.size, 0);
  await checkRoom(store, bytes);
  const cap = await capture(store, who, v.capturedAt, nowMs);
  // The promise is part of the same submit: same moment, the next sequence number.
  const ptpCap = v.ptp ? await capture(store, who, v.capturedAt, nowMs) : undefined;
  const media: MediaPart[] = [];
  for (const p of v.photos) {
    const blobKey = `${cap.client_submission_id}:${p.subject}`;
    await store.putBlob(blobKey, p.blob);
    media.push({ subject: p.subject, blobKey, contentType: p.blob.type || "image/jpeg",
                 bytes: p.blob.size, sha256: await hash(p.blob) });
  }
  const item: VisitItem = {
    kind: "visit", id: cap.client_submission_id, userId: who.userId, deviceId: who.deviceId,
    caseId: v.caseId, caseLabel: v.caseLabel, capture: cap, state: "pending", attempts: 0,
    nextAttemptAt: 0, bytes, createdAt: nowMs, body: v.body, media,
    ptp: v.ptp && ptpCap ? { capture: ptpCap, body: v.ptp, done: false } : undefined,
  };
  await store.put(item);
  return item;
}

export async function enqueueCall(
  store: OutboxStore, who: Who,
  c: { caseId: string; caseLabel: string; capturedAt: Date; body: LogCallPayload },
  nowMs = Date.now(),
): Promise<CallItem> {
  await checkRoom(store, 0);
  const cap = await capture(store, who, c.capturedAt, nowMs);
  const item: CallItem = {
    kind: "call", id: cap.client_submission_id, userId: who.userId, deviceId: who.deviceId,
    caseId: c.caseId, caseLabel: c.caseLabel, capture: cap, state: "pending", attempts: 0,
    nextAttemptAt: 0, bytes: 0, createdAt: nowMs, body: c.body,
  };
  await store.put(item);
  return item;
}

/** Where each uploaded photo's key and hash go on the visit body. */
function photoFields(media: MediaPart[]): Partial<VisitPayload> {
  const out: Record<string, string> = {};
  for (const m of media) {
    if (!m.uploadedKey) continue;
    if (m.subject === "signature") {
      out.signature_key = m.uploadedKey;
    } else {
      out[`${m.subject}_photo_key`] = m.uploadedKey;
      out[`${m.subject}_photo_sha256`] = m.sha256;
    }
  }
  return out as Partial<VisitPayload>;
}

/**
 * Send one item, step by step. Progress is saved after every step, so a retry
 * resumes where the last attempt stopped and never repeats an acknowledged step.
 * Throws the failing call's error.
 */
export async function sendItem(
  item: OutboxItem, store: Pick<OutboxStore, "getBlob">, api: OutboxApi,
  save: (item: OutboxItem) => Promise<void>,
): Promise<void> {
  if (item.kind === "call") {
    await api.logCall(item.caseId, { ...item.body, ...item.capture });
    return;
  }
  for (const m of item.media) {
    if (m.uploadedKey) continue;
    const blob = await store.getBlob(m.blobKey);
    if (!blob) throw Object.assign(new Error("A photo for this visit is missing from the phone."),
                                   { response: { status: 410, data: { detail: "A photo for this visit is missing from the phone." } } });
    const { upload_url, key } = await api.photoUploadUrl(item.caseId, m.subject, item.capture);
    await api.putObject(upload_url, blob, m.contentType);
    m.uploadedKey = key;
    await save(item);
  }
  if (!item.visitId) {
    const res = await api.recordVisit(item.caseId, { ...item.body, ...photoFields(item.media), ...item.capture });
    item.visitId = res.id;
    await save(item);
    const a = item.body.agent_recording_key, b = item.body.borrower_recording_key;
    const recorder = a && b ? "both" : a ? "agent" : b ? "borrower" : null;
    if (recorder && api.transcribe) api.transcribe(res.id, recorder).catch(() => {});
  }
  if (item.ptp && !item.ptp.done) {
    await api.setPTP(item.caseId, { ...item.ptp.body, ...item.ptp.capture });
    item.ptp.done = true;
    await save(item);
  }
}

async function removeItem(store: OutboxStore, item: OutboxItem): Promise<void> {
  if (item.kind === "visit") for (const m of item.media) await store.removeBlob(m.blobKey);
  await store.remove(item.id);
}

export interface FlushReport {
  sent: string[];
  refused: string[];
  /** Something is still waiting for signal (or its backoff). */
  waiting: boolean;
}

/**
 * Send this login's pending items in capture order. A retryable failure stops
 * the flush (order is what the server's sequence check relies on); a refusal
 * parks the item for the agent and moves on.
 */
export async function flush(store: OutboxStore, api: OutboxApi, who: Who, nowMs = Date.now()): Promise<FlushReport> {
  const report: FlushReport = { sent: [], refused: [], waiting: false };
  const mine = (await store.list())
    .filter((it) => it.userId === who.userId && it.deviceId === who.deviceId && it.state === "pending")
    .sort((a, b) => a.capture.device_seq - b.capture.device_seq);
  for (const item of mine) {
    if (item.nextAttemptAt > nowMs) {
      report.waiting = true;
      break;
    }
    try {
      await sendItem(item, store, api, (it) => store.put(it));
      await removeItem(store, item);
      report.sent.push(item.id);
    } catch (err) {
      if (classify(err) === "retry") {
        item.attempts += 1;
        item.nextAttemptAt = nowMs + backoffMs(item.attempts);
        await store.put(item);
        report.waiting = true;
        break;
      }
      item.state = "attention";
      item.error = refusal(err);
      // A refused visit's photos can never be delivered; keep the record, free the space.
      if (item.kind === "visit") {
        for (const m of item.media) await store.removeBlob(m.blobKey);
        item.bytes = 0;
      }
      await store.put(item);
      report.refused.push(item.id);
    }
  }
  return report;
}

/** The agent's decision on a refused item, or a live submit taking its record back. */
export async function discard(store: OutboxStore, id: string): Promise<void> {
  const item = await store.get(id);
  if (item) await removeItem(store, item);
}

/** The in-memory store: tests, and the fallback when IndexedDB is unavailable. */
export class MemoryOutboxStore implements OutboxStore {
  private items = new Map<string, OutboxItem>();
  private blobs = new Map<string, Blob>();
  private lastSeq = 0;

  async nextSeq(nowMs: number): Promise<number> {
    this.lastSeq = nextSeqAfter(this.lastSeq, nowMs);
    return this.lastSeq;
  }
  async put(item: OutboxItem): Promise<void> {
    this.items.set(item.id, structuredClone(item));
  }
  async get(id: string): Promise<OutboxItem | undefined> {
    const it = this.items.get(id);
    return it ? structuredClone(it) : undefined;
  }
  async list(): Promise<OutboxItem[]> {
    return [...this.items.values()].map((it) => structuredClone(it));
  }
  async remove(id: string): Promise<void> {
    this.items.delete(id);
  }
  async putBlob(key: string, blob: Blob): Promise<void> {
    this.blobs.set(key, blob);
  }
  async getBlob(key: string): Promise<Blob | undefined> {
    return this.blobs.get(key);
  }
  async removeBlob(key: string): Promise<void> {
    this.blobs.delete(key);
  }
  get blobCount(): number {
    return this.blobs.size;
  }
}
