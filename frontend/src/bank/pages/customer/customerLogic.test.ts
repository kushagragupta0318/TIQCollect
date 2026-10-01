/** The borrower page's display rules (customerLogic.ts), above all: nothing summed across cases. */
import { describe, expect, it } from "vitest";
import type { CaseRow, CustomerHeader, TimelineEntry } from "@/api/bankCustomer";
import {
  caseSummary, collectedPct, contactFlags, day, dayTime, initialCaseId, inr, remainingOnCase,
  timelineLines, words,
} from "./customerLogic";

const kase = (o: Partial<CaseRow> = {}): CaseRow => ({
  case_id: "c1", case_number: "C-0001", status: "ASSIGNED", agency_id: "a1", agency_name: "Aravalli Field Services",
  placed_on: "2026-09-01", agent_id: null, agent_name: null, loan_id: "l1", loan_type: "PERSONAL", dpd: 47,
  dpd_bucket: "BUCKET_2", total_outstanding: 180000, overdue_amount: 24600, target_amount: 24600,
  collected_verified: 5000, last_visit_at: null, last_visit_outcome: null, last_call_at: null,
  last_call_outcome: null, last_contact_at: null, active_ptp_date: null, active_ptp_amount: null,
  has_open_dispute: false, is_escalated: false, latest_probability: 0.71, latest_band: "C",
  latest_model_version: "2.2.0", latest_disposition: null, ...o,
});

const header = (o: Partial<CustomerHeader> = {}): CustomerHeader => ({
  customer_id: "cu1", full_name: "Farhan Siddiqui", phone_primary: "9899000101", address_line1: "C-214, Sector 49",
  city: "Gurugram", state: "Haryana", pincode: "122018", pan_masked: "XXXXX4821K", aadhaar_masked: "XXXXXXXX3307",
  language_preference: "HINDI", is_hostile: false, do_not_contact: false, tags: [], ...o,
});

const entry = (o: Partial<TimelineEntry>): TimelineEntry => ({
  kind: "VISIT", at: "2026-09-20T11:00:00+00:00", actor_type: "AGENT", actor_id: "ag1", entity_id: "v1",
  detail: {}, ...o,
});

describe("per-case figures, never across cases", () => {
  it("measures what one case still needs, and its progress", () => {
    expect(remainingOnCase(kase())).toBe(19600);
    expect(collectedPct(kase())).toBe(20);
  });

  it("has no answer where there is no target, rather than inventing zero", () => {
    expect(remainingOnCase(kase({ target_amount: null }))).toBeNull();
    expect(collectedPct(kase({ target_amount: 0 }))).toBeNull();
  });

  it("never collects more than the target on the bar", () => {
    expect(collectedPct(kase({ collected_verified: 99999 }))).toBe(100);
  });

  it("describes the set by COUNTS only — no borrower-level money", () => {
    const summary = caseSummary([kase(), kase({ case_id: "c2", agency_id: "a2" })], 1);
    expect(summary).toBe("2 cases · 2 agencies · 1 loan never placed");
    expect(summary).not.toMatch(/₹|\d{4,}/);            // no money, no totals
    expect(caseSummary([], 0)).toBe("No cases yet");
    expect(caseSummary([kase()], 0)).toBe("1 case");    // one agency is not worth saying
  });
});

describe("what a reader sees", () => {
  it("reads enum codes as words and keeps an unknown code legible", () => {
    expect(words("PTP_SET")).toBe("Promise taken");
    expect(words("PENDING_VERIFICATION")).toBe("Awaiting the borrower's OTP");
    expect(words("SOME_NEW_CODE")).toBe("Some new code");
    expect(words(null)).toBe("—");
  });

  it("formats money and dates, and shows nothing as a dash", () => {
    expect(inr(24600)).toBe("₹24,600");
    expect(inr(null)).toBe("—");
    expect(day("2026-09-01")).toBe("01 Sep 2026");   // theme/format.pyDate, zero-padded and locale-free
    expect(day(null)).toBe("—");
    expect(dayTime("2026-09-20T11:00:00+00:00")).toContain("20 Sep 2026");
    expect(words("PTP")).toBe("Promised to pay");          // a visit outcome, not left as "Ptp"
  });

  it("surfaces every contact flag before anyone rings the borrower", () => {
    expect(contactFlags(header())).toEqual([]);
    expect(contactFlags(header({ do_not_contact: true, is_hostile: true, tags: ["DECEASED"] })))
      .toEqual(["Do not contact", "Hostile", "Borrower deceased"]);
  });

  it("opens the case the caller arrived for, else the newest", () => {
    const cases = [kase({ case_id: "c1", loan_id: "l1" }), kase({ case_id: "c2", loan_id: "l2" })];
    expect(initialCaseId(cases, "l2")).toBe("c2");
    expect(initialCaseId(cases, null)).toBe("c1");
    expect(initialCaseId(cases, "l-unknown")).toBe("c1");   // a loan with no case: still opens something
    expect(initialCaseId([], "l1")).toBeNull();
  });
});

describe("timeline lines", () => {
  it("summarises each kind in one scannable line", () => {
    const [visit, call, payment, ptp] = timelineLines([
      entry({ kind: "VISIT", detail: { outcome: "PTP", customer_met: true, borrower_disposition: "WILL_PAY", geo_verified: true } }),
      entry({ kind: "CALL", entity_id: "c1", detail: { outcome: "ANSWERED", duration_seconds: 95, borrower_disposition: "HARDSHIP" } }),
      entry({ kind: "PAYMENT", entity_id: "p1", detail: { amount: 5000, mode: "CASH", status: "VERIFIED" } }),
      entry({ kind: "PTP", entity_id: "t1", detail: { committed_amount: 10000, committed_date: "2026-10-05", status: "ACTIVE" } }),
    ]);
    expect(visit.summary).toBe("Promised to pay · Borrower met · Said they will pay");
    expect(call.summary).toBe("Answered · 95s · Hardship");
    expect(payment.summary).toBe("₹5,000 · Cash · Verified");
    expect(ptp.summary).toBe("₹10,000 by 05 Oct 2026 · Active");
    expect([visit.kindLabel, call.kindLabel, payment.kindLabel, ptp.kindLabel])
      .toEqual(["Visit", "Call", "Payment", "Promise to pay"]);
  });

  it("says when a visit was recorded outside the geo-fence", () => {
    const [line] = timelineLines([entry({ detail: { outcome: "PTP", customer_met: true, geo_verified: false } })]);
    expect(line.summary).toContain("outside the geo-fence");
  });

  it("marks evidence only on a visit that has some, and never carries a link", () => {
    const [withEvidence] = timelineLines([entry({ detail: { outcome: "PTP", has_evidence: true } })]);
    const [without] = timelineLines([entry({ detail: { outcome: "PTP", has_evidence: false } })]);
    const [payment] = timelineLines([entry({ kind: "PAYMENT", detail: { amount: 1, has_evidence: true } })]);
    expect(withEvidence.hasEvidence).toBe(true);
    expect(without.hasEvidence).toBe(false);
    expect(payment.hasEvidence).toBe(false);
    expect(JSON.stringify(timelineLines([entry({ detail: { has_evidence: true } })]))).not.toMatch(/http|_key/);
  });
});
