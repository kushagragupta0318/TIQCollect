/**
 * One way to read an error out of an API call.
 *
 * 2026-09-07 — this shape was written out ten times across six files, always as
 * `catch (err: any) { const detail = err?.response?.data?.detail; ... }`. Each
 * copy needed `any` because `catch` gives you `unknown` and the axios error
 * shape was never named, so every one of them was a lint error and a small
 * opportunity to get the fallback wrong.
 *
 * The backend's contract is fixed and worth relying on: main.py maps every
 * AppException to `{detail, code}`, so `detail` is either a string or absent.
 * A validation error from FastAPI puts a LIST there instead, which is why the
 * string check below is not defensive padding — `String(detail)` on that list
 * would show a borrower-facing toast reading "[object Object]".
 */

/** The `{detail, code}` body main.py returns for a handled AppException. */
interface ApiErrorBody {
  detail?: unknown;
  code?: unknown;
}

function body(err: unknown): ApiErrorBody | undefined {
  if (typeof err !== "object" || err === null) return undefined;
  const response = (err as { response?: unknown }).response;
  if (typeof response !== "object" || response === null) return undefined;
  const data = (response as { data?: unknown }).data;
  if (typeof data !== "object" || data === null) return undefined;
  return data as ApiErrorBody;
}

/**
 * The server's own message, or `fallback` when there isn't a usable one.
 *
 * Never returns a stringified object: FastAPI's own 422 puts an array of field
 * errors in `detail`, and showing that to a field agent standing in front of a
 * borrower is worse than showing nothing.
 */
export function errorDetail(err: unknown, fallback: string): string {
  const detail = body(err)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}

/** The typed `ErrorCode` the backend attaches, when it attached one. */
export function errorCode(err: unknown): string | undefined {
  const code = body(err)?.code;
  return typeof code === "string" ? code : undefined;
}

/** HTTP status, for the few places that branch on 404 vs 409 vs the rest. */
export function errorStatus(err: unknown): number | undefined {
  if (typeof err !== "object" || err === null) return undefined;
  const response = (err as { response?: unknown }).response;
  if (typeof response !== "object" || response === null) return undefined;
  const status = (response as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}
