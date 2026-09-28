// ─── CHANGELOG ─────────────────────────────────────────────────────────────
// 2026-09-24 (hotfix AU-2) — the call now names a CASE, never a number. This
//   hook used to send the borrower's number as the custom param `PhoneTo`,
//   and the server dialled whatever arrived there — so any agent (or anyone
//   replaying the webhook) could ring any number on the company's Twilio
//   account. The server now resolves the number from `CaseId` for a case
//   assigned to the caller and ignores any number a client sends. The phone
//   passed in here is for the call screen only. Tokens now live five minutes,
//   so the Device refreshes its token before it expires.
// ─────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback } from "react";
import { Device, Call } from "@twilio/voice-sdk";
import { getVoiceToken } from "@/api/agent";

export type CallStatus = "idle" | "connecting" | "ringing" | "in-call" | "ended" | "error";

export interface ActiveCall {
  customerName: string;
  phone: string;
  status: CallStatus;
  duration: number;
}

export function useVoiceCall() {
  const deviceRef = useRef<Device | null>(null);
  const callRef = useRef<Call | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [activeCall, setActiveCall] = useState<ActiveCall | null>(null);

  const clearTimer = () => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  };

  // 2026-09-24 (audit gate 2): the call is placed for a CASE. The server
  // resolves the borrower's number from it (and refuses a case that is not
  // yours, outside contact hours, do-not-contact or demo data); `phone` is
  // display only and is never sent.
  const startCall = useCallback(async (caseId: string, phone: string, customerName: string) => {
    try {
      setActiveCall({ customerName, phone, status: "connecting", duration: 0 });

      if (!deviceRef.current) {
        const { token } = await getVoiceToken();
        // Call.Codec is the union the SDK actually accepts; the literals
        // widen to string[] without it, which is why this was cast away.
        const device = new Device(token, {
          codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU],
        });
        // Five-minute tokens: renew before expiry so a later call still works.
        device.on("tokenWillExpire", async () => {
          try {
            const { token: fresh } = await getVoiceToken();
            device.updateToken(fresh);
          } catch {
            // The next call will fail and show the error state; nothing to do here.
          }
        });
        await device.register();
        deviceRef.current = device;
      }

      const call = await deviceRef.current.connect({ params: { CaseId: caseId } });
      callRef.current = call;

      setActiveCall((a) => a ? { ...a, status: "ringing" } : a);

      call.on("accept", () => {
        setActiveCall((a) => a ? { ...a, status: "ringing" } : a);
        timerRef.current = setInterval(() => {
          setActiveCall((a) => a ? { ...a, duration: a.duration + 1 } : a);
        }, 1000);
      });

      call.on("disconnect", () => {
        clearTimer();
        setActiveCall((a) => a ? { ...a, status: "ended" } : a);
        setTimeout(() => setActiveCall(null), 2500);
        callRef.current = null;
      });

      call.on("error", () => {
        clearTimer();
        setActiveCall((a) => a ? { ...a, status: "error" } : a);
        setTimeout(() => setActiveCall(null), 2500);
        callRef.current = null;
      });
    } catch {
      setActiveCall((a) => a ? { ...a, status: "error" } : a);
      setTimeout(() => setActiveCall(null), 2500);
    }
  }, []);

  const hangUp = useCallback(() => {
    callRef.current?.disconnect();
    clearTimer();
  }, []);

  return { activeCall, startCall, hangUp };
}
