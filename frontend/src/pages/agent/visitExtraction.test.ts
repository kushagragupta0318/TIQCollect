import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatRupees,
  patchForEmpty,
  patchForOne,
  planSuggestions,
  sourceLabel,
  type ExtractableForm,
  type OutcomeOption,
  type ReasonOption,
  type VisitExtraction,
} from "./visitExtraction";

// The Borrower path's own lists, as RecordVisitPage declares them (labels trimmed).
const OUTCOMES: OutcomeOption[] = [
  { value: "PAID_FULL", label: "Paid in Full", needsPayment: true },
  { value: "PART_PAID", label: "Partial Payment", needsPayment: true },
  { value: "PTP", label: "Promise to Pay", needsPTP: true },
  { value: "PART_PAID_PTP", label: "Part Paid + PTP", needsPayment: true, needsPTP: true },
  { value: "BROKEN_PTP", label: "PTP Broken" },
  { value: "RTP", label: "Refuse to Pay" },
  { value: "DISPUTE", label: "Dispute" },
  { value: "REVISIT", label: "Revisit Required" },
];
const REASONS: ReasonOption[] = [
  { value: "JOB_LOSS", label: "Lost job / unemployed" },
  { value: "SALARY_CUT", label: "Salary reduction" },
  { value: "MEDICAL", label: "Medical emergency" },
];

const EMPTY: ExtractableForm = { outcome: null, defaultReason: null, ptpAmount: "", ptpDate: "" };

function ext(suggestions: VisitExtraction["suggestions"], over: Partial<VisitExtraction> = {}): VisitExtraction {
  return {
    source: "llm", ai_generated: true, suggestions, rejected: [],
    llm_status: "OK", failure_reason: null, version: "visit-extraction-1.0.0", ...over,
  };
}

// "Met the borrower. Lost his job, will pay Rs 5,000 on the 30th."
const PROMISE = ext([
  { field: "ptp_date", value: "2026-09-30", evidence: "on the 30th" },
  { field: "person_met", value: "BORROWER", evidence: "Met the borrower" },
  { field: "outcome", value: "PTP", evidence: "will pay" },
  { field: "ptp_amount", value: 5000.0, evidence: "Rs 5,000" },
  { field: "default_reason", value: "JOB_LOSS", evidence: "Lost his job" },
]);

const byField = (rows: ReturnType<typeof planSuggestions>) => Object.fromEntries(rows.map((r) => [r.field, r]));

describe("filling an empty form", () => {
  const rows = planSuggestions(PROMISE, EMPTY, OUTCOMES, REASONS);
  const f = byField(rows);

  it("fills outcome, reason and both PTP fields, and nothing else", () => {
    expect(patchForEmpty(rows)).toEqual({
      outcome: "PTP", defaultReason: "JOB_LOSS", ptpAmount: "5000", ptpDate: "2026-09-30",
    });
  });

  it("lets the outcome be used alone, but not the fields that depend on it", () => {
    // Tapping "Use" on the amount before the outcome is a promise would set a
    // value the page hides and never submits.
    expect(f.outcome.usable).toBe(true);
    expect(f.ptp_amount.usable).toBe(false);
    expect(f.default_reason.usable).toBe(false);
  });

  it("reports who was met but never applies it — the Borrower path sets it", () => {
    expect(f.person_met.usable).toBe(false);
    expect(f.person_met.fillable).toBe(false);
    expect(f.person_met.note).toMatch(/who you met/);
  });

  it("orders rows the way the form reads", () => {
    expect(rows.map((r) => r.field)).toEqual(["outcome", "default_reason", "ptp_amount", "ptp_date", "person_met"]);
  });

  it("the PTP section receives exactly the numbers the note gave", () => {
    const form = { ...EMPTY, ...patchForEmpty(rows) };
    expect(Number(form.ptpAmount)).toBe(5000);
    expect(form.ptpDate).toBe("2026-09-30");
  });
});

describe("the agent's own choices win", () => {
  it("never replaces an outcome the agent chose, and drops what depends on the suggestion", () => {
    const rows = planSuggestions(PROMISE, { ...EMPTY, outcome: "RTP" }, OUTCOMES, REASONS);
    const f = byField(rows);
    expect(patchForEmpty(rows)).toEqual({ defaultReason: "JOB_LOSS" });
    // still offered, one tap, because the agent may have picked the wrong one
    expect(f.outcome.usable).toBe(true);
    expect(f.outcome.fillable).toBe(false);
    expect(f.ptp_amount.usable || f.ptp_amount.fillable).toBe(false);
    expect(f.ptp_amount.note).toMatch(/promise to pay/);
  });

  it("does not overwrite an amount already typed, but a tap on Use does", () => {
    const form = { ...EMPTY, outcome: "PTP" as const, ptpAmount: "3000" };
    const f = byField(planSuggestions(PROMISE, form, OUTCOMES, REASONS));
    expect(patchForEmpty(Object.values(f))).not.toHaveProperty("ptpAmount");
    expect(patchForOne(f.ptp_amount)).toEqual({ ptpAmount: "5000" });
  });

  it("marks a suggestion the form already holds, and offers nothing for it", () => {
    const form = { ...EMPTY, outcome: "PTP" as const, ptpDate: "2026-09-30" };
    const f = byField(planSuggestions(PROMISE, form, OUTCOMES, REASONS));
    expect(f.outcome.alreadySet).toBe(true);
    expect(f.outcome.usable).toBe(false);
    expect(f.ptp_date.alreadySet).toBe(true);
    expect(patchForOne(f.ptp_date)).toEqual({});
  });
});

describe("the page's own rules about when a field is asked", () => {
  it("asks no reason for a payment outcome or a revisit", () => {
    for (const outcome of ["PART_PAID", "PAID_FULL", "REVISIT"] as const) {
      const f = byField(planSuggestions(PROMISE, { ...EMPTY, outcome }, OUTCOMES, REASONS));
      expect(f.default_reason.usable).toBe(false);
      expect(f.default_reason.fillable).toBe(false);
    }
  });

  it("uses PTP figures for Part Paid + PTP, which also carries a promise", () => {
    const f = byField(planSuggestions(PROMISE, { ...EMPTY, outcome: "PART_PAID_PTP" }, OUTCOMES, REASONS));
    expect(f.ptp_amount.usable).toBe(true);
  });

  it("refuses an outcome this visit type does not offer", () => {
    const e = ext([{ field: "outcome", value: "NOT_AVAILABLE", evidence: "not at home" }]);
    const [row] = planSuggestions(e, EMPTY, OUTCOMES, REASONS);
    expect(row.usable || row.fillable).toBe(false);
    expect(patchForEmpty([row])).toEqual({});
  });

  it("refuses a reason the page does not list", () => {
    const e = ext([
      { field: "outcome", value: "RTP", evidence: "refused to pay" },
      { field: "default_reason", value: "OVER_LEVERAGED", evidence: "other loans" },
    ]);
    expect(patchForEmpty(planSuggestions(e, EMPTY, OUTCOMES, REASONS))).toEqual({ outcome: "RTP" });
  });

  it("never applies a not-met reason on the Borrower path", () => {
    const e = ext([{ field: "not_met_reason", value: "PREMISES_LOCKED", evidence: "house was locked" }]);
    const [row] = planSuggestions(e, EMPTY, OUTCOMES, REASONS);
    expect(patchForOne(row)).toEqual({});
  });
});

describe("what the agent reads", () => {
  it("shows labels, rupees and a readable date — never raw codes", () => {
    const f = byField(planSuggestions(PROMISE, EMPTY, OUTCOMES, REASONS));
    expect(f.outcome.display).toBe("Promise to Pay");
    expect(f.default_reason.display).toBe("Lost job / unemployed");
    expect(f.ptp_amount.display).toBe("₹5,000");
    expect(f.ptp_date.display).toMatch(/30/);
    expect(f.ptp_date.display).toMatch(/Sep/);
  });

  it("rounds a fractional amount the way the PTP input would hold it", () => {
    const e = ext([
      { field: "outcome", value: "PTP", evidence: "will pay" },
      { field: "ptp_amount", value: 2499.6, evidence: "2,499.60" },
    ]);
    expect(patchForEmpty(planSuggestions(e, EMPTY, OUTCOMES, REASONS)).ptpAmount).toBe("2500");
  });

  it("formats in Indian digit grouping", () => {
    expect(formatRupees(150000)).toBe("₹1,50,000");
  });

  it("prints the calendar date it was given, whatever the timezone", () => {
    expect(formatDate("2026-10-01")).toMatch(/^1 Oct 2026$/);
    expect(formatDate("garbage")).toBe("garbage");
  });
});

describe("the label says who produced the suggestions", () => {
  it("calls it AI only when a model produced it", () => {
    expect(sourceLabel(PROMISE).title).toBe("AI suggestions");
    const rules = sourceLabel({ ...PROMISE, source: "rules", ai_generated: false, llm_status: "NOT_CONFIGURED" });
    expect(rules.title).not.toMatch(/\bAI\b/);
    expect(rules.detail).toMatch(/not set up/);
  });

  it("explains a fallback it has no words for without inventing a reason", () => {
    const rules = sourceLabel({ ...PROMISE, source: "rules", ai_generated: false, llm_status: "SOMETHING_NEW" });
    expect(rules.detail).toMatch(/AI could not answer/);
  });

  it("says there was nothing to read", () => {
    expect(sourceLabel({ ...PROMISE, source: "none", suggestions: [] }).title).toBe("Nothing to read");
  });
});
