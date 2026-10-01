// Pure display rules for the borrower page (C08). No React, no axios.
//
// The rule this file exists to hold: nothing is summed across cases. Every
// helper takes ONE case, or describes the set without aggregating its money.
import type { CaseRow, CustomerHeader, TimelineEntry, TimelineKind } from "@/api/bankCustomer";
import { pyDate } from "../../theme/format";

/** "₹24,600" — the bank portal's money style, en-IN grouping, no paise. */
export function inr(value: number | null | undefined): string {
  return value == null ? "—" : `₹${Math.round(value).toLocaleString("en-IN")}`;
}

/** The bank portal's one date format (theme/format.pyDate) — not a second one:
 *  `toLocaleDateString("en-GB")` renders "Sept" on some runtimes and "Sep" on
 *  others, so a locale-dependent date would read differently per machine. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : pyDate(d);
}

export function dayTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${day(iso)}, ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`;
}

/** Enum codes as a bank user reads them; an unknown code shows as itself. */
const WORDS: Record<string, string> = {
  // Visit outcomes (models/visit.py VisitOutcome).
  PAID_FULL: "Paid in full", PART_PAID: "Part paid", PTP: "Promised to pay",
  PART_PAID_PTP: "Part paid, promised the rest", BROKEN_PTP: "Broke an earlier promise",
  RTP: "Refused to pay", NOT_AVAILABLE: "Borrower not there", ADDRESS_ISSUE: "Address wrong",
  DECEASED: "Borrower deceased", REVISIT: "Revisit needed",
  // Call outcomes (models/call_log.py CallOutcome).
  ANSWERED: "Answered", NO_ANSWER: "No answer", BUSY: "Busy", DECLINED: "Call cut",
  SWITCHED_OFF: "Phone off", WRONG_NUMBER: "Wrong number",
  ASSIGNED: "Assigned", IN_PROGRESS: "In progress", PTP_SET: "Promise taken", PARTIALLY_PAID: "Part paid",
  PAID: "Paid", CLOSED: "Closed", ESCALATED: "Escalated", WRITTEN_OFF: "Written off", NEW: "New",
  CURRENT: "Current", BUCKET_1: "1–30 days", BUCKET_2: "31–60 days", BUCKET_3: "61–90 days", NPA: "NPA",
  WILL_PAY: "Said they will pay", MAY_PAY: "Might pay", NO_COMMITMENT: "No commitment",
  HARDSHIP: "Hardship", DISPUTE: "Disputes the debt", REFUSES: "Refuses to pay",
  VERIFIED: "Verified", PENDING_VERIFICATION: "Awaiting the borrower's OTP", FAILED: "Failed",
  ACTIVE: "Active", HONORED: "Kept", PARTIALLY_HONORED: "Part kept", BROKEN: "Broken",
  EXPIRED: "Expired", RESCHEDULED: "Rescheduled",
};

export function words(code: string | null | undefined): string {
  if (!code) return "—";
  return WORDS[code] ?? code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

/** What this ONE case still needs. Never added up across cases. */
export function remainingOnCase(c: CaseRow): number | null {
  if (c.target_amount == null) return null;
  return Math.max(0, c.target_amount - (c.collected_verified ?? 0));
}

/** 0-100 for one case's progress bar, or null when there is no target to measure against. */
export function collectedPct(c: CaseRow): number | null {
  if (!c.target_amount) return null;
  return Math.min(100, Math.round(((c.collected_verified ?? 0) / c.target_amount) * 100));
}

/**
 * How the set of cases is described WITHOUT aggregating money: counts only.
 * A borrower-level outstanding or recovery figure is a statistic nobody has
 * defined, and two cases may sit at two agencies under different contracts.
 */
export function caseSummary(cases: CaseRow[], loansWithoutCases: number): string {
  const parts: string[] = [];
  if (cases.length) parts.push(`${cases.length} case${cases.length === 1 ? "" : "s"}`);
  const agencies = new Set(cases.map((c) => c.agency_id).filter(Boolean));
  if (agencies.size > 1) parts.push(`${agencies.size} agencies`);
  if (loansWithoutCases) parts.push(`${loansWithoutCases} loan${loansWithoutCases === 1 ? "" : "s"} never placed`);
  return parts.length ? parts.join(" · ") : "No cases yet";
}

/** The flags a bank user must see before anyone contacts this borrower. */
export function contactFlags(h: CustomerHeader): string[] {
  const flags: string[] = [];
  if (h.do_not_contact) flags.push("Do not contact");
  if (h.is_hostile) flags.push("Hostile");
  for (const tag of h.tags) flags.push(words(tag));
  return flags;
}

/** Which case opens first: the one the caller arrived for, else the newest placed. */
export function initialCaseId(cases: CaseRow[], focusLoanId: string | null): string | null {
  if (!cases.length) return null;
  if (focusLoanId) {
    const match = cases.find((c) => c.loan_id === focusLoanId);
    if (match) return match.case_id;
  }
  return cases[0].case_id;
}

const KIND_WORDS: Record<TimelineKind, string> = {
  VISIT: "Visit", CALL: "Call", PAYMENT: "Payment", PTP: "Promise to pay",
};

export interface TimelineLine {
  key: string;
  kind: TimelineKind;
  kindLabel: string;
  at: string;
  /** One line a reader can scan; the raw detail stays available beneath it. */
  summary: string;
  hasEvidence: boolean;
}

export function timelineLines(entries: TimelineEntry[]): TimelineLine[] {
  return entries.map((e) => {
    const d = e.detail ?? {};
    let summary = "";
    switch (e.kind) {
      case "VISIT": {
        const met = d.customer_met ? "Borrower met" : "Borrower not met";
        summary = `${words(String(d.outcome ?? ""))} · ${met}`;
        if (d.borrower_disposition) summary += ` · ${words(String(d.borrower_disposition))}`;
        if (d.geo_verified === false) summary += " · outside the geo-fence";
        break;
      }
      case "CALL": {
        summary = words(String(d.outcome ?? ""));
        if (d.duration_seconds) summary += ` · ${d.duration_seconds}s`;
        if (d.borrower_disposition) summary += ` · ${words(String(d.borrower_disposition))}`;
        break;
      }
      case "PAYMENT":
        summary = `${inr(Number(d.amount ?? 0))} · ${words(String(d.mode ?? ""))} · ${words(String(d.status ?? ""))}`;
        break;
      case "PTP":
        summary = `${inr(Number(d.committed_amount ?? 0))} by ${day(String(d.committed_date ?? ""))} · ${words(String(d.status ?? ""))}`;
        break;
    }
    return {
      key: `${e.kind}-${e.entity_id}`,
      kind: e.kind,
      kindLabel: KIND_WORDS[e.kind],
      at: dayTime(e.at),
      summary,
      hasEvidence: e.kind === "VISIT" && d.has_evidence === true,
    };
  });
}
