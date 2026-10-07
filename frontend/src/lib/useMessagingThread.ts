// One open thread: the message list (polled live), an optimistic send that
// makes the UI feel instant instead of waiting on the round trip, and the
// small behaviours a real chat has (Enter to send, auto-scroll to the
// newest message). Shared by every axis' ThreadPanel — bank<->agency
// (escalations + reversals), manager<->agent — the hard part (cache
// reconciliation) has one definition; each caller only renders it and
// supplies its own endpoints, since the axes don't share a URL shape
// (/messaging/threads/{subject_type}/{subject_id} vs
// /messaging/agent-threads/{agent_id}) — no subjectType swap could cover
// both, so the hook takes the bound fetchers themselves.
import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import type { SenderSide, ThreadDetail, ThreadMessage, ThreadStatus } from "@/api/messaging";

// Perf audit, 20 concurrent users: the app-wide unread dot alone was ~200
// req/min against GET /messaging/threads at the old 6s. 15s keeps the
// "live" feel at a third the request rate. Same value as
// useMessagingInbox.ts's own POLL_MS, kept in step by hand — no shared
// constant between the two files, since an open thread and an inbox query
// are different endpoints with no reason to be coupled beyond both having
// survived the same load check.
const POLL_MS = 15_000;

/** A message still in flight — client-only, never sent to or read from the
 *  server. Lets the bubble show a "Sending…" affordance until the real row
 *  (with its real id) replaces it. */
export interface DisplayMessage extends ThreadMessage {
  pending?: boolean;
}

/** The three calls a thread needs, bound by the caller to whichever axis
 *  and id this thread is on. `setStatus` is omitted entirely for an axis
 *  with no status concept of its own (a REVERSAL thread, or an
 *  AGENT_DIRECT one — only an ISSUE has escalation_issues.status). */
export interface ThreadEndpoints {
  getThread: () => Promise<ThreadDetail>;
  postMessage: (body: string) => Promise<ThreadDetail>;
  setStatus?: (status: ThreadStatus) => Promise<ThreadDetail>;
}

export function useMessagingThread(
  queryKey: QueryKey, mySide: SenderSide, endpoints: ThreadEndpoints,
  /** Invalidated once per thread opened (not on every poll tick) — the
   *  inbox this thread's unread dot lives on. Different axes have
   *  different inboxes (MESSAGING_INBOX_KEY vs AGENT_MESSAGING_INBOX_KEY),
   *  so the caller names its own rather than the hook assuming one. */
  inboxKey: QueryKey,
) {
  const qc = useQueryClient();

  const q = useQuery({ queryKey, queryFn: endpoints.getThread, refetchInterval: POLL_MS });

  // GET .../threads/{subject_type}/{subject_id} (or its agent-axis sibling)
  // is what marks the thread read server-side (messaging_service.py). The
  // inbox's unread dot needs telling exactly once per thread opened, keyed
  // on the query key's own identity — not on every poll tick of the same
  // thread, which would re-invalidate the inbox for no reason.
  const keyIdentity = JSON.stringify(queryKey);
  const markedReadFor = useRef<string | null>(null);
  useEffect(() => {
    if (q.isSuccess && markedReadFor.current !== keyIdentity) {
      markedReadFor.current = keyIdentity;
      qc.invalidateQueries({ queryKey: inboxKey });
    }
    // inboxKey is a literal array from the caller, re-created every render;
    // comparing by reference would re-run this every render instead of once
    // per thread.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.isSuccess, keyIdentity, qc]);

  const send = useMutation({
    mutationFn: (body: string) => endpoints.postMessage(body),
    onMutate: async (body: string) => {
      await qc.cancelQueries({ queryKey });
      const previous = qc.getQueryData<ThreadDetail>(queryKey);
      const optimistic: DisplayMessage = {
        id: `optimistic-${Date.now()}`, sender_user_id: "", sender_side: mySide,
        body, created_at: new Date().toISOString(), pending: true,
      };
      qc.setQueryData<ThreadDetail>(queryKey, (old) =>
        old ? { ...old, messages: [...old.messages, optimistic] } : old);
      return { previous };
    },
    onError: (_err, _body, ctx) => {
      // The optimistic row disappears with the rollback — an honest "this
      // didn't send" rather than leaving a bubble that was never real.
      if (ctx?.previous) qc.setQueryData(queryKey, ctx.previous);
    },
    onSuccess: (real) => {
      qc.setQueryData(queryKey, real);
      qc.invalidateQueries({ queryKey: inboxKey });
    },
  });

  const changeStatus = useMutation({
    mutationFn: (status: ThreadStatus) => {
      if (!endpoints.setStatus) throw new Error("This thread has no status to change.");
      return endpoints.setStatus(status);
    },
    onSuccess: (real) => {
      qc.setQueryData(queryKey, real);
      qc.invalidateQueries({ queryKey: inboxKey });
    },
  });

  return {
    thread: q.data?.thread ?? null,
    messages: (q.data?.messages ?? []) as DisplayMessage[],
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
    send: (body: string) => send.mutate(body),
    sendPending: send.isPending,
    canChangeStatus: !!endpoints.setStatus,
    changeStatus: (status: ThreadStatus) => changeStatus.mutate(status),
    statusPending: changeStatus.isPending,
  };
}

/** The newest message in view on open and on every arrival — the server's
 *  own poll result included, not just a reply this tab sent. A separate
 *  hook, not a field on useMessagingThread's own return: bundling a ref
 *  into the same object as reactive state there trips the
 *  react-hooks/refs rule on every OTHER field read off that object too.
 *  jsdom (every test here) has no scrollIntoView at all, not even a
 *  no-op — guard the method itself, not just the ref, or every render
 *  under test throws. */
export function useAutoScrollOnChange(dep: number) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [dep]);
  return bottomRef;
}

/** Enter sends, Shift+Enter is a newline — the one keyboard rule every chat
 *  app shares. `onSend` is only called when there is something to send;
 *  the caller still owns disabling the Send button on an empty draft. */
export function handleComposeKeyDown(
  e: React.KeyboardEvent<HTMLTextAreaElement>, draft: string, onSend: () => void,
): void {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (draft.trim()) onSend();
  }
}
