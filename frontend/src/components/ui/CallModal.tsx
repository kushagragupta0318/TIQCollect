import { Phone, PhoneOff } from "lucide-react";
import type { ActiveCall } from "@/hooks/useVoiceCall";

function fmt(s: number) {
  const m = Math.floor(s / 60).toString().padStart(2, "0");
  const sec = (s % 60).toString().padStart(2, "0");
  return `${m}:${sec}`;
}

const STATUS_LABEL: Record<string, string> = {
  connecting: "Connecting…",
  ringing:    "Ringing…",
  "in-call":  "Connected",
  ended:      "Call Ended",
  error:      "Call Failed",
};

const STATUS_COLOR: Record<string, string> = {
  connecting: "bg-slate-100 text-slate-700",
  ringing:    "bg-brand-100 text-primary",
  "in-call":  "bg-success-100 text-success-700",
  ended:      "bg-slate-100 text-slate-600",
  error:      "bg-danger-100 text-danger-700",
};

export default function CallModal({ call, onHangUp }: { call: ActiveCall; onHangUp: () => void }) {
  const isActive = call.status === "connecting" || call.status === "ringing" || call.status === "in-call";
  const statusTone = STATUS_COLOR[call.status] ?? "bg-slate-100 text-slate-700";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div className="w-full max-w-sm overflow-hidden rounded-card border border-border bg-white text-foreground shadow-premium">
        <div className="px-6 pt-8 pb-6 text-center">
          <div className="mx-auto mb-4 flex size-16 items-center justify-center rounded-full bg-brand-100 text-primary">
            <Phone className="w-7 h-7" />
          </div>
          <p className="text-xl font-bold">{call.customerName}</p>
          <p className="mt-1 text-sm text-muted-foreground">{call.phone}</p>
          <p className={`mx-auto mt-3 inline-flex rounded-pill px-3 py-1 text-sm font-medium ${statusTone}`}>{STATUS_LABEL[call.status]}</p>
          {call.status === "in-call" && (
            <p className="text-2xl font-mono font-bold mt-1">{fmt(call.duration)}</p>
          )}
        </div>

        <div className="px-6 pb-8 flex justify-center">
          {isActive ? (
            <button
              onClick={onHangUp}
              className="flex size-16 items-center justify-center rounded-full border border-danger-500 bg-white text-danger-700 transition-colors hover:bg-danger-100"
              aria-label="End call"
            >
              <PhoneOff className="w-7 h-7" />
            </button>
          ) : (
            <p className="pb-2 text-sm text-muted-foreground">{call.status === "error" ? "Check Twilio Voice configuration" : ""}</p>
          )}
        </div>
      </div>
    </div>
  );
}
