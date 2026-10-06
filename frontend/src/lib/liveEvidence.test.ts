import { describe, expect, it } from "vitest";
import type { UploadResult } from "./evidenceUpload";
import { liveEvidenceOutcome, type LiveEvidence } from "./liveEvidence";

const none: UploadResult = { status: "none" };
const ok = (key: string, contentType = "image/jpeg"): UploadResult => ({ status: "uploaded", key, sha256: `sha-${key}`, contentType });
const bad: UploadResult = { status: "failed", message: "The storage service refused the upload (HTTP 403)." };

const nothing: LiveEvidence = { agent: none, borrower: none, object: none, signature: none, receipt: none, documents: [] };
const cash = { cheque: false, accepted: false };

describe("liveEvidenceOutcome: stopping to ask", () => {
  it("proceeds, with nothing missing, when nothing was captured", () => {
    expect(liveEvidenceOutcome(nothing, cash)).toEqual({ proceed: true, missing: [], keys: { documents: [] } });
  });

  it("does not treat a photo that was never taken as a failure", () => {
    const r = liveEvidenceOutcome({ ...nothing, agent: ok("a") }, cash);
    expect(r.proceed).toBe(true);
    expect(r.missing).toEqual([]);
  });

  it("stops, and names the evidence, when any upload failed and the agent has not chosen to go without", () => {
    const r = liveEvidenceOutcome({ ...nothing, agent: ok("a"), borrower: bad, signature: bad }, cash);
    expect(r).toEqual({ proceed: false, missing: ["Borrower photo", "Signature"] });
    expect("keys" in r).toBe(false);          // nothing to record against
  });

  it("names the receipt photo for what it is: a cheque or a payment screenshot", () => {
    const e = { ...nothing, receipt: bad };
    expect(liveEvidenceOutcome(e, { cheque: true, accepted: false }).missing).toEqual(["Cheque photo"]);
    expect(liveEvidenceOutcome(e, { cheque: false, accepted: false }).missing).toEqual(["Payment screenshot"]);
  });

  it("names a failed document by its category", () => {
    const e = { ...nothing, documents: [{ category: "ID_PROOF" as const, result: bad }] };
    expect(liveEvidenceOutcome(e, cash).missing).toEqual(["Document: ID Proof (Aadhaar / PAN)"]);
  });
});

describe("liveEvidenceOutcome: what may be recorded", () => {
  it("hands over the keys of what uploaded", () => {
    const r = liveEvidenceOutcome({
      agent: ok("a"), borrower: ok("b"), object: ok("o"), signature: ok("s", "image/png"), receipt: ok("r"),
      documents: [{ category: "BANK_STMT", result: ok("d1", "application/pdf") }],
    }, cash);
    expect(r).toEqual({
      proceed: true, missing: [],
      keys: {
        agent: { key: "a", sha256: "sha-a" }, borrower: { key: "b", sha256: "sha-b" }, object: { key: "o", sha256: "sha-o" },
        signatureKey: "s", receiptKey: "r",
        documents: [{ category: "BANK_STMT", key: "d1", sha256: "sha-d1", content_type: "application/pdf" }],
      },
    });
  });

  it("once the agent chooses to go without, the record names only what uploaded and says what is missing", () => {
    const r = liveEvidenceOutcome({
      ...nothing, agent: ok("a"), borrower: bad, receipt: bad,
      documents: [{ category: "ID_PROOF", result: ok("d1") }, { category: "BANK_STMT", result: bad }],
    }, { cheque: true, accepted: true });
    expect(r.proceed).toBe(true);
    expect(r.missing).toEqual(["Borrower photo", "Cheque photo", "Document: Bank Statement"]);
    if (r.proceed) {
      expect(r.keys.agent).toEqual({ key: "a", sha256: "sha-a" });
      expect(r.keys.borrower).toBeUndefined();         // a failed upload never becomes a key
      expect(r.keys.receiptKey).toBeUndefined();
      expect(r.keys.documents.map((d) => d.category)).toEqual(["ID_PROOF"]);
    }
  });

  it("accepting changes nothing when nothing failed", () => {
    const e = { ...nothing, agent: ok("a") };
    expect(liveEvidenceOutcome(e, { cheque: false, accepted: true })).toEqual(liveEvidenceOutcome(e, cash));
  });
});
