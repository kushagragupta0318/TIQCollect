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

  const startCall = useCallback(async (phone: string, customerName: string) => {
    try {
      setActiveCall({ customerName, phone, status: "connecting", duration: 0 });

      if (!deviceRef.current) {
        const { token } = await getVoiceToken();
        // Call.Codec is the union the SDK actually accepts; the literals
        // widen to string[] without it, which is why this was cast away.
        const device = new Device(token, {
          codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU],
        });
        await device.register();
        deviceRef.current = device;
      }

      const e164 = phone.startsWith("+") ? phone : `+91${phone.replace(/^0+/, "")}`;
      const call = await deviceRef.current.connect({ params: { PhoneTo: e164 } });
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
