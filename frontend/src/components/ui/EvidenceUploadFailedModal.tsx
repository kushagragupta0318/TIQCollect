// Shown on a live submit when evidence the agent captured did not reach storage.
// The page used to swallow the failure and still say "Visit Recorded" (N1,
// docs/business/PRIORITIES.md). Nothing is recorded until the agent has chosen.
import { AlertTriangle } from "lucide-react";

interface Props {
  labels: string[];
  onRetry: () => void;
  /** Record without them: the agent's choice, made knowing what will be missing. */
  onContinue: () => void;
  onCancel: () => void;
}

export default function EvidenceUploadFailedModal({ labels, onRetry, onContinue, onCancel }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="alertdialog"
      aria-live="assertive"
      aria-label="Some evidence was not saved"
    >
      <div className="w-full max-w-sm overflow-hidden rounded-card border border-border bg-white shadow-premium">
        <div className="bg-warning-100 px-5 pb-5 pt-6 text-center text-warning-700">
          <AlertTriangle className="mx-auto mb-2 h-10 w-10" strokeWidth={1.75} />
          <p className="text-lg font-bold">Some evidence was not saved</p>
        </div>
        <div className="space-y-2 px-5 py-4 text-sm text-slate-700">
          <p>These did not upload:</p>
          <ul className="list-disc pl-5 font-medium text-slate-900">
            {labels.map((l) => <li key={l}>{l}</li>)}
          </ul>
          <p className="text-xs text-slate-500">
            Nothing has been recorded yet. Try again, or record without them: the visit will then have no{" "}
            {labels.join(", ").toLowerCase()}.
          </p>
        </div>
        <div className="space-y-2 px-5 pb-5">
          <button onClick={onRetry} className="w-full rounded-xl bg-brand-600 py-3 text-sm font-semibold text-white active:scale-[0.99] transition-transform">
            Try again
          </button>
          <button onClick={onContinue} className="w-full rounded-xl border border-slate-200 py-3 text-sm font-semibold text-slate-700 active:scale-[0.99] transition-transform">
            Record without them
          </button>
          <button onClick={onCancel} className="w-full py-2 text-xs font-medium text-slate-500">
            Go back to the form
          </button>
        </div>
      </div>
    </div>
  );
}
