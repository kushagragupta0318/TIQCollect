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

/**
 * Accepts a bare host ("google.com") as readily as a full URL
 * ("https://foo.co"), and normalises both to one consistent stored form.
 *
 * 2026-09-29 — owner-reported: the Website field was `<input type="url">`,
 * which requires a scheme, so a manager typing the domain as most people
 * actually write it ("google.com") got the browser's native "Please enter a
 * URL" and could not submit. The input is now `type="text"`; this function
 * is what makes typing a bare host work anyway — no scheme -> prefix
 * https://, an explicit http:// is left alone (never silently upgraded),
 * and a bare-root trailing slash is trimmed so "https://foo.com/" and
 * "https://foo.com" store the same way. Mirrored server-side
 * (agency_service._normalise_website) so a value pasted straight into an
 * API call, not just typed through this form, normalises the same way —
 * one rule, stated once in each language since they cannot share source,
 * with the same test-input table on both sides.
 *
 * Deliberately does NOT touch "www." — that changes which host is actually
 * being named, which is not this function's business to decide for a bank
 * whose real site might only resolve under www.
 */
export function normaliseWebsite(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const withScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(trimmed) ? trimmed : `https://${trimmed}`;
  return withScheme.replace(/^(https?:\/\/[^/]+)\/$/, "$1");
}
