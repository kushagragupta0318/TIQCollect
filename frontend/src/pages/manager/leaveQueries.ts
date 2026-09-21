// 2026-09-21 — the one query behind the Leave window and the Agents-page
// button. In its own file so the component modules export only components
// (react-refresh rule).
import { useQuery } from "@tanstack/react-query";
import { getLeaveRequests } from "@/api/manager";
import { LIVE } from "@/lib/liveQuery";

export const LEAVE_QUERY_KEY = ["manager", "leave-requests"] as const;

export function useLeaveRequests() {
  return useQuery({
    queryKey: LEAVE_QUERY_KEY,
    queryFn: () => getLeaveRequests(),
    retry: false, staleTime: 0, ...LIVE,
  });
}
