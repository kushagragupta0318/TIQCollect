/**
 * I02 — the offline outbox engine (lib/outbox.ts): capture stamping, replay
 * order, resumable steps, retry versus refusal, caps and login isolation.
 */
import { describe, expect, it, vi } from "vitest";
import {
  MAX_ITEMS, MemoryOutboxStore, OutboxFullError, backoffMs, classify, discard, enqueueCall, enqueueVisit,
  flush, nextSeqAfter, usage, type OutboxApi, type VisitItem,
} from "./outbox";
import type { VisitPayload } from "@/api/agent";

const WHO = { userId: "user-a", deviceId: "phone-a" };
const T0 = Date.UTC(2026, 8, 29, 7, 0);          // 12:30 IST
const hash = async () => "f".repeat(64);

const body: VisitPayload = {
  check_in_latitude: 28.6315, check_in_longitude: 77.2167, customer_met: false, outcome: "NOT_AVAILABLE",
};

function httpError(status: number, detail = "no", code?: string) {
  return Object.assign(new Error(detail), { response: { status, data: { detail, code } } });
}

function fakeApi(overrides: Partial<OutboxApi> = {}) {
  const calls: string[] = [];
  let n = 0;
  const api: OutboxApi = {
    photoUploadUrl: vi.fn(async (_c, subject) => {
      calls.push(`url:${subject}`);
      return { upload_url: `https://minio.test/${subject}`, key: `k/${subject}` };
    }),
    putObject: vi.fn(async (url: string) => { calls.push(`put:${url.split("/").pop()}`); }),
    recordVisit: vi.fn(async () => { calls.push("visit"); return { id: `visit-${++n}` }; }),
    setPTP: vi.fn(async () => { calls.push("ptp"); return {}; }),
    logCall: vi.fn(async () => { calls.push("call"); return {}; }),
    ...overrides,
  };
  return { api, calls };
}

async function visit(store: MemoryOutboxStore, opts: { photos?: number; ptp?: boolean; who?: typeof WHO; at?: number } = {}) {
  const photos = Array.from({ length: opts.photos ?? 0 }, (_, i) => ({
    subject: (["agent", "borrower", "signature"] as const)[i],
    blob: new Blob([new Uint8Array(1000)], { type: "image/jpeg" }),
  }));
  return enqueueVisit(store, opts.who ?? WHO, {
    caseId: "case-1", caseLabel: "C-A1", capturedAt: new Date(opts.at ?? T0), body, photos,
    ptp: opts.ptp ? { committed_amount: 5000, committed_date: "2026-10-04" } : undefined,
  }, opts.at ?? T0, hash);
}

describe("capture stamping", () => {
  it("stamps a submission id, the capture time, the device and increasing sequence numbers", async () => {
    const store = new MemoryOutboxStore();
    const a = await visit(store, { ptp: true });
    const b = await visit(store);
    expect(a.capture).toMatchObject({ captured_at: new Date(T0).toISOString(), device_id: "phone-a" });
    expect(a.ptp!.capture.captured_at).toBe(a.capture.captured_at);
    expect(a.ptp!.capture.device_seq).toBeGreaterThan(a.capture.device_seq);
    expect(b.capture.device_seq).toBeGreaterThan(a.ptp!.capture.device_seq);
    expect(new Set([a.id, a.ptp!.capture.client_submission_id, b.id]).size).toBe(3);
  });

  it("never lets the sequence fall behind the clock or go backwards", () => {
    expect(nextSeqAfter(0, 1_700_000_000_000)).toBe(1_700_000_000_000);   // fresh store: the clock
    expect(nextSeqAfter(1_700_000_000_005, 1_700_000_000_000)).toBe(1_700_000_000_006);   // clock set back
  });
});

describe("flush", () => {
  it("uploads photos, then the visit with their keys and hashes, then the promise, then forgets it", async () => {
    const store = new MemoryOutboxStore();
    const item = await visit(store, { photos: 3, ptp: true });
    const { api, calls } = fakeApi();
    const report = await flush(store, api, WHO, T0);
    expect(report).toEqual({ sent: [item.id], refused: [], waiting: false });
    expect(calls).toEqual(["url:agent", "put:agent", "url:borrower", "put:borrower", "url:signature", "put:signature", "visit", "ptp"]);
    expect(api.photoUploadUrl).toHaveBeenCalledWith("case-1", "agent", item.capture);
    const sent = vi.mocked(api.recordVisit).mock.calls[0][1];
    expect(sent).toMatchObject({ ...item.capture, agent_photo_key: "k/agent", agent_photo_sha256: "f".repeat(64),
                                 borrower_photo_key: "k/borrower", signature_key: "k/signature" });
    expect(sent).not.toHaveProperty("signature_photo_key");
    expect(vi.mocked(api.setPTP).mock.calls[0][1]).toMatchObject(item.ptp!.capture);
    expect(await store.list()).toEqual([]);
    expect(store.blobCount).toBe(0);
  });

  it("resumes after a network failure without repeating an acknowledged step", async () => {
    const store = new MemoryOutboxStore();
    await visit(store, { photos: 1, ptp: true });
    let fail = true;
    const { api, calls } = fakeApi({
      setPTP: vi.fn(async () => { if (fail) throw new Error("Network Error"); calls.push("ptp"); return {}; }),
    });
    const first = await flush(store, api, WHO, T0);
    expect(first.waiting).toBe(true);
    const [kept] = (await store.list()) as VisitItem[];
    expect(kept).toMatchObject({ state: "pending", attempts: 1, visitId: "visit-1" });
    expect(kept.nextAttemptAt).toBe(T0 + backoffMs(1));
    expect(await flush(store, api, WHO, T0 + 1000)).toMatchObject({ sent: [], waiting: true });   // still backing off
    fail = false;
    await flush(store, api, WHO, T0 + backoffMs(1));
    expect(calls.filter((c) => c === "visit")).toHaveLength(1);
    expect(calls.filter((c) => c.startsWith("put:"))).toHaveLength(1);
    expect(await store.list()).toEqual([]);
  });

  it("keeps capture order: a waiting item holds back the ones after it", async () => {
    const store = new MemoryOutboxStore();
    await visit(store);
    await visit(store);
    const { api } = fakeApi({ recordVisit: vi.fn(async () => { throw httpError(503); }) });
    await flush(store, api, WHO, T0);
    expect(api.recordVisit).toHaveBeenCalledTimes(1);
    expect(await store.list()).toHaveLength(2);
  });

  it("parks a refused item for the agent, frees its photos, and sends the next one", async () => {
    const store = new MemoryOutboxStore();
    const bad = await visit(store, { photos: 1 });
    const good = await visit(store);
    const recordVisit = vi.fn()
      .mockRejectedValueOnce(httpError(403, "Visits can only be recorded between 8:00 and 19:00 IST"))
      .mockResolvedValueOnce({ id: "v2" });
    const { api } = fakeApi({ recordVisit });
    const report = await flush(store, api, WHO, T0);
    expect(report).toEqual({ sent: [good.id], refused: [bad.id], waiting: false });
    const [parked] = await store.list();
    expect(parked).toMatchObject({ id: bad.id, state: "attention", bytes: 0,
                                   error: { status: 403, message: expect.stringContaining("19:00") } });
    expect(store.blobCount).toBe(0);
    await flush(store, api, WHO, T0 + 60_000);
    expect(recordVisit).toHaveBeenCalledTimes(2);     // a parked item is never retried
  });

  it("explains a Do Not Contact refusal instead of just quoting it", async () => {
    const store = new MemoryOutboxStore();
    await visit(store);
    const { api } = fakeApi({ recordVisit: vi.fn(async () => { throw httpError(403, "Customer is marked Do Not Contact", "DO_NOT_CONTACT"); }) });
    await flush(store, api, WHO, T0);
    const [parked] = await store.list();
    expect(parked.error!.message).toMatch(/today's list/);
  });

  it("never sends another login's records", async () => {
    const store = new MemoryOutboxStore();
    await visit(store, { who: { userId: "user-b", deviceId: "phone-a" } });
    await visit(store);
    const { api } = fakeApi();
    await flush(store, api, WHO, T0);
    expect(api.recordVisit).toHaveBeenCalledTimes(1);
    expect(await usage(store, WHO)).toMatchObject({ pending: 0, otherLogins: 1 });
  });

  it("sends a call log with its capture", async () => {
    const store = new MemoryOutboxStore();
    const c = await enqueueCall(store, WHO, { caseId: "case-1", caseLabel: "C-A1", capturedAt: new Date(T0),
                                             body: { outcome: "NO_ANSWER" } }, T0);
    const { api } = fakeApi();
    await flush(store, api, WHO, T0);
    expect(api.logCall).toHaveBeenCalledWith("case-1", { outcome: "NO_ANSWER", ...c.capture });
  });
});

describe("limits and classification", () => {
  it("refuses a capture beyond the item cap", async () => {
    const store = new MemoryOutboxStore();
    for (let i = 0; i < MAX_ITEMS; i++) await visit(store);
    await expect(visit(store)).rejects.toBeInstanceOf(OutboxFullError);
  });

  it("retries what may succeed later and parks what will not", () => {
    for (const s of [undefined, 500, 502, 503, 408, 429, 401]) {
      expect(classify(s === undefined ? new Error("Network Error") : httpError(s))).toBe("retry");
    }
    for (const s of [400, 403, 404, 409, 410, 422]) expect(classify(httpError(s))).toBe("refused");
  });

  it("backs off exponentially up to five minutes", () => {
    expect([1, 2, 3, 10].map(backoffMs)).toEqual([5_000, 10_000, 20_000, 300_000]);
  });

  it("discards a record and its photos", async () => {
    const store = new MemoryOutboxStore();
    const item = await visit(store, { photos: 2 });
    await discard(store, item.id);
    expect(await store.list()).toEqual([]);
    expect(store.blobCount).toBe(0);
  });
});
