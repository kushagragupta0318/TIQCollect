/**
 * Live field events over Server-Sent Events — the client half of
 * backend/app/api/v1/endpoints/events.py.
 *
 * 2026-09-24 (standalone plan, P0-06/P0-07). Why not `EventSource`: it cannot
 * send an Authorization header, and the alternative — the access token in the
 * query string — writes a bearer credential into every proxy and access log on
 * the way. `fetch` can send the header and read the body as a stream, so the
 * SSE framing is parsed here instead (it is four lines of spec).
 *
 * Behaviour, in the order a manager's tab lives through it:
 *   - connect; each `data:` line becomes one LiveEvent;
 *   - the server closes the stream every 10 minutes on purpose (a revoked
 *     session must not listen forever) → reconnect at once, with whatever
 *     token is current by then;
 *   - network error → back off 1, 2, 4 … 15 s and retry;
 *   - 503 (Redis down on the server) → poll /events/recent instead, and try
 *     the stream again every minute;
 *   - 401 → ask `onUnauthorized` to refresh, then retry; stop if it cannot.
 * Events are de-duplicated by id, because a reconnect and a poll can both
 * deliver the same one.
 */

export interface LiveEvent {
  id: string;
  type: string;
  at: string;
  agent_id: string | null;
  agent_name: string | null;
  data: Record<string, unknown>;
}

export type StreamStatus = "connecting" | "live" | "polling" | "closed";

export interface SubscribeOptions {
  /** Current access token, read at every (re)connect. */
  getToken: () => string | null;
  onEvent: (e: LiveEvent) => void;
  onStatus?: (s: StreamStatus) => void;
  /** Called on 401; resolve true once a fresh token is available. */
  onUnauthorized?: () => Promise<boolean>;
  baseUrl?: string;
  pollMs?: number;
}

/**
 * Parse one SSE block ("event: …\ndata: …") into a LiveEvent, or null for a
 * comment/heartbeat/retry block or anything malformed. Exported for tests.
 */
export function parseSseBlock(block: string): LiveEvent | null {
  const data: string[] = [];
  for (const raw of block.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (data.length === 0) return null;
  try {
    const obj = JSON.parse(data.join("\n")) as Partial<LiveEvent>;
    if (typeof obj.id !== "string" || typeof obj.type !== "string") return null;
    return {
      id: obj.id,
      type: obj.type,
      at: typeof obj.at === "string" ? obj.at : new Date().toISOString(),
      agent_id: obj.agent_id ?? null,
      agent_name: obj.agent_name ?? null,
      data: (obj.data as Record<string, unknown>) ?? {},
    };
  } catch {
    return null;
  }
}

/** Split a growing buffer into complete SSE blocks and the unfinished tail. */
export function splitSseBuffer(buffer: string): { blocks: string[]; rest: string } {
  const normalised = buffer.replace(/\r\n/g, "\n");
  const parts = normalised.split("\n\n");
  const rest = parts.pop() ?? "";
  return { blocks: parts, rest };
}

const SEEN_MAX = 500;

export function subscribeEvents(opts: SubscribeOptions): () => void {
  const base = opts.baseUrl ?? "/api/v1";
  const pollMs = opts.pollMs ?? 5_000;
  let stopped = false;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let backoff = 1_000;
  const seen = new Set<string>();

  const status = (s: StreamStatus) => opts.onStatus?.(s);

  function emit(e: LiveEvent) {
    if (seen.has(e.id)) return;
    seen.add(e.id);
    if (seen.size > SEEN_MAX) {
      const first = seen.values().next().value;
      if (first !== undefined) seen.delete(first);
    }
    try {
      opts.onEvent(e);
    } catch {
      /* a listener's bug must not kill the stream */
    }
  }

  function later(fn: () => void, ms: number) {
    if (stopped) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(fn, ms);
  }

  function authHeaders(): Record<string, string> | null {
    const t = opts.getToken();
    return t ? { Authorization: `Bearer ${t}` } : null;
  }

  async function pollOnce(): Promise<void> {
    const headers = authHeaders();
    if (!headers) return;
    try {
      const r = await fetch(`${base}/events/recent?limit=50`, { headers });
      if (!r.ok) return;
      const body = (await r.json()) as { events?: LiveEvent[] };
      // Oldest first, so listeners see them in the order they happened.
      for (const e of [...(body.events ?? [])].reverse()) emit(e);
    } catch {
      /* offline — the next tick tries again */
    }
  }

  function startPolling(since: number) {
    status("polling");
    const tick = async () => {
      if (stopped) return;
      await pollOnce();
      // Retry the real stream every minute; poll in between.
      if (Date.now() - since > 60_000) void connect();
      else later(tick, pollMs);
    };
    void tick();
  }

  async function connect(): Promise<void> {
    if (stopped) return;
    const headers = authHeaders();
    if (!headers) {
      status("closed");
      later(() => void connect(), 5_000); // not logged in yet (simulator frame loading)
      return;
    }
    status("connecting");
    controller = new AbortController();
    try {
      const res = await fetch(`${base}/events/stream`, {
        headers: { ...headers, Accept: "text/event-stream" },
        signal: controller.signal,
        cache: "no-store",
      });
      if (res.status === 401) {
        const ok = opts.onUnauthorized ? await opts.onUnauthorized() : false;
        if (ok) later(() => void connect(), 0);
        else { status("closed"); later(() => void connect(), 15_000); }
        return;
      }
      if (res.status === 503) {
        startPolling(Date.now());
        return;
      }
      if (!res.ok || !res.body) throw new Error(`stream HTTP ${res.status}`);

      status("live");
      backoff = 1_000;
      // Catch up on anything published while we were disconnected.
      void pollOnce();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { blocks, rest } = splitSseBuffer(buffer);
        buffer = rest;
        for (const b of blocks) {
          const e = parseSseBlock(b);
          if (e) emit(e);
        }
      }
      // Server closed it (the deliberate 10-minute cycle): reconnect now.
      later(() => void connect(), 0);
    } catch {
      if (stopped) return;
      status("connecting");
      later(() => void connect(), backoff);
      backoff = Math.min(backoff * 2, 15_000);
    }
  }

  void connect();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    controller?.abort();
    status("closed");
  };
}
