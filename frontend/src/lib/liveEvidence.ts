// What a live (payment) submit does with the evidence it tried to upload, in one
// place: which uploads failed, whether to stop and ask the agent first, and which
// keys the visit and the payment may then name. The page used to decide this
// inline, and decided wrongly: a failed photo was recorded as uploaded (N1).
import type { VisitDocumentPayload } from "@/api/agent";
import { failedLabels, keyOf, type UploadResult } from "@/lib/evidenceUpload";
import { DOCUMENT_CATEGORY_LABELS, type DocumentCategory } from "@/lib/visitEvidence";

export interface LiveEvidence {
  agent: UploadResult;
  borrower: UploadResult;
  object: UploadResult;
  signature: UploadResult;
  receipt: UploadResult;
  documents: ReadonlyArray<{ category: DocumentCategory; result: UploadResult }>;
}

export interface UploadedPhoto {
  key: string;
  sha256: string;
}

export interface LiveKeys {
  agent?: UploadedPhoto;
  borrower?: UploadedPhoto;
  object?: UploadedPhoto;
  signatureKey?: string;
  receiptKey?: string;
  documents: VisitDocumentPayload[];
}

export type LiveEvidenceOutcome =
  | { proceed: false; missing: string[] }                 // ask the agent; record nothing
  | { proceed: true; missing: string[]; keys: LiveKeys };  // `missing` is what the agent chose to go without

export function liveEvidenceOutcome(
  e: LiveEvidence, opts: { cheque: boolean; accepted: boolean },
): LiveEvidenceOutcome {
  const missing = failedLabels([
    ["Agent photo", e.agent], ["Borrower photo", e.borrower], ["Premises photo", e.object],
    ["Signature", e.signature],
    [opts.cheque ? "Cheque photo" : "Payment screenshot", e.receipt],
    ...e.documents.map((d) => [`Document: ${DOCUMENT_CATEGORY_LABELS[d.category]}`, d.result] as const),
  ]);
  if (missing.length && !opts.accepted) return { proceed: false, missing };
  const photo = (r: UploadResult): UploadedPhoto | undefined =>
    r.status === "uploaded" ? { key: r.key, sha256: r.sha256 } : undefined;
  return {
    proceed: true,
    missing,
    keys: {
      agent: photo(e.agent), borrower: photo(e.borrower), object: photo(e.object),
      signatureKey: keyOf(e.signature),
      receiptKey: keyOf(e.receipt),
      documents: e.documents.flatMap((d) => d.result.status === "uploaded"
        ? [{ category: d.category, key: d.result.key, sha256: d.result.sha256, content_type: d.result.contentType }]
        : []),
    },
  };
}
