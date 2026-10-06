// One captured file to the object store, with the outcome said out loud.
// The page used to swallow every error and never read the PUT's status, so a
// photo the store refused was recorded as uploaded (N1, docs/business/PRIORITIES.md).
import { errorDetail } from "@/lib/apiError";
import { sha256Hex } from "@/lib/sha256";

export type UploadResult =
  | { status: "none" }                                   // nothing was captured
  | { status: "uploaded"; key: string; sha256: string; contentType: string }
  | { status: "failed"; message: string };

export interface UploadIo {
  presign(): Promise<{ upload_url: string; key: string }>;
  /** The store's answer. Rejects only when no answer came back at all. */
  put(url: string, blob: Blob, contentType: string): Promise<{ ok: boolean; status: number }>;
}

export async function putWithFetch(url: string, blob: Blob, contentType: string): Promise<{ ok: boolean; status: number }> {
  const res = await fetch(url, { method: "PUT", body: blob, headers: { "Content-Type": contentType } });
  return { ok: res.ok, status: res.status };
}

export async function uploadEvidence(io: UploadIo, blob: Blob | null | undefined, contentType?: string): Promise<UploadResult> {
  if (!blob) return { status: "none" };
  try {
    const { upload_url, key } = await io.presign();
    const type = contentType ?? (blob.type || "image/jpeg");
    const [answer, sha256] = await Promise.all([io.put(upload_url, blob, type), sha256Hex(blob)]);
    if (!answer.ok) {
      return { status: "failed", message: `The storage service refused the upload (HTTP ${answer.status}).` };
    }
    return { status: "uploaded", key, sha256, contentType: type };
  } catch (err) {
    return { status: "failed", message: errorDetail(err, "The upload did not reach the server.") };
  }
}

/** What did not reach storage, by label; empty when everything captured did. */
export function failedLabels(results: ReadonlyArray<readonly [string, UploadResult]>): string[] {
  return results.filter(([, r]) => r.status === "failed").map(([label]) => label);
}

/** The key of an upload, or undefined when nothing was captured or it failed. */
export function keyOf(r: UploadResult): string | undefined {
  return r.status === "uploaded" ? r.key : undefined;
}
