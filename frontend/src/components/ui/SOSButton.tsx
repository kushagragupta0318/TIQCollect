// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-18 — getCoords() used to race the browser against a 2.5s timer and,
//   on timeout OR error, resolve to hardcoded coordinates (28.4595, 77.0266 —
//   Gurugram). Every SOS therefore carried a position, and the manager had no
//   way to tell a real one from the constant. In an emergency a confidently
//   wrong location is worse than none: it sends help to the wrong place.
//
//   Now: 10s to acquire (a phone indoors routinely needs more than 2.5s),
//   nothing sent when it fails, and the server falls back to the last tracked
//   fix from the location trail — labelled as last-known, with its age. The
//   agent is told which of the three happened rather than always being told
//   their location went out.
// ────────────────────────────────────────────────────────────────────────────
import { clsx } from "clsx";
import { AlertTriangle } from "lucide-react";
import { toast } from "react-hot-toast";
import { triggerSOS as apiTriggerSOS, cancelSOS as apiCancelSOS } from "@/api/agent";
import { useSOSStore } from "@/store/sosStore";

interface Fix {
  lat: number;
  lon: number;
  accuracy: number | null;
}

// A cold GPS fix indoors commonly takes 5-8 seconds. The old 2.5s window meant
// the fallback fired most of the time it mattered.
const ACQUIRE_TIMEOUT_MS = 10_000;

/** Resolves to a real fix, or null. Never invents one. */
function getCoords(): Promise<Fix | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(null);
      return;
    }
    let settled = false;
    const done = (v: Fix | null) => {
      if (settled) return;
      settled = true;
      resolve(v);
    };
    const timer = setTimeout(() => done(null), ACQUIRE_TIMEOUT_MS);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        clearTimeout(timer);
        done({
          lat: p.coords.latitude,
          lon: p.coords.longitude,
          accuracy: Number.isFinite(p.coords.accuracy) ? p.coords.accuracy : null,
        });
      },
      () => {
        clearTimeout(timer);
        done(null);
      },
      // maximumAge lets a fix the shared watcher already has satisfy this
      // instantly, which is the common case while the agent is on duty.
      { enableHighAccuracy: true, timeout: ACQUIRE_TIMEOUT_MS, maximumAge: 30_000 },
    );
  });
}

export function SOSButton({ compact = false }: { compact?: boolean }) {
  const { sosActive, sosLoading, setSosActive, setSosLoading } = useSOSStore();

  async function handlePress() {
    if (sosLoading) return;

    if (sosActive) {
      setSosLoading(true);
      try {
        await apiCancelSOS();
        setSosActive(false);
        toast.success("SOS deactivated — you're marked safe");
      } catch {
        toast.error("Could not cancel SOS — call your manager directly");
      } finally {
        setSosLoading(false);
      }
      return;
    }

    setSosLoading(true);
    // Held while GPS is acquired so a 10s wait does not look like a dead button.
    const pending = toast.loading("Sending SOS — getting your location…");
    try {
      const fix = await getCoords();
      const res = await apiTriggerSOS(fix?.lat, fix?.lon, fix?.accuracy);
      setSosActive(true);
      toast.dismiss(pending);

      // Report what was actually sent. The three cases are materially different
      // to the person in trouble, so they are not collapsed into one message.
      if (res.location_quality === "LIVE") {
        toast.error("🚨 SOS SENT — your manager has your exact location. Stay calm.",
          { duration: 12_000 });
      } else if (res.location_quality === "LAST_KNOWN") {
        const mins = Math.max(1, Math.round((res.location_age_seconds ?? 0) / 60));
        toast.error(
          `🚨 SOS SENT — GPS did not respond, so your manager was sent your last known location (${mins} min old). Move to open sky if you can.`,
          { duration: 15_000 },
        );
      } else {
        toast.error(
          "🚨 SOS SENT — but your LOCATION COULD NOT BE SENT. Call your manager and 112 now.",
          { duration: 20_000 },
        );
      }
    } catch {
      toast.dismiss(pending);
      toast.error("SOS failed — call emergency: 112");
    } finally {
      setSosLoading(false);
    }
  }

  return (
    <button
      onClick={handlePress}
      disabled={sosLoading}
      title={sosActive ? "SOS active — tap to cancel" : "Trigger SOS emergency alert"}
      className={clsx(
        "tap-target flex items-center justify-center gap-1.5 rounded-xl font-bold text-sm transition-all select-none",
        compact ? "px-2.5 py-1.5" : "px-3 py-2 shadow-lg",
        sosActive
          ? "bg-danger-500 text-white ring-2 ring-danger-300 ring-offset-1 animate-pulse"
          : "bg-danger-600 hover:bg-danger-700 active:scale-95 text-white",
        sosLoading && "opacity-60 cursor-wait",
      )}
    >
      <AlertTriangle className={clsx("flex-shrink-0", compact ? "w-3.5 h-3.5" : "w-4 h-4")} />
      <span>{sosActive ? "SOS ACTIVE" : "SOS"}</span>
    </button>
  );
}
