import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { getBeat } from "@/api/agent";
import type { Case } from "@/types";

export interface BeatData {
  id: string;
  beat_number: string;
  beat_date: string;
  status: string;
  estimated_distance_km: number;
  estimated_duration_minutes: number;
  total_cases: number;
  total_target_amount: number;
  ordered_case_ids: string[];
  cases_visited_today: number;
  visited_today_ids: string[];
  amount_collected_today: number;
  cases: Case[];
  // consolidated from /home-summary
  ptps_due_today: number;
  check_in_status: string;
  sos_active: boolean;
}

interface BeatCtxValue {
  beat: BeatData | null;
  loading: boolean;
  refresh: () => Promise<void>;
  patch: (partial: Partial<BeatData>) => void;
}

const BeatContext = createContext<BeatCtxValue>({
  beat: null,
  loading: true,
  refresh: async () => {},
  patch: () => {},
});

export function BeatProvider({ children }: { children: ReactNode }) {
  const [beat, setBeat] = useState<BeatData | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const data = await getBeat();
    setBeat(data as BeatData);
  }, []);

  const patch = useCallback((partial: Partial<BeatData>) => {
    setBeat((prev) => (prev ? { ...prev, ...partial } : prev));
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  // Re-sync whenever the user switches back to the tab — keeps Home, Cases,
  // and Beat Map consistent without requiring a manual page refresh.
  useEffect(() => {
    const handleVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", handleVisible);
    return () => document.removeEventListener("visibilitychange", handleVisible);
  }, [refresh]);

  return (
    <BeatContext.Provider value={{ beat, loading, refresh, patch }}>
      {children}
    </BeatContext.Provider>
  );
}

export function useBeat() {
  return useContext(BeatContext);
}
