/**
 * I02 — the offline read cache (lib/readCache.ts): when a saved copy may stand
 * in for the server, and for whom.
 */
import { describe, expect, it } from "vitest";
import { MemoryReadStore, beatKey, cacheOwner, cachedRead, caseKey, mutatedCase, unreachable } from "./readCache";

const CASE = "8ba7d0eb-08e9-5234-ae3e-9407fef01280";
const T0 = Date.UTC(2026, 8, 29, 4, 30);            // 10:00 IST
const offline = () => Promise.reject(new Error("Network Error"));
const http = (status: number) => () => Promise.reject(Object.assign(new Error("x"), { response: { status, data: {} } }));

describe("cachedRead", () => {
  it("serves live data and saves it", async () => {
    const store = new MemoryReadStore();
    const r = await cachedRead(store, "u1", beatKey("u1"), async () => ({ n: 1 }), T0);
    expect(r).toEqual({ data: { n: 1 } });
    expect(store.size).toBe(1);
  });

  it("stands in for an unreachable server with the copy saved today", async () => {
    const store = new MemoryReadStore();
    await cachedRead(store, "u1", caseKey("u1", CASE), async () => ({ n: 1 }), T0);
    const later = T0 + 8 * 3600_000;                                  // 18:00 IST, same day
    expect(await cachedRead(store, "u1", caseKey("u1", CASE), offline, later)).toEqual({ data: { n: 1 }, cachedAt: T0 });
    expect(await cachedRead(store, "u1", caseKey("u1", CASE), http(503), later)).toMatchObject({ cachedAt: T0 });
  });

  it("never serves a copy past the end of its IST day", async () => {
    const store = new MemoryReadStore();
    await cachedRead(store, "u1", beatKey("u1"), async () => ({ n: 1 }), T0);
    const nextIstDay = Date.UTC(2026, 8, 29, 18, 31);                // 00:01 IST on the 30th
    await expect(cachedRead(store, "u1", beatKey("u1"), offline, nextIstDay)).rejects.toThrow("Network Error");
  });

  it("never answers for the server when it DID answer: 401, 403, 404 pass through", async () => {
    const store = new MemoryReadStore();
    await cachedRead(store, "u1", caseKey("u1", CASE), async () => ({ n: 1 }), T0);
    for (const s of [401, 403, 404]) {
      await expect(cachedRead(store, "u1", caseKey("u1", CASE), http(s), T0)).rejects.toMatchObject({ response: { status: s } });
    }
  });

  it("never serves another login's copy", async () => {
    const store = new MemoryReadStore();
    await cachedRead(store, "u1", beatKey("u1"), async () => ({ n: 1 }), T0);
    await expect(cachedRead(store, "u2", beatKey("u2"), offline, T0)).rejects.toThrow();
    // Even under a colliding key, the entry names its owner.
    await store.put(beatKey("u2"), { ...(await store.get(beatKey("u1")))!, userId: "u1" });
    await expect(cachedRead(store, "u2", beatKey("u2"), offline, T0)).rejects.toThrow();
  });

  it("saves nothing and serves nothing without a login", async () => {
    const store = new MemoryReadStore();
    await cachedRead(store, null, null, async () => ({ n: 1 }), T0);
    expect(store.size).toBe(0);
    await expect(cachedRead(store, null, null, offline, T0)).rejects.toThrow();
  });
});

describe("invalidation", () => {
  it("names the case a successful mutation changed", () => {
    expect(mutatedCase("post", `/agent/cases/${CASE}/visit`)).toBe(CASE);
    expect(mutatedCase("patch", `/agent/cases/${CASE}`)).toBe(CASE);
    expect(mutatedCase("get", `/agent/cases/${CASE}`)).toBeNull();
    expect(mutatedCase("post", "/agent/beat/reoptimize")).toBeNull();
    expect(mutatedCase("post", `/agent/cases/not-a-uuid/visit`)).toBeNull();
  });

  it("treats no response and 5xx as unreachable, nothing else", () => {
    expect(unreachable(new Error("Network Error"))).toBe(true);
    expect(unreachable({ response: { status: 502 } })).toBe(true);
    expect(unreachable({ response: { status: 404 } })).toBe(false);
  });
});

describe("cacheOwner", () => {
  const base = { isAuthenticated: true, user: { id: "u1" }, deviceId: "d1" };
  it("changes with the login, the device, the slot, and at logout", () => {
    const o = cacheOwner(base, null);
    expect(cacheOwner({ ...base, user: { id: "u2" } }, null)).not.toBe(o);
    expect(cacheOwner({ ...base, deviceId: "d2" }, null)).not.toBe(o);
    expect(cacheOwner(base, "agent")).not.toBe(o);
    expect(cacheOwner({ ...base, isAuthenticated: false }, null)).not.toBe(o);
    expect(cacheOwner({ ...base }, null)).toBe(o);
  });
});
