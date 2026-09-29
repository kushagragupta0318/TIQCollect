/**
 * IndexedDB storage for the offline outbox (lib/outbox.ts). Thin on purpose:
 * every decision lives in outbox.ts, which is tested against the in-memory
 * store; this file only moves records in and out.
 *
 * One database per session slot (lib/sessionSlot.ts), so the simulator's two
 * frames never share a queue or a sequence counter (P0-A6). Items carry their
 * login and are only ever sent under it. Version 2 adds `reads`, the offline
 * read cache (lib/readCache.ts), in the same slot database.
 */
import { slotKey } from "@/lib/sessionSlot";
import { nextSeqAfter, type OutboxItem, type OutboxStore } from "@/lib/outbox";
import type { ReadEntry, ReadStore } from "@/lib/readCache";

const DB_NAME = slotKey("tiq-outbox");
const VERSION = 2;
const ITEMS = "items";
const BLOBS = "blobs";
const META = "meta";
const READS = "reads";

function req<T>(r: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB_NAME, VERSION);
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains(ITEMS)) db.createObjectStore(ITEMS, { keyPath: "id" });
      if (!db.objectStoreNames.contains(BLOBS)) db.createObjectStore(BLOBS);
      if (!db.objectStoreNames.contains(META)) db.createObjectStore(META);
      if (!db.objectStoreNames.contains(READS)) db.createObjectStore(READS);
    };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

let shared: Promise<IDBDatabase> | null = null;
function connection(): Promise<IDBDatabase> {
  shared ??= open();
  return shared;
}

export class IdbOutboxStore implements OutboxStore {
  private conn(): Promise<IDBDatabase> {
    return connection();
  }

  private async run<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const tx = (await this.conn()).transaction(store, mode);
    const result = req(fn(tx.objectStore(store)));
    await done(tx);
    return result;
  }

  async nextSeq(nowMs: number): Promise<number> {
    // Read and write in ONE transaction: two tabs of a slot never get the same number.
    const tx = (await this.conn()).transaction(META, "readwrite");
    const meta = tx.objectStore(META);
    const last = ((await req(meta.get("seq"))) as number | undefined) ?? 0;
    const seq = nextSeqAfter(last, nowMs);
    meta.put(seq, "seq");
    await done(tx);
    return seq;
  }

  async put(item: OutboxItem): Promise<void> {
    await this.run(ITEMS, "readwrite", (s) => s.put(item));
  }

  async get(id: string): Promise<OutboxItem | undefined> {
    return this.run(ITEMS, "readonly", (s) => s.get(id) as IDBRequest<OutboxItem | undefined>);
  }

  async list(): Promise<OutboxItem[]> {
    return this.run(ITEMS, "readonly", (s) => s.getAll() as IDBRequest<OutboxItem[]>);
  }

  async remove(id: string): Promise<void> {
    await this.run(ITEMS, "readwrite", (s) => s.delete(id));
  }

  async putBlob(key: string, blob: Blob): Promise<void> {
    await this.run(BLOBS, "readwrite", (s) => s.put(blob, key));
  }

  async getBlob(key: string): Promise<Blob | undefined> {
    return this.run(BLOBS, "readonly", (s) => s.get(key) as IDBRequest<Blob | undefined>);
  }

  async removeBlob(key: string): Promise<void> {
    await this.run(BLOBS, "readwrite", (s) => s.delete(key));
  }
}

export class IdbReadStore implements ReadStore {
  private async run<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const tx = (await connection()).transaction(READS, mode);
    const result = req(fn(tx.objectStore(READS)));
    await done(tx);
    return result;
  }

  get(key: string): Promise<ReadEntry | undefined> {
    return this.run("readonly", (s) => s.get(key) as IDBRequest<ReadEntry | undefined>);
  }

  async put(key: string, entry: ReadEntry): Promise<void> {
    await this.run("readwrite", (s) => s.put(entry, key));
  }

  async remove(key: string): Promise<void> {
    await this.run("readwrite", (s) => s.delete(key));
  }

  async clear(): Promise<void> {
    await this.run("readwrite", (s) => s.clear());
  }
}
