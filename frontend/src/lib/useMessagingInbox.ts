// The inbox query, shared by both portals' Messaging pages AND their nav
// badges — one query key, so a badge polling in the background and an open
// Messaging page share the same request instead of two separate polls
// hitting GET /messaging/threads. Poll gated to the focused tab
// (refetchIntervalInBackground: false): live unread shouldn't mean hammering
// the endpoint from a backgrounded one.
import { useQuery } from "@tanstack/react-query";
import { listThreads } from "@/api/messaging";

export const MESSAGING_INBOX_KEY = ["messaging", "inbox"] as const;
const POLL_MS = 6_000;

export function useMessagingInbox() {
  return useQuery({
    queryKey: MESSAGING_INBOX_KEY,
    queryFn: () => listThreads(),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  });
}

/** For a nav icon's unread dot. `pending` is a per-thread "awaiting my
 *  reply" state the server already derives; `unread` (thread_reads) is the
 *  real "there's something here I haven't opened yet" signal — the badge
 *  uses unread, not pending, since a thread can be pending without having
 *  NEW unseen text (e.g. after a page refresh that didn't re-open it). */
export function useUnreadMessagingCount(): number {
  const q = useMessagingInbox();
  return q.data?.filter((t) => t.unread).length ?? 0;
}
