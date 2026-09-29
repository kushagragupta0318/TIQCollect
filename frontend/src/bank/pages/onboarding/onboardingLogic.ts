// Pure logic extracted out of the onboarding wizard so it can be unit tested
// without mounting a step component — this repo's own pattern (see
// bank/components/kpi.ts, rowActivation.ts, visualMath.ts and their .test.ts
// siblings).
import type { AgencyDocument, InviteSummary } from "@/api/bank";

/** The most recent document row of each type — get_agency_detail already
 *  orders `documents` newest-first, so "first match per type" is "latest". A
 *  type can have more than one row (a rejected upload followed by a retry);
 *  only the latest one describes the current state of that requirement. */
export function latestDocumentsByType(documents: AgencyDocument[]): Map<string, AgencyDocument> {
  const byType = new Map<string, AgencyDocument>();
  for (const doc of documents) {
    if (!byType.has(doc.doc_type)) byType.set(doc.doc_type, doc);
  }
  return byType;
}

/** Required doc types with no UPLOADED-or-better row yet — what the
 *  Documents step still needs before every required slot has something in
 *  it. A REJECTED-only row still counts as missing: it needs a fresh upload. */
export function missingRequiredDocs(documents: AgencyDocument[], required: readonly string[]): string[] {
  const latest = latestDocumentsByType(documents);
  return required.filter((type) => {
    const doc = latest.get(type);
    return !doc || doc.status === "REJECTED" || doc.status === "EXPIRED" || doc.status === "SUPERSEDED";
  });
}

/** Required doc types not yet VERIFIED — the Review step's activation
 *  checklist. Distinct from missingRequiredDocs: an UPLOADED (not yet
 *  reviewed) document is present for step 4's purposes but still blocks
 *  activation until a bank admin verifies it. */
export function unverifiedRequiredDocs(documents: AgencyDocument[], required: readonly string[]): string[] {
  const latest = latestDocumentsByType(documents);
  return required.filter((type) => latest.get(type)?.status !== "VERIFIED");
}

/** The master-login invite for this agency, newest first — a withdrawn or
 *  expired invite can be followed by a fresh one, and the fresh one is what
 *  the Review step and stepper should describe. */
export function latestMasterLoginInvite(invites: InviteSummary[], agencyId: string): InviteSummary | null {
  const matches = invites
    .filter((inv) => inv.agency_id === agencyId && inv.purpose === "AGENCY_MASTER_LOGIN")
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  return matches[0] ?? null;
}
