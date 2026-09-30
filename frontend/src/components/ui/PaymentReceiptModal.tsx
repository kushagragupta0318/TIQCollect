// ─── CHANGELOG (prototype → product) ───
// 2026-07-30 — Gained a `verified` flag so the receipt reflects borrower OTP
//   status: verified (green "Borrower-verified via OTP") vs the offline path
//   (amber "Pending borrower verification"). Backs the OTP gate in
//   RecordVisitPage — an offline collection must not read as "Auto-verified".
//   See prototype_to_product/30.07.md and /changelog.md.
// ───────────────────────────────────────────────────────────────────────────
import { CheckCircle, Clock, X, Share2, Copy } from "lucide-react";
import { toast } from "react-hot-toast";

/** What the receipt modal renders. Exported because the page that builds one
 *  has to hold it in state before showing it, and typing that state `any` was
 *  the only reason a field could be dropped without anything noticing. */
export interface PaymentReceiptData {
  receiptNumber: string;
  amount: number;
  mode: string;
  customerName: string;
  loanAccount: string;
  caseNumber: string;
  agentName: string;
  timestamp: string;
  upiRef?: string;
  verified?: boolean;   // borrower confirmed the amount via OTP
  /** Evidence the agent chose to record without (N1): shown to the agent, not on the shared receipt text. */
  missingEvidence?: string[];
}

interface Props {
  receipt: PaymentReceiptData;
  onClose: () => void;
}

export default function PaymentReceiptModal({ receipt: r, onClose }: Props) {
  const verified = r.verified !== false;   // default to verified unless explicitly pending
  const text = `PAYMENT RECEIPT\n─────────────────\nReceipt No: ${r.receiptNumber}\nAmount: ₹${r.amount.toLocaleString("en-IN")}\nMode: ${r.mode}\nCustomer: ${r.customerName}\nLoan: ${r.loanAccount}\nCase: ${r.caseNumber}\nAgent: ${r.agentName}\nDate: ${new Date(r.timestamp).toLocaleString("en-IN")}\n─────────────────\nTIQCollect`;

  async function handleShare() {
    if (navigator.share) {
      try {
        await navigator.share({ title: "Payment Receipt", text });
        return;
      } catch { /* fall through */ }
    }
    await navigator.clipboard.writeText(text).catch(() => {});
    toast.success("Receipt copied to clipboard");
  }

  function handleCopy() {
    navigator.clipboard.writeText(text).catch(() => {});
    toast.success("Receipt copied!");
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm overflow-hidden rounded-card border border-border bg-white shadow-premium">
        {/* Header */}
        <div className={`${verified ? "bg-success-100 text-success-700" : "bg-warning-100 text-warning-700"} relative p-5 text-center`}>
          <button onClick={onClose} aria-label="Close receipt" className="absolute right-3 top-3 flex size-9 items-center justify-center rounded-control text-foreground/60 hover:bg-white/60 hover:text-foreground">
            <X className="w-5 h-5" />
          </button>
          {verified ? <CheckCircle className="w-10 h-10 mx-auto mb-2" /> : <Clock className="w-10 h-10 mx-auto mb-2" />}
          <p className="font-bold text-lg">{verified ? "Payment Collected!" : "Payment Recorded"}</p>
          <p className="text-2xl font-extrabold mt-1">₹{r.amount.toLocaleString("en-IN")}</p>
          <p className="text-xs mt-1 opacity-90">{verified ? "Borrower-verified via OTP" : "Pending borrower verification"}</p>
        </div>

        {/* Receipt body */}
        <div className="p-5 font-mono text-sm">
          <div className="border-b border-dashed border-slate-200 pb-4 mb-4 space-y-2">
            <ReceiptRow label="Receipt No." value={r.receiptNumber} bold />
            <ReceiptRow label="Payment Mode" value={r.mode} />
            {r.upiRef && <ReceiptRow label="UPI Ref" value={r.upiRef} />}
          </div>
          <div className="space-y-2 mb-4">
            <ReceiptRow label="Customer" value={r.customerName} />
            <ReceiptRow label="Loan Account" value={r.loanAccount} />
            <ReceiptRow label="Case No." value={r.caseNumber} />
            <ReceiptRow label="Collected By" value={r.agentName} />
            <ReceiptRow label="Date & Time" value={new Date(r.timestamp).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })} />
          </div>
          <div className="border-t border-dashed border-slate-200 pt-3 text-center text-xs text-slate-400">
            TIQCollect · {verified ? "Borrower-verified (OTP)" : "Awaiting borrower OTP"}
          </div>
        </div>

        {r.missingEvidence && r.missingEvidence.length > 0 && (
          <p className="mx-5 mb-3 rounded-lg bg-warning-50 px-3 py-2 text-center text-xs font-medium text-warning-700">
            Recorded without: {r.missingEvidence.join(", ")}.
          </p>
        )}

        {/* Actions */}
        <div className="px-5 pb-5 flex gap-2">
          <button onClick={handleCopy} className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl border border-slate-200 text-sm font-medium text-slate-600 hover:bg-slate-50 transition-colors">
            <Copy className="w-4 h-4" /> Copy
          </button>
          <button onClick={handleShare} className="flex min-h-10 flex-1 items-center justify-center gap-2 rounded-control border border-success-500 bg-white py-2.5 text-sm font-medium text-success-700 transition-colors hover:bg-success-100">
            <Share2 className="w-4 h-4" /> Share
          </button>
        </div>
      </div>
    </div>
  );
}

function ReceiptRow({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-slate-400 text-xs">{label}</span>
      <span className={`text-slate-800 text-xs text-right truncate max-w-[60%] ${bold ? "font-bold" : ""}`}>{value}</span>
    </div>
  );
}
