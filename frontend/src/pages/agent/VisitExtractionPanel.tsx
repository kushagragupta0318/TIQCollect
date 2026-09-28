// ─── CHANGELOG ─────────────────────────────────────────────────────────────
// 2026-09-24 — New file. H14, feature #2: "Fill the form from your notes".
//   Sits under the two voice-note boxes on RecordVisitPage's Borrower path.
//   Sends what the agent dictated (or typed) to the extraction endpoint and
//   lists each suggested value beside the words it came from. The agent taps
//   "Use" per value, or fills every still-empty field at once. It never
//   overwrites a choice silently and never submits anything.
//
//   Its own file so the 2,300-line page gains one import and one element.
// ─────────────────────────────────────────────────────────────────────────────
import { useState } from "react";
import { CheckCircle, ListChecks, Sparkles } from "lucide-react";
import { toast } from "react-hot-toast";
import { extractVisitFields } from "@/api/agent";
import { Button } from "@/components/ui/Button";
import { errorDetail } from "@/lib/apiError";
import {
  patchForEmpty,
  patchForOne,
  planSuggestions,
  sourceLabel,
  type ExtractableForm,
  type OutcomeOption,
  type ReasonOption,
  type VisitExtraction,
} from "./visitExtraction";

const FIELD_LABEL: Record<string, string> = {
  outcome: "Outcome",
  default_reason: "Reason for default",
  ptp_amount: "Promised amount",
  ptp_date: "Promised date",
  person_met: "Who you met",
  not_met_reason: "Why not met",
};

export function VisitExtractionPanel({ caseId, transcript, form, outcomes, reasons, onApply }: {
  caseId: string;
  transcript: string;
  form: ExtractableForm;
  outcomes: OutcomeOption[];
  reasons: ReasonOption[];
  onApply: (patch: Partial<ExtractableForm>) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<VisitExtraction | null>(null);
  const [readText, setReadText] = useState("");

  const text = transcript.trim();
  const rows = result ? planSuggestions(result, form, outcomes, reasons) : [];
  const fillCount = Object.keys(patchForEmpty(rows)).length;
  const stale = result !== null && readText !== text;

  async function read() {
    if (!text || loading) return;
    setLoading(true);
    try {
      const r = await extractVisitFields(caseId, text);
      setResult(r);
      setReadText(text);
    } catch (err) {
      toast.error(errorDetail(err, "Could not read the notes. Fill the form by hand."));
    } finally {
      setLoading(false);
    }
  }

  function apply(patch: Partial<ExtractableForm>) {
    const n = Object.keys(patch).length;
    if (!n) return;
    onApply(patch);
    toast.success(`Filled ${n} field${n === 1 ? "" : "s"} — check them above before you submit`);
  }

  const label = result ? sourceLabel(result) : null;

  return (
    <div className="mt-4 rounded-xl border border-brand-100 bg-brand-50/40 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-700">Fill the form from your notes</p>
          <p className="mt-0.5 text-xs text-slate-500">
            Reads the two notes above and suggests the outcome, reason and promise. You confirm each one.
          </p>
        </div>
        <Button type="button" size="sm" variant="secondary" loading={loading} disabled={!text} onClick={read}>
          {result ? "Read again" : "Read notes"}
        </Button>
      </div>

      {result && label && (
        <div className="mt-3 space-y-2">
          <div className="flex items-start gap-2">
            {result.ai_generated
              ? <Sparkles className="mt-0.5 size-3.5 flex-shrink-0 text-violet-600" />
              : <ListChecks className="mt-0.5 size-3.5 flex-shrink-0 text-slate-500" />}
            <div className="min-w-0 text-xs">
              <span className={`font-semibold ${result.ai_generated ? "text-violet-700" : "text-slate-700"}`}>{label.title}</span>
              {label.detail && <span className="text-slate-500"> — {label.detail}</span>}
            </div>
          </div>

          {stale && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
              Your notes changed after they were read. Read them again before using these.
            </p>
          )}

          {rows.length === 0 && result.source !== "none" && (
            <p className="text-xs text-slate-500">Nothing in the notes points to a form value. Fill the form by hand.</p>
          )}

          {rows.map((r) => (
            <div key={r.field} className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 bg-white px-3 py-2">
              <div className="min-w-0">
                <p className="text-xs text-slate-400">{FIELD_LABEL[r.field] ?? r.field}</p>
                <p className="text-sm font-medium text-slate-800">{r.display}</p>
                <p className="mt-0.5 truncate text-xs italic text-slate-500" title={r.evidence}>“{r.evidence}”</p>
              </div>
              <div className="flex-shrink-0 pt-1 text-right">
                {r.alreadySet ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-success-700">
                    <CheckCircle className="size-3.5" /> In the form
                  </span>
                ) : r.usable && !stale ? (
                  <button
                    type="button"
                    onClick={() => apply(patchForOne(r))}
                    className="rounded-lg border border-brand-200 bg-white px-2.5 py-1 text-xs font-semibold text-brand-700 transition-colors hover:bg-brand-50"
                  >
                    Use
                  </button>
                ) : (
                  <span className="block max-w-[9rem] text-xs text-slate-400">
                    {stale ? "Read again" : r.note ?? "Needs the outcome first"}
                  </span>
                )}
              </div>
            </div>
          ))}

          {fillCount > 0 && !stale && (
            <Button type="button" size="sm" fullWidth onClick={() => apply(patchForEmpty(rows))}>
              Fill {fillCount} empty field{fillCount === 1 ? "" : "s"}
            </Button>
          )}

          {result.rejected.length > 0 && (
            <p className="text-xs text-slate-400">
              Set aside: {result.rejected.map((x) => `${FIELD_LABEL[x.field] ?? x.field} (${x.reason})`).join("; ")}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
