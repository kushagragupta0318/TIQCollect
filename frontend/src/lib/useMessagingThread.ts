// One open thread: the message list (polled live), an optimistic send that
// makes the UI feel instant instead of waiting on the round trip, and the
// small behaviours a real chat has (Enter to send, auto-scroll to the
// newest message). Shared by both portals' ThreadPanel — the hard part
// (cache reconciliation) has one definition; each portal only renders it.
import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getThread, postMessage, setEscalationStatus,
  type SenderSide, type ThreadDetail, type ThreadMessage, type ThreadStatus, type ThreadSubjectType,
} from "@/api/messaging";
import { MESSAGING_INBOX_KEY } from "./useMessagingInbox";

const POLL_MS = 6_000;

/** A message still in flight — client-only, never sent to or read from the
 *  server. Lets the bubble show a "Sending…" affordance until the real row
 *  (with its real id) replaces it. */
export interface DisplayMessage extends ThreadMessage {
  pending?: boolean;
}

function threadKey(subjectType: ThreadSubjectType, subjectId: string) {
  return ["messaging", "thread", subjectType, subjectId] as const;
}

export function useMessagingThread(
  subjectType: ThreadSubjectType, subjectId: string, mySide: SenderSide,
) {
  const qc = useQueryClient();
  const key = threadKey(subjectType, subjectId);

  const q = useQuery({ queryKey: key, queryFn: () => getThread(subjectType, subjectId), refetchInterval: POLL_MS });

  // GET .../threads/{subject_type}/{subject_id} is what marks the thread
  // read server-side (messaging_service.py). The inbox's unread dot needs
  // telling exactly once per thread opened — not on every 6s poll of the
  // same thread, which would re-invalidate the inbox for no reason.
  const markedReadFor = useRef<string | null>(null);
  useEffect(() => {
    if (q.isSuccess && markedReadFor.current !== subjectId) {
      markedReadFor.current = subjectId;
      qc.invalidateQueries({ queryKey: MESSAGING_INBOX_KEY });
    }
  }, [q.isSuccess, subjectId, qc]);

  const send = useMutation({
    mutationFn: (body: string) => postMessage(subjectType, subjectId, body),
    onMutate: async (body: string) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<ThreadDetail>(key);
      const optimistic: DisplayMessage = {
        id: `optimistic-${Date.now()}`, sender_user_id: "", sender_side: mySide,
        body, created_at: new Date().toISOString(), pending: true,
      };
      qc.setQueryData<ThreadDetail>(key, (old) =>
        old ? { ...old, messages: [...old.messages, optimistic] } : old);
      return { previous };
    },
    onError: (_err, _body, ctx) => {
      // The optimistic row disappears with the rollback — an honest "this
      // didn't send" rather than leaving a bubble that was never real.
      if (ctx?.previous) qc.setQueryData(key, ctx.previous);
    },
    onSuccess: (real) => {
      qc.setQueryData(key, real);
      qc.invalidateQueries({ queryKey: MESSAGING_INBOX_KEY });
    },
  });

  const changeStatus = useMutation({
    mutationFn: (status: ThreadStatus) => setEscalationStatus(subjectId, status),
    onSuccess: (real) => {
      qc.setQueryData(key, real);
      qc.invalidateQueries({ queryKey: MESSAGING_INBOX_KEY });
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
