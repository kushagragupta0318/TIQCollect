// Bank<->agency messaging (backend/app/api/v1/endpoints/messaging.py,
// services/messaging_service.py). One client for both portals: the manager
// and bank Messaging pages read and post through the exact same routes and
// row shapes, so there is one definition of what a thread or an inbox row
// looks like, not a manager-side copy and a bank-side copy.
//
// sender_side is DERIVED server-side from the caller's scope (messaging_service
// .MessagingService._side) and is never sent by this client — there is no
// field for it on MessageIn, by design, not by omission.
import api from "./axios";

export type ThreadSubjectType = "REVERSAL" | "PLACEMENT" | "ISSUE" | "AGENT_DIRECT";
/** RESOLVED exists only for an ISSUE's own status (escalation_issues.status);
 *  a REVERSAL thread's status is OPEN/CLOSED, never RESOLVED, and an
 *  AGENT_DIRECT thread has no status concept at all. One union covers all
 *  of them rather than a conditional type for one extra value nothing
 *  downstream needs to distinguish. */
export type ThreadStatus = "OPEN" | "RESOLVED" | "CLOSED";
/** AGENT only appears on the manager<->agent axis; BANK/AGENCY only on the
 *  bank<->agency one. Never mixed within a single thread. */
export type SenderSide = "BANK" | "AGENCY" | "AGENT";

export interface ThreadMessage {
  id: string;
  sender_user_id: string;
  sender_side: SenderSide;
  body: string;
  created_at: string | null;
}

export interface Thread {
  id: string;
  subject_type: ThreadSubjectType;
  subject_id: string;
  status: ThreadStatus;
  bank_id: string;
  agency_id: string;
}

export interface ThreadDetail {
  thread: Thread | null;
  subject_type: ThreadSubjectType;
  subject_id: string;
  messages: ThreadMessage[];
}

/** One row of GET /messaging/threads — the full inbox, newest activity first. */
export interface InboxThread {
  thread_id: string;
  subject_type: ThreadSubjectType;
  subject_id: string;
  /** Always has one: ISSUE -> the ticket's own title; REVERSAL -> a derived
   *  short label ("Reversal <shortid>") — there is always something to show,
   *  never a raw id. */
  title: string;
  status: ThreadStatus;
  /** Which side the CALLER is, so the inbox can label the other party
   *  ("Bank" on the agency's list, "Agency" on the bank's). */
  counterparty: SenderSide;
  last_message: { sender_side: SenderSide; preview: string; at: string | null };
  /** True when the thread is awaiting the caller's reply (the last message
   *  came from the other side) — list_pending's own rule, carried onto every
   *  row instead of being a separate endpoint to cross-reference. */
  pending: boolean;
  /** Real unread, off collections.thread_reads — not derived from `pending`.
   *  Opening the thread (GET .../threads/{subject_type}/{subject_id}) is
   *  what clears it. */
  unread: boolean;
  message_count: number;
}

export async function listThreads(pending?: boolean): Promise<InboxThread[]> {
  const { data } = await api.get<{ threads: InboxThread[] }>("/messaging/threads",
    { params: pending === undefined ? undefined : { pending } });
  return data.threads;
}

export async function getThread(subjectType: ThreadSubjectType, subjectId: string): Promise<ThreadDetail> {
  const { data } = await api.get<ThreadDetail>(`/messaging/threads/${subjectType}/${subjectId}`);
  return data;
}

export async function postMessage(
  subjectType: ThreadSubjectType, subjectId: string, body: string,
): Promise<ThreadDetail> {
  const { data } = await api.post<ThreadDetail>(`/messaging/threads/${subjectType}/${subjectId}/messages`, { body });
  return data;
}

export interface EscalationIssue {
  id: string;
  title: string;
  status: ThreadStatus;
  bank_id: string;
  agency_id: string;
  created_by_user_id: string;
  created_at: string;
}

export interface EscalationOpened {
  issue: EscalationIssue;
  thread: Thread;
  messages: ThreadMessage[];
}

/** Opens a new ISSUE thread (messaging.escalate — the agency side only; the
 *  bank never opens an escalation, it only replies on one). `issue.id` is
 *  the subject_id for every later call on this thread (reply, status). */
export async function raiseEscalation(title: string, body: string): Promise<EscalationOpened> {
  const { data } = await api.post<EscalationOpened>("/messaging/escalations", { title, body });
  return data;
}

/** Resolve, close or reopen an escalation — the owning agency or the bank,
 *  both audited (ESCALATION_STATUS_CHANGED). Status lives on the issue, not
 *  the thread: only an ISSUE has one (a REVERSAL thread's status is fixed by
 *  its own subject, never changed here), so this takes the issue id, not a
 *  (subject_type, subject_id) pair. */
export async function setEscalationStatus(issueId: string, status: ThreadStatus): Promise<EscalationOpened> {
  const { data } = await api.post<EscalationOpened>(`/messaging/escalations/${issueId}/status`, { status });
  return data;
}

// ── manager<->agent direct chat: a second axis, same envelope shapes,
//    a different URL family (/messaging/agent-threads, not
//    /messaging/threads/{subject_type}/{subject_id}) — an agent sees only
//    their own thread, a manager/admin sees every one of their agency's
//    agents'. No status concept here (no escalation_issues row behind it). ──

export async function listAgentThreads(pending?: boolean): Promise<InboxThread[]> {
  const { data } = await api.get<{ threads: InboxThread[] }>("/messaging/agent-threads",
    { params: pending === undefined ? undefined : { pending } });
  return data.threads;
}

export async function getAgentThread(agentId: string): Promise<ThreadDetail> {
  const { data } = await api.get<ThreadDetail>(`/messaging/agent-threads/${agentId}`);
  return data;
}

export async function postAgentMessage(agentId: string, body: string): Promise<ThreadDetail> {
  const { data } = await api.post<ThreadDetail>(`/messaging/agent-threads/${agentId}/messages`, { body });
  return data;
}
