import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { getBeat, getHomeSummary } from "@/api/agent";
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
  ptps_due_today: number;
  check_in_status: string;
  sos_active: boolean;
  // Route shape for the beat map (2026-09-08). All optional: beats planned
  // before that date carry none, and a planning run where OSRM was unreachable
  // records route_source "haversine" with no polyline — BeatRouteMap reads the
  // source and declines to draw straight lines as though they were roads.
  route_geometry?: string | null;
  route_source?: string | null;
  start_latitude?: number | null;
  start_longitude?: number | null;
}

export interface HomeSummaryData {
  cases_today: number;
  visits_done: number;
  amount_collected_today: number;
  total_target_today: number;
  ptps_due_today: number;
  check_in_status: string;
  beat_status: string | null;
  sos_active: boolean;
}

interface BeatCtxValue {
  beat: BeatData | null;
  summary: HomeSummaryData | null;
  loading: boolean;
  refresh: () => Promise<void>;
  patch: (partial: Partial<BeatData>) => void;
  patchSummary: (partial: Partial<HomeSummaryData>) => void;
}

const BeatContext = createContext<BeatCtxValue>({
  beat: null,
  summary: null,
  loading: true,
  refresh: async () => {},
  patch: () => {},
  patchSummary: () => {},
});

export function BeatProvider({ children }: { children: ReactNode }) {
  const [beat, setBeat] = useState<BeatData | null>(null);
  const [summary, setSummary] = useState<HomeSummaryData | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [beatRes, summaryRes] = await Promise.allSettled([
        getBeat(),
        getHomeSummary(),
      ]);

      if (beatRes.status === "fulfilled" && beatRes.value) {
        setBeat(beatRes.value as BeatData);
      } else {
        setBeat(null);
      }

      if (summaryRes.status === "fulfilled" && summaryRes.value) {
        setSummary(summaryRes.value as HomeSummaryData);
      }
    } catch {
      // best-effort handling
    }
  }, []);

  const patch = useCallback((partial: Partial<BeatData>) => {
    setBeat((prev) => (prev ? { ...prev, ...partial } : prev));
  }, []);

  const patchSummary = useCallback((partial: Partial<HomeSummaryData>) => {
    setSummary((prev) => (prev ? { ...prev, ...partial } : prev));
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    const handleVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", handleVisible);
    return () => document.removeEventListener("visibilitychange", handleVisible);
  }, [refresh]);

  return (
    <BeatContext.Provider value={{ beat, summary, loading, refresh, patch, patchSummary }}>
      {children}
    </BeatContext.Provider>
  );
}

export function useBeat() {
  return useContext(BeatContext);
}
