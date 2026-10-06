// What an agent may attach to a visit, in one place. DOCUMENT_CATEGORY_IDS
// mirrors the server's VISIT_DOCUMENT_CATEGORIES (backend/app/models/visit.py);
// test_n1_evidence.py reads this file and fails if the two drift.

export const DOCUMENT_CATEGORY_IDS = ["BANK_STMT", "ID_PROOF", "INCOME_PROOF", "MEDICAL_SUPPORT"] as const;
export type DocumentCategory = (typeof DOCUMENT_CATEGORY_IDS)[number];

export const DOCUMENT_CATEGORY_LABELS: Record<DocumentCategory, string> = {
  BANK_STMT: "Bank Statement",
  ID_PROOF: "ID Proof (Aadhaar / PAN)",
  INCOME_PROOF: "GST / Salary Slip / ITR",
  MEDICAL_SUPPORT: "Medical / Support Docs",
};

/** The file types the server issues an upload for: a phone photo or a scan. */
export const DOCUMENT_CONTENT_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"] as const;
export const DOCUMENT_MAX_BYTES = 10 * 1024 * 1024;

/** Why this file cannot be attached, or null when it can. */
export function documentProblem(file: { type: string; size: number }): string | null {
  if (!(DOCUMENT_CONTENT_TYPES as readonly string[]).includes(file.type)) {
    return "Attach a photo (JPEG, PNG or WebP) or a PDF.";
  }
  if (file.size > DOCUMENT_MAX_BYTES) return "That file is over 10 MB. Attach a smaller photo or scan.";
  return null;
}

export interface EscalationForm {
  escalationNotes: string;
  witnessPresent: boolean;
  witnessName: string;
}

/**
 * What the escalation box and the witness question put on the visit: nothing
 * unless the outcome asked for them, never a witness name without a witness.
 * They used to be captured and dropped on submit.
 */
export function escalationFields(
  asked: boolean, f: EscalationForm,
): { escalation_notes?: string; witness_present?: boolean; witness_name?: string } {
  if (!asked) return {};
  return {
    escalation_notes: f.escalationNotes.trim() || undefined,
    witness_present: f.witnessPresent,
    witness_name: f.witnessPresent ? f.witnessName.trim() || undefined : undefined,
  };
}
