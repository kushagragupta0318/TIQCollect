// ─── CHANGELOG ─────────────────────────────────────────────────────────────
// 2026-09-24 — New file. H14: what RecordVisitPage does with the suggestions
//   POST /agent/cases/{id}/visit-extraction returns. Pure, so it is tested
//   without mounting a 2,300-line page — the same pattern as
//   allocationReasons.ts and the other eight modules extracted for tests.
//
//   The rules it enforces are the page's own: a suggestion is only usable for
//   a field the current visit type shows, a reason only where the page asks
//   for one, PTP figures only when the outcome is a promise. Nothing here
//   changes the form; it returns the patch the agent chose to apply.
// 2026-09-24 (later) — after the audit of a4c834b: refuses payment outcomes
//   itself (defence in depth — the server already never sends one), and the
//   wire types moved to api/agent.ts, where an API type belongs.
// ─────────────────────────────────────────────────────────────────────────────
import type { DefaultReason, VisitOutcome } from "@/types";
import type { ExtractedField, VisitExtraction } from "@/api/agent";

export type { ExtractedField, VisitExtraction };

/** Evidenced by a verified payment, never by speech. The server never
 *  suggests these; refusing them here too means a change there cannot put a
 *  claim about money into the form. */
export const PAYMENT_OUTCOMES: ReadonlySet<string> = new Set(["PAID_FULL", "PART_PAID", "PART_PAID_PTP"]);

/** The slice of RecordVisitPage's FormState a suggestion can fill. */
export interface ExtractableForm {
  outcome: VisitOutcome | null;
  defaultReason: DefaultReason | null;
  ptpAmount: string;
  ptpDate: string;
}

export interface OutcomeOption {
  value: VisitOutcome;
  label: string;
  needsPayment?: boolean;
  needsPTP?: boolean;
}

export interface ReasonOption {
  value: DefaultReason;
  label: string;
}

export interface PlannedSuggestion {
  field: string;
  formKey: keyof ExtractableForm | null;
  value: string;              // what goes into the form
  display: string;            // what the agent reads
  evidence: string;
  /** The agent can apply this one now, against the form as it stands. */
  usable: boolean;
  /** "Fill empty fields" would set it: usable once the suggested outcome is in. */
  fillable: boolean;
  alreadySet: boolean;        // the form already holds exactly this value
  note: string | null;        // why it is not usable, in the agent's words
}

const FIELD_ORDER = ["outcome", "default_reason", "ptp_amount", "ptp_date", "person_met", "not_met_reason"];

/** Fields the Borrower path fills itself or does not show — reported, never applied. */
const NOT_ON_THIS_PATH: Record<string, string> = {
  person_met: "Set by choosing who you met",
  not_met_reason: "Only asked when the borrower was not met",
};

export function planSuggestions(
  ext: VisitExtraction,
  form: ExtractableForm,
  outcomes: OutcomeOption[],
  reasons: ReasonOption[],
): PlannedSuggestion[] {
  const byValue = new Map(outcomes.map((o) => [o.value, o]));
  const outcomeSug = ext.suggestions.find((s) => s.field === "outcome");
  const suggestedOutcome =
    outcomeSug && byValue.has(outcomeSug.value as VisitOutcome) && !PAYMENT_OUTCOMES.has(String(outcomeSug.value))
      ? (outcomeSug.value as VisitOutcome) : null;
  // What the outcome WOULD be after "fill empty fields": the agent's own choice
  // always wins over a suggestion.
  const effective = form.outcome ?? suggestedOutcome;

  const rows = ext.suggestions.map((s): PlannedSuggestion => {
    const base = { field: s.field, evidence: s.evidence, value: String(s.value), display: String(s.value) };

    if (s.field in NOT_ON_THIS_PATH) {
      return { ...base, formKey: null, usable: false, fillable: false, alreadySet: false, note: NOT_ON_THIS_PATH[s.field] };
    }

    if (s.field === "outcome") {
      if (PAYMENT_OUTCOMES.has(String(s.value))) {
        return { ...base, formKey: "outcome", usable: false, fillable: false, alreadySet: false,
          note: "Recorded from a verified payment, not suggested" };
      }
      const opt = byValue.get(s.value as VisitOutcome);
      if (!opt) {
        return { ...base, formKey: "outcome", usable: false, fillable: false, alreadySet: false,
          note: "Not an outcome for this visit type" };
      }
      const alreadySet = form.outcome === opt.value;
      return { ...base, formKey: "outcome", display: opt.label, usable: !alreadySet,
        fillable: form.outcome === null, alreadySet, note: null };
    }

    if (s.field === "default_reason") {
      const opt = reasons.find((r) => r.value === s.value);
      const alreadySet = form.defaultReason === s.value;
      if (!opt) {
        return { ...base, formKey: "defaultReason", usable: false, fillable: false, alreadySet, note: "Not a listed reason" };
      }
      // The page asks for a reason only once an outcome is chosen, and never
      // for a payment or a revisit (RecordVisitPage, section C).
      const asks = (o: VisitOutcome | null) =>
        o !== null && o !== "REVISIT" && !byValue.get(o)?.needsPayment;
      return { ...base, formKey: "defaultReason", display: opt.label,
        usable: asks(form.outcome) && !alreadySet,
        fillable: asks(effective) && form.defaultReason === null,
        alreadySet,
        note: asks(effective) ? null : "Only asked for an unpaid outcome" };
    }

    if (s.field === "ptp_amount" || s.field === "ptp_date") {
      const key = s.field === "ptp_amount" ? "ptpAmount" : "ptpDate";
      const value = s.field === "ptp_amount" ? String(Math.round(Number(s.value))) : String(s.value);
      const display = s.field === "ptp_amount" ? formatRupees(Number(s.value)) : formatDate(String(s.value));
      const promise = (o: VisitOutcome | null) => o !== null && !!byValue.get(o)?.needsPTP;
      const alreadySet = form[key] === value;
      return { ...base, formKey: key, value, display,
        usable: promise(form.outcome) && !alreadySet,
        fillable: promise(effective) && form[key] === "",
        alreadySet,
        note: promise(effective) ? null : "Only used when the outcome is a promise to pay" };
    }

    return { ...base, formKey: null, usable: false, fillable: false, alreadySet: false, note: "Not a field on this form" };
  });

  return rows.sort((a, b) => FIELD_ORDER.indexOf(a.field) - FIELD_ORDER.indexOf(b.field));
}

/** The patch for one row the agent tapped "Use" on — it overwrites, because
 *  the agent asked for exactly this value. */
export function patchForOne(row: PlannedSuggestion): Partial<ExtractableForm> {
  if (!row.usable || row.formKey === null) return {};
  return { [row.formKey]: row.value } as Partial<ExtractableForm>;
}

/** "Fill empty fields": every fillable row, never over something the agent set. */
export function patchForEmpty(rows: PlannedSuggestion[]): Partial<ExtractableForm> {
  const patch: Partial<ExtractableForm> = {};
  for (const r of rows) {
    if (r.fillable && r.formKey !== null) Object.assign(patch, { [r.formKey]: r.value });
  }
  return patch;
}

const LLM_STATUS_WORDS: Record<string, string> = {
  NOT_CONFIGURED: "AI is not set up on this server",
  RATE_LIMITED: "AI is busy right now",
  TIMEOUT: "AI took too long to answer",
  AUTH_FAILED: "AI is not available",
  MODEL_NOT_FOUND: "AI is not available",
  BAD_RESPONSE: "AI gave an answer that could not be used",
};

/** The label the panel must carry — "AI" only when a model produced it. */
export function sourceLabel(ext: VisitExtraction): { title: string; detail: string | null } {
  if (ext.source === "llm") return { title: "AI suggestions", detail: null };
  if (ext.source === "rules") {
    const why = (ext.llm_status && LLM_STATUS_WORDS[ext.llm_status]) || "AI could not answer";
    return { title: "Keyword suggestions", detail: `${why}, so these come from matching words in your notes.` };
  }
  return { title: "Nothing to read", detail: "Record or type a note first." };
}

export function formatRupees(n: number): string {
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

export function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  // Built from parts, not new Date(iso): an ISO date string parses as UTC
  // midnight and would print as the previous day west of Greenwich.
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
