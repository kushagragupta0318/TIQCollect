// The inbox query, shared by a portal's own Messaging page AND its nav
// badge — one query key per axis, so a badge polling in the background and
// an open Messaging page share the same request instead of two separate
// polls hitting the same endpoint. Poll gated to the focused tab
// (refetchIntervalInBackground: false): live unread shouldn't mean hammering
// the endpoint from a backgrounded one.
import { useQuery, type QueryKey } from "@tanstack/react-query";
import { listAgentThreads, listThreads, type InboxThread } from "@/api/messaging";

// Perf audit, 20 concurrent users: the app-wide unread dot alone (one poll
// per signed-in user, regardless of whether they have Messaging open) was
// ~200 req/min against GET /messaging/threads at the old 6s. 15s keeps the
// "live" feel — still faster than a user would manually refresh — at a
// third the request rate. Checked the payload itself too: an inbox row is a
// title, a status, a <=140-char preview and a few booleans — at pilot scale
// (per CLAUDE.md, ~100 agents) that's tens of KB even at full size, not
// worth a separate count-only endpoint for.
const POLL_MS = 15_000;

function useInboxQuery(queryKey: QueryKey, fetcher: () => Promise<InboxThread[]>) {
  return useQuery({
    queryKey,
    queryFn: fetcher,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  });
}

function unreadCount(rows: InboxThread[] | undefined): number {
  // `pending` is a per-thread "awaiting my reply" state the server already
  // derives; `unread` (thread_reads) is the real "there's something here I
  // haven't opened yet" signal — the badge uses unread, not pending, since a
  // thread can be pending without having NEW unseen text (e.g. after a page
  // refresh that didn't re-open it).
  return rows?.filter((t) => t.unread).length ?? 0;
}

// ── bank<->agency axis (escalations + reversals) ────────────────────────────

export const MESSAGING_INBOX_KEY = ["messaging", "inbox"] as const;

export function useMessagingInbox() {
  return useInboxQuery(MESSAGING_INBOX_KEY, () => listThreads());
}

/** For the Messages nav icon's unread dot on the bank<->agency axis. */
export function useUnreadMessagingCount(): number {
  return unreadCount(useMessagingInbox().data);
}

// ── manager<->agent axis ────────────────────────────────────────────────────

export const AGENT_MESSAGING_INBOX_KEY = ["messaging", "agent-inbox"] as const;

export function useAgentMessagingInbox() {
  return useInboxQuery(AGENT_MESSAGING_INBOX_KEY, () => listAgentThreads());
}

/** For the agent-chat nav icon's unread dot — a separate surface from the
 *  bank<->agency one above, so a separate count: an agency's own escalation
 *  inbox and its agent-chats inbox are different lists, never combined into
 *  one badge. */
export function useUnreadAgentMessagingCount(): number {
  return unreadCount(useAgentMessagingInbox().data);
}
