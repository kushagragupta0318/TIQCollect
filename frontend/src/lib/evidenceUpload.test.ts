import { describe, expect, it, vi } from "vitest";
import { failedLabels, keyOf, uploadEvidence, type UploadIo, type UploadResult } from "./evidenceUpload";

const blob = (n = 10, type = "image/jpeg") => new Blob([new Uint8Array(n)], { type });

function io(over: Partial<UploadIo> = {}): UploadIo & { puts: string[] } {
  const puts: string[] = [];
  return {
    puts,
    presign: vi.fn(async () => ({ upload_url: "https://store.test/put/abc", key: "collections/2026/09/abcd1234/AGENT_SELFIE_1.jpg" })),
    put: vi.fn(async (url: string) => { puts.push(url); return { ok: true, status: 200 }; }),
    ...over,
  };
}

describe("uploadEvidence", () => {
  it("reports nothing captured as none, and touches no network", async () => {
    const i = io();
    expect(await uploadEvidence(i, null)).toEqual({ status: "none" });
    expect(i.presign).not.toHaveBeenCalled();
    expect(i.put).not.toHaveBeenCalled();
  });

  it("returns the key and the SHA-256 of the bytes it sent", async () => {
    const r = await uploadEvidence(io(), blob(3));
    expect(r.status).toBe("uploaded");
    if (r.status === "uploaded") {
      expect(r.key).toBe("collections/2026/09/abcd1234/AGENT_SELFIE_1.jpg");
      // SHA-256 of three zero bytes.
      expect(r.sha256).toBe("709e80c88487a2411e1ee4dfb9f22a861492d20c4765150c0c794abd70f8147c");
      expect(r.contentType).toBe("image/jpeg");
    }
  });

  it("is a failure when the store answers with an error status: the PUT's answer is read", async () => {
    const r = await uploadEvidence(io({ put: vi.fn(async () => ({ ok: false, status: 403 })) }), blob());
    expect(r).toEqual({ status: "failed", message: "The storage service refused the upload (HTTP 403)." });
  });

  it("is a failure when no presigned URL can be had, and says the server's own reason", async () => {
    const err = Object.assign(new Error("500"), { response: { status: 500, data: { detail: "Storage is down" } } });
    const r = await uploadEvidence(io({ presign: vi.fn(async () => { throw err; }) }), blob());
    expect(r).toEqual({ status: "failed", message: "Storage is down" });
  });

  it("is a failure when the PUT never gets an answer (offline, timeout)", async () => {
    const r = await uploadEvidence(io({ put: vi.fn(async () => { throw new TypeError("Failed to fetch"); }) }), blob());
    expect(r.status).toBe("failed");
  });

  it("sends the content type the caller names, else the blob's, else JPEG", async () => {
    const sent: string[] = [];
    const put: UploadIo["put"] = async (...args) => { sent.push(args[2]); return { ok: true, status: 200 }; };
    await uploadEvidence(io({ put }), blob(2, "audio/webm"), "audio/webm");
    await uploadEvidence(io({ put }), blob(2, "image/png"));
    await uploadEvidence(io({ put }), blob(2, ""));
    expect(sent).toEqual(["audio/webm", "image/png", "image/jpeg"]);
  });
});

describe("failedLabels and keyOf", () => {
  const up: UploadResult = { status: "uploaded", key: "k", sha256: "s", contentType: "image/jpeg" };
  const bad: UploadResult = { status: "failed", message: "x" };
  const none: UploadResult = { status: "none" };

  it("names only what failed: a photo never taken is not a failure", () => {
    expect(failedLabels([["Agent photo", up], ["Borrower photo", bad], ["Signature", none], ["Receipt", bad]]))
      .toEqual(["Borrower photo", "Receipt"]);
  });

  it("gives a key only for a completed upload", () => {
    expect([keyOf(up), keyOf(bad), keyOf(none)]).toEqual(["k", undefined, undefined]);
  });
});
