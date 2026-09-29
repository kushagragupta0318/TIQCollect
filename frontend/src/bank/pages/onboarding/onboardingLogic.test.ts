import { describe, expect, it } from "vitest";
import { latestMasterLoginInvite, missingRequiredDocs, normaliseWebsite, unverifiedRequiredDocs } from "./onboardingLogic";
import type { AgencyDocument, InviteSummary } from "@/api/bank";

const REQUIRED = ["REGISTRATION_CERT", "AGREEMENT", "INSURANCE", "POLICE_VERIFICATION_POLICY"] as const;

function doc(overrides: Partial<AgencyDocument>): AgencyDocument {
  return {
    document_id: "d1", doc_type: "REGISTRATION_CERT", file_name: "cert.pdf", content_type: "application/pdf",
    size_bytes: 1024, status: "UPLOADED", scan_status: "PENDING", issued_on: null, expires_on: null,
    rejection_reason: null, uploaded_by: "u1", verified_by: null, verified_at: null,
    ...overrides,
  };
}

describe("missingRequiredDocs — what step 4 still needs uploaded", () => {
  it("every required type missing when nothing has been uploaded", () => {
    expect(missingRequiredDocs([], REQUIRED)).toEqual([...REQUIRED]);
  });

  it("an UPLOADED row satisfies the requirement, even unverified", () => {
    expect(missingRequiredDocs([doc({ doc_type: "REGISTRATION_CERT", status: "UPLOADED" })], REQUIRED))
      .toEqual(["AGREEMENT", "INSURANCE", "POLICE_VERIFICATION_POLICY"]);
  });

  it("a REJECTED-only row still counts as missing — it needs a fresh upload", () => {
    expect(missingRequiredDocs([doc({ doc_type: "REGISTRATION_CERT", status: "REJECTED" })], REQUIRED))
      .toContain("REGISTRATION_CERT");
  });

  it("the latest row wins when a rejected upload was followed by a retry", () => {
    const documents = [
      doc({ document_id: "d2", doc_type: "REGISTRATION_CERT", status: "UPLOADED" }), // newest — get_agency_detail orders newest-first
      doc({ document_id: "d1", doc_type: "REGISTRATION_CERT", status: "REJECTED" }),
    ];
    expect(missingRequiredDocs(documents, REQUIRED)).not.toContain("REGISTRATION_CERT");
  });
});

describe("unverifiedRequiredDocs — the Review step's activation checklist", () => {
  it("an UPLOADED-but-not-verified row still blocks activation", () => {
    expect(unverifiedRequiredDocs([doc({ doc_type: "REGISTRATION_CERT", status: "UPLOADED" })], REQUIRED))
      .toContain("REGISTRATION_CERT");
  });

  it("a VERIFIED row clears that requirement", () => {
    expect(unverifiedRequiredDocs([doc({ doc_type: "REGISTRATION_CERT", status: "VERIFIED" })], REQUIRED))
      .not.toContain("REGISTRATION_CERT");
  });

  it("all four verified means nothing outstanding", () => {
    const documents = REQUIRED.map((type) => doc({ doc_type: type, status: "VERIFIED" }));
    expect(unverifiedRequiredDocs(documents, REQUIRED)).toEqual([]);
  });
});

describe("latestMasterLoginInvite", () => {
  function invite(overrides: Partial<InviteSummary>): InviteSummary {
    return {
      id: "i1", email: "a@x.com", full_name: "A", phone: "+911234567890", role: "AGENCY_ADMIN",
      purpose: "AGENCY_MASTER_LOGIN", bank_id: "b1", agency_id: "ag1", delivery_channel: "LINK",
      invited_by: "u1", created_at: "2026-09-01T00:00:00Z", expires_at: "2026-09-04T00:00:00Z", status: "OPEN",
      ...overrides,
    };
  }

  it("null when no invite exists for this agency", () => {
    expect(latestMasterLoginInvite([], "ag1")).toBeNull();
  });

  it("ignores invites for other agencies and other purposes", () => {
    const invites = [
      invite({ agency_id: "ag2" }),
      invite({ agency_id: "ag1", purpose: "USER_ONBOARD" }),
    ];
    expect(latestMasterLoginInvite(invites, "ag1")).toBeNull();
  });

  it("picks the most recently created when a revoked invite was followed by a fresh one", () => {
    const invites = [
      invite({ id: "old", status: "REVOKED", created_at: "2026-09-01T00:00:00Z" }),
      invite({ id: "new", status: "OPEN", created_at: "2026-09-05T00:00:00Z" }),
    ];
    expect(latestMasterLoginInvite(invites, "ag1")?.id).toBe("new");
  });
});

describe("normaliseWebsite — owner-reported: type=\"url\" rejected a bare host", () => {
  it("prefixes https:// onto a bare host", () => {
    expect(normaliseWebsite("google.com")).toBe("https://google.com");
  });

  it("leaves an explicit https:// URL alone", () => {
    expect(normaliseWebsite("https://foo.co")).toBe("https://foo.co");
  });

  it("prefixes a www subdomain the same as any other bare host", () => {
    expect(normaliseWebsite("www.foo.in")).toBe("https://www.foo.in");
  });

  it("preserves a path", () => {
    expect(normaliseWebsite("foo.com/x")).toBe("https://foo.com/x");
  });

  it("never upgrades an explicit http:// to https", () => {
    expect(normaliseWebsite("http://foo.com")).toBe("http://foo.com");
  });

  it("trims a bare-root trailing slash so foo.com and foo.com/ store the same way", () => {
    expect(normaliseWebsite("https://foo.com/")).toBe("https://foo.com");
  });

  it("does not touch a trailing slash on a real path", () => {
    expect(normaliseWebsite("https://foo.com/x/")).toBe("https://foo.com/x/");
  });

  it("trims surrounding whitespace", () => {
    expect(normaliseWebsite("  foo.com  ")).toBe("https://foo.com");
  });

  it("empty or whitespace-only input is null, not an empty-string website", () => {
    expect(normaliseWebsite("")).toBeNull();
    expect(normaliseWebsite("   ")).toBeNull();
  });

  it("does not strip www — that would change which host is actually named", () => {
    expect(normaliseWebsite("www.foo.com")).not.toBe(normaliseWebsite("foo.com"));
  });
});
