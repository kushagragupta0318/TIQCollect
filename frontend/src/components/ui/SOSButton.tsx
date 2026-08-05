import { clsx } from "clsx";
import { AlertTriangle } from "lucide-react";
import { toast } from "react-hot-toast";
import { triggerSOS as apiTriggerSOS, cancelSOS as apiCancelSOS } from "@/api/agent";
import { useSOSStore } from "@/store/sosStore";

function getCoords(): Promise<{ lat: number; lon: number }> {
  return new Promise((resolve) => {
    const fallback = () => resolve({ lat: 28.4595, lon: 77.0266 });
    setTimeout(fallback, 2500);
    navigator.geolocation?.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
      fallback,
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
    try {
      const { lat, lon } = await getCoords();
      await apiTriggerSOS(lat, lon);
      setSosActive(true);
      toast.error(
        "🚨 SOS SENT — Your manager has been alerted with your location. Stay calm.",
        { duration: 12000 }
      );
    } catch {
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
