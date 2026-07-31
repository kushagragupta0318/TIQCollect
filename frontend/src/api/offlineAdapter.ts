// ─── Offline snapshot adapter ────────────────────────────────────────────────
// 2026-07-30 — collection_dashboard: lets the app run with NO backend at all.
//   Used only by the `offline` build mode (see .env.offline), which is what the
//   Collections Command Center embeds so its Field Recovery → agency click can
//   open the manager analytics view without anyone starting this project's
//   frontend or backend. Normal dev/build never loads this file's behaviour:
//   axios.ts installs the adapter only when VITE_OFFLINE_SNAPSHOTS is set.
//
//   Responses are replayed from public/offline/snapshots.json, captured from a
//   live backend by scripts/capture-offline-snapshots.py. Reads are served from
//   the bundle; writes are refused with a clear message (nothing to write to).
// ─────────────────────────────────────────────────────────────────────────────
import { AxiosError, AxiosHeaders } from "axios";
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios";

type Bundle = Record<string, unknown>;

let bundle: Bundle | null = null;
let inflight: Promise<Bundle> | null = null;

// Resolved against the document, so the same build works at "/" or under any
// sub-path (the Command Center serves it from /tiqcollect-offline/).
const BUNDLE_URL = () => new URL("offline/snapshots.json", document.baseURI).href;

function loadBundle(): Promise<Bundle> {
  if (bundle) return Promise.resolve(bundle);
  if (!inflight) {
    inflight = fetch(BUNDLE_URL())
      .then((res) => {
        if (!res.ok) throw new Error(`snapshot bundle HTTP ${res.status}`);
        return res.json() as Promise<Bundle>;
      })
      .then((json) => (bundle = json));
  }
  return inflight;
}

// Must match the key format written by scripts/capture-offline-snapshots.py:
// "<METHOD> <path>" with query params sorted by name.
function queryOf(params: unknown): string {
  if (!params || typeof params !== "object") return "";
  const entries = Object.entries(params as Record<string, unknown>)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  if (!entries.length) return "";
  return (
    "?" +
    entries
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join("&")
  );
}

// Most specific first. Dropping agent_id lets a per-agent AI report fall back to
// the agency-wide one for that month rather than showing nothing.
function candidateKeys(method: string, path: string, params: unknown): string[] {
  const keys = [`${method} ${path}${queryOf(params)}`];
  if (params && typeof params === "object" && "agent_id" in (params as object)) {
    const { agent_id: _drop, ...rest } = params as Record<string, unknown>;
    keys.push(`${method} ${path}${queryOf(rest)}`);
  }
  keys.push(`${method} ${path}`);
  return keys;
}

function respond(data: unknown, config: InternalAxiosRequestConfig): AxiosResponse {
  return {
    data,
    status: 200,
    statusText: "OK",
    headers: new AxiosHeaders(),
    config,
    request: { offline: true },
  };
}

function fail(
  message: string,
  status: number,
  config: InternalAxiosRequestConfig
): AxiosError {
  return new AxiosError(
    message,
    status === 404 ? AxiosError.ERR_BAD_REQUEST : AxiosError.ERR_BAD_RESPONSE,
    config,
    { offline: true },
    {
      data: { detail: message },
      status,
      statusText: status === 404 ? "Not Found" : "Not Implemented",
      headers: new AxiosHeaders(),
      config,
      request: { offline: true },
    }
  );
}

export const offlineAdapter: AxiosAdapter = async (config) => {
  const method = (config.method ?? "get").toUpperCase();
  const path = (config.url ?? "").replace(/\?.*$/, "");
  const data = await loadBundle();

  // Session bootstrap: the captured login response carries the manager's role
  // and name, so ProtectedRoute and the layouts behave exactly as they do live.
  if (method === "POST" && (path === "/auth/login" || path === "/auth/quick-login")) {
    const login = data["POST /auth/login"];
    if (!login) throw fail("No login snapshot in the offline bundle.", 404, config);
    return respond(login, config);
  }
  if (method === "POST" && path === "/auth/logout") {
    return respond({ success: true }, config);
  }

  if (method !== "GET") {
    throw fail(
      "This is a read-only offline build — that action needs the live backend.",
      501,
      config
    );
  }

  for (const key of candidateKeys(method, path, config.params)) {
    if (key in data) return respond(data[key], config);
  }

  throw fail(`Not captured in the offline snapshot bundle: ${method} ${path}`, 404, config);
};
