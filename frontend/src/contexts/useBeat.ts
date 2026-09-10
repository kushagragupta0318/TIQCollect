/**
 * The beat context object, its types, and the hook that reads it.
 *
 * SPLIT OUT OF BeatContext.tsx, 2026-09-10. Named for the hook rather than the
 * context because `beatContext.ts` beside `BeatContext.tsx` differs only by
 * case, and on a case-insensitive filesystem `./beatContext` can resolve to
 * either one. That file exported both a component
 * (`BeatProvider`) and non-components (`useBeat`, the types), which trips
 * `react-refresh/only-export-components` — one of the 18 lint errors keeping CI
 * red. The rule is not cosmetic: a module mixing the two cannot be hot-replaced
 * cleanly, so editing the provider silently remounts every consumer and drops
 * their state.
 *
 * Nothing here changed except its address. `BeatProvider` still owns all the
 * state and still lives in BeatContext.tsx, which is now a component module and
 * nothing else.
 */
import { createContext, useContext } from "react";
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

export const BeatContext = createContext<BeatCtxValue>({
  beat: null,
  summary: null,
  loading: true,
  refresh: async () => {},
  patch: () => {},
  patchSummary: () => {},
});

export function useBeat() {
  return useContext(BeatContext);
}
