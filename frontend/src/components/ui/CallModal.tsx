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
  connecting: "bg-slate-700",
  ringing:    "bg-blue-700",
  "in-call":  "bg-success-600",
  ended:      "bg-slate-500",
  error:      "bg-danger-600",
};

export default function CallModal({ call, onHangUp }: { call: ActiveCall; onHangUp: () => void }) {
  const isActive = call.status === "connecting" || call.status === "ringing" || call.status === "in-call";
  const bg = STATUS_COLOR[call.status] ?? "bg-slate-700";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 bg-black/60">
      <div className={`w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl text-white ${bg} transition-colors duration-500`}>
        <div className="px-6 pt-8 pb-6 text-center">
          <div className="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center mx-auto mb-4">
            <Phone className="w-7 h-7" />
          </div>
          <p className="text-xl font-bold">{call.customerName}</p>
          <p className="text-sm text-white/70 mt-1">{call.phone}</p>
          <p className="text-sm font-medium mt-3 tracking-wide">{STATUS_LABEL[call.status]}</p>
          {call.status === "in-call" && (
            <p className="text-2xl font-mono font-bold mt-1">{fmt(call.duration)}</p>
          )}
        </div>

        <div className="px-6 pb-8 flex justify-center">
          {isActive ? (
            <button
              onClick={onHangUp}
              className="w-16 h-16 rounded-full bg-red-500 hover:bg-red-600 flex items-center justify-center shadow-lg transition-colors"
            >
              <PhoneOff className="w-7 h-7" />
            </button>
          ) : (
            <p className="text-white/50 text-sm pb-2">{call.status === "error" ? "Check Twilio Voice configuration" : ""}</p>
          )}
        </div>
      </div>
    </div>
  );
}
