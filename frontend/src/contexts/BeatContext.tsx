// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-10 — Migrated from hand-rolled `useState` + `useEffect` fetching to
//   the @tanstack/react-query client that has been mounted in App.tsx, and
//   unused, since the project began. The effect this replaces called
//   `refresh().finally(() => setLoading(false))`, writing state synchronously
//   inside an effect — `react-hooks/set-state-in-effect`, one of the errors
//   keeping CI red.
//
//   EVERY OPTION BELOW EXISTS TO PRESERVE BEHAVIOUR, NOT TO CONFIGURE WELL.
//   The client sets `staleTime: 30_000, retry: 1` globally and both would have
//   changed what this provider puts on the wire; see AS_BEFORE.
//
//   The two fetches keep their asymmetric failure handling, which was NOT
//   incidental: the old code ran them through `Promise.allSettled` and then set
//   `beat` to null on a rejection while leaving `summary` untouched. So a failed
//   beat load clears the beat, and a failed summary load keeps the last one.
//   That is reproduced exactly — the beat queryFn swallows and returns null, the
//   summary queryFn throws and React Query retains the previous data.
// ───────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getBeat, getHomeSummary } from "@/api/agent";
import { BeatContext, type BeatData, type HomeSummaryData } from "./useBeat";

const BEAT_KEY = ["agent", "beat"] as const;
const SUMMARY_KEY = ["agent", "home-summary"] as const;

// retry: false            — the old code retried nothing; the global `retry: 1`
//                           would silently double every failed request.
// staleTime: 0            — the old code fetched on every mount.
// refetchOnWindowFocus    — off, because the provider already has its own
//   / refetchOnReconnect    `visibilitychange` listener below. React Query's
//                           defaults would fire a SECOND refetch beside it.
const AS_BEFORE = {
  retry: false,
  staleTime: 0,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
} as const;

export function BeatProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const beatQ = useQuery({
    queryKey: BEAT_KEY,
    // Swallowed on purpose: `Promise.allSettled` + `setBeat(null)` meant a
    // failed load read as "no beat today", never as an error. Throwing here
    // would leave the previous beat on screen instead.
    queryFn: async (): Promise<BeatData | null> => {
      try {
        return ((await getBeat()) as BeatData) ?? null;
      } catch {
        return null;
      }
    },
    ...AS_BEFORE,
  });

  const summaryQ = useQuery({
    // Throws, so React Query keeps the last good summary — which is what the
    // old code did by only assigning on `status === "fulfilled"`.
    queryKey: SUMMARY_KEY,
    queryFn: async () => (await getHomeSummary()) as HomeSummaryData,
    ...AS_BEFORE,
  });

  const beat = beatQ.data ?? null;
  const summary = summaryQ.data ?? null;
  // `isPending` is "no data yet". The old `loading` was set false in a
  // `.finally()` after the FIRST refresh and never set true again, so a later
  // refresh never re-showed the skeleton. `isFetching` would have.
  const loading = beatQ.isPending || summaryQ.isPending;

  const refetchBeat = beatQ.refetch;
  const refetchSummary = summaryQ.refetch;
  const refresh = useCallback(async () => {
    await Promise.all([refetchBeat(), refetchSummary()]);
  }, [refetchBeat, refetchSummary]);

  // Optimistic local edits — a visit is recorded, the beat updates without a
  // round trip. `setQueryData` is the cache-level equivalent of the functional
  // `setBeat` these replace, including the "only if something is already
  // there" guard.
  const patch = useCallback((partial: Partial<BeatData>) => {
    queryClient.setQueryData<BeatData | null>(BEAT_KEY, (prev) =>
      prev ? { ...prev, ...partial } : prev);
  }, [queryClient]);

  const patchSummary = useCallback((partial: Partial<HomeSummaryData>) => {
    queryClient.setQueryData<HomeSummaryData | undefined>(SUMMARY_KEY, (prev) =>
      prev ? { ...prev, ...partial } : prev);
  }, [queryClient]);

  // Kept verbatim rather than replaced by `refetchOnWindowFocus`. The two are
  // not the same event: `visibilitychange` fires when the tab is revealed,
  // which on a phone includes returning from another app, and React Query's
  // focus handling would additionally be gated by staleTime.
  useEffect(() => {
    const handleVisible = () => {
      if (document.visibilityState === "visible") void refresh();
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
