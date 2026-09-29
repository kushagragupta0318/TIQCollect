// Confirmation shown after a visit is submitted with no money collected.
//
// That path used to end in a toast, which on a phone appears at the edge of
// the screen for a second or two while the page is already navigating away —
// easy to miss, and the agent is left unsure whether the visit saved. A
// collection ends in PaymentReceiptModal; this gives the no-payment path the
// same weight, centred and unmissable.
//
// Auto-dismisses so the agent is not made to tap through a screen that carries
// no information they need to act on; the Done button is there for anyone who
// wants to move sooner.
import { useEffect } from "react";
import { CheckCircle } from "lucide-react";

interface Props {
  caseNumber?: string;
  customerName?: string;
  outcomeLabel?: string;
  /** Auto-dismiss delay in ms. 0 disables it and waits for the tap. */
  autoCloseMs?: number;
  /** I02: saved in the offline outbox, not yet on the server. Says so. */
  queued?: boolean;
  onClose: () => void;
}

export default function VisitRecordedModal({
  caseNumber,
  customerName,
  outcomeLabel,
  autoCloseMs = 2000,
  queued = false,
  onClose,
}: Props) {
  useEffect(() => {
    if (!autoCloseMs) return;
    const t = setTimeout(onClose, autoCloseMs);
    return () => clearTimeout(t);
  }, [autoCloseMs, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="alertdialog"
      aria-live="assertive"
      aria-label={queued ? "Visit saved on this phone" : "Visit recorded"}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm overflow-hidden rounded-card border border-border bg-white shadow-premium animate-enter"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="bg-success-100 px-5 pb-6 pt-7 text-center text-success-700">
          {/* Tick, with a ring that expands out of it once */}
          <div className="relative w-16 h-16 mx-auto mb-3">
            <span className="absolute inset-0 rounded-full bg-white/40 animate-tick-ring" />
            <CheckCircle className="relative w-16 h-16 animate-tick-pop" strokeWidth={1.75} />
          </div>
          <p className="font-bold text-lg">{queued ? "Saved on this phone" : "Visit Recorded"}</p>
          {queued && (
            <p className="text-sm mt-1 opacity-95">No signal. It sends by itself when signal returns.</p>
          )}
          {outcomeLabel && (
            <p className="text-sm mt-1 opacity-95 font-medium">{outcomeLabel}</p>
          )}
        </div>

        {(customerName || caseNumber) && (
          <div className="px-5 py-4 text-center space-y-0.5">
            {customerName && (
              <p className="text-sm font-semibold text-slate-800">{customerName}</p>
            )}
            {caseNumber && (
              <p className="text-xs text-slate-500">{caseNumber}</p>
            )}
          </div>
        )}

        <div className="px-5 pb-5">
          <button
            onClick={onClose}
            className="w-full py-3 rounded-xl bg-slate-100 text-slate-700 text-sm font-semibold active:scale-[0.99] transition-transform"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
