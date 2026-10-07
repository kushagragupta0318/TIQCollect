// Messages: the agency's half of bank<->agency threads (backend messaging.py
// / messaging_service.py). A manager or admin can reply on any thread and can
// open a new general escalation to the bank (messaging.escalate — agency-only;
// the bank replies but never opens one). Every message is part of the audit
// record (MESSAGE_SENT, stage_audit) — said on screen, not left implied.
import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { MessageSquarePlus, ScrollText, Send } from "lucide-react";
import {
  getThread, listThreads, postMessage, raiseEscalation, setEscalationStatus,
  type InboxThread, type ThreadStatus, type ThreadSubjectType,
} from "@/api/messaging";
import { errorDetail } from "@/lib/apiError";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

const STATUS_VARIANT: Record<ThreadStatus, "blue" | "green" | "gray"> = {
  OPEN: "blue", RESOLVED: "green", CLOSED: "gray",
};

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function InboxRow({ row, active, onSelect }: { row: InboxThread; active: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`tap-target w-full rounded-control border px-4 py-3 text-left transition-colors ${
        active ? "border-primary bg-primary/5" : "border-[#E1E3E9] bg-white hover:bg-[#F7F8FA]"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-[13px] font-semibold text-slate-900">{row.title}</p>
        {row.unread && <span className="size-2 shrink-0 rounded-full bg-primary" aria-label="Unread" />}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <Badge variant={STATUS_VARIANT[row.status]}>{row.status}</Badge>
        <span className="text-[11px] text-slate-500">Bank</span>
        {row.pending && <Badge variant="orange">Awaiting your reply</Badge>}
      </div>
      <p className="mt-1.5 truncate text-[12px] text-slate-500">
        {row.last_message.sender_side === "AGENCY" ? "You: " : "Them: "}{row.last_message.preview}
      </p>
      <p className="mt-1 text-[10.5px] text-slate-400">{when(row.last_message.at)}</p>
    </button>
  );
}

function ThreadPanel({ subjectType, subjectId, onChanged }: {
  subjectType: ThreadSubjectType; subjectId: string; onChanged: () => void;
}) {
  const [body, setBody] = useState("");
  const q = useQuery({ queryKey: ["messaging", "thread", subjectType, subjectId], queryFn: () => getThread(subjectType, subjectId) });

  const send = useMutation({
    mutationFn: () => postMessage(subjectType, subjectId, body),
    onSuccess: () => { setBody(""); q.refetch(); onChanged(); },
  });
  const changeStatus = useMutation({
    mutationFn: (status: ThreadStatus) => setEscalationStatus(subjectId, status),
    onSuccess: () => { q.refetch(); onChanged(); },
  });

  if (q.isLoading) return <p className="py-10 text-center text-[13px] text-slate-500">Loading the conversation…</p>;
  if (q.isError) return <p className="py-10 text-center text-[13px] text-rose-600">{errorDetail(q.error, "This conversation could not load.")}</p>;
  const data = q.data;
  if (!data) return null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-[#E1E3E9] pb-3">
        <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <ScrollText className="size-3.5" aria-hidden="true" />
          Every message here is part of the audit record.
        </p>
        {subjectType === "ISSUE" && data.thread && (
          <select
            className="input text-[12px] py-1.5 px-2.5 w-auto tap-target-h"
            value={data.thread.status}
            onChange={(e) => changeStatus.mutate(e.target.value as ThreadStatus)}
            disabled={changeStatus.isPending}
          >
            <option value="OPEN">Open</option>
            <option value="RESOLVED">Resolved</option>
            <option value="CLOSED">Closed</option>
          </select>
        )}
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto py-4">
        {data.messages.map((m) => (
          <div key={m.id} className={`max-w-[80%] rounded-control px-3.5 py-2.5 ${
            m.sender_side === "AGENCY" ? "ml-auto bg-primary/5" : "bg-[#F7F8FA]"
          }`}>
            <p className="text-[11px] font-semibold text-slate-500">{m.sender_side === "AGENCY" ? "You" : "Bank"}</p>
            <p className="mt-0.5 whitespace-pre-wrap text-[13px] text-slate-900">{m.body}</p>
            <p className="mt-1 text-[10.5px] text-slate-400">{when(m.created_at)}</p>
          </div>
        ))}
        {data.messages.length === 0 && (
          <p className="py-8 text-center text-[12.5px] text-slate-500">No messages yet.</p>
        )}
      </div>

      <div className="border-t border-[#E1E3E9] pt-3">
        <textarea
          className="input text-[13px] w-full"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Reply…"
          rows={3}
        />
        <div className="mt-2 flex justify-end">
          <Button onClick={() => send.mutate()} disabled={!body.trim() || send.isPending}>
            <Send className="size-4" /> Send
          </Button>
        </div>
      </div>
    </div>
  );
}

function EscalateForm({ onOpened }: { onOpened: (subjectId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const create = useMutation({
    mutationFn: () => raiseEscalation(title.trim(), body.trim()),
    onSuccess: (res) => { setOpen(false); setTitle(""); setBody(""); onOpened(res.issue.id); },
  });

  if (!open) {
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        <MessageSquarePlus className="size-4" /> Escalate an issue
      </Button>
    );
  }
  return (
    <div className="rounded-control border border-[#E1E3E9] bg-white p-4 space-y-3">
      <p className="text-[13px] font-semibold text-slate-900">Escalate an issue to the bank</p>
      <input className="input text-[13px] tap-target-h" placeholder="Short title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className="input text-[13px] w-full" placeholder="What's going on, and what you need from the bank." rows={4}
                value={body} onChange={(e) => setBody(e.target.value)} />
      {create.isError && <p className="text-[12px] text-rose-600">{errorDetail(create.error, "Could not open the escalation.")}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        <Button onClick={() => create.mutate()} disabled={!title.trim() || !body.trim() || create.isPending}>
          Open escalation
        </Button>
      </div>
    </div>
  );
}

export default function ManagerMessagingPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<{ subjectType: ThreadSubjectType; subjectId: string } | null>(null);
  const q = useQuery({ queryKey: ["messaging", "inbox"], queryFn: () => listThreads() });
  const refreshInbox = () => qc.invalidateQueries({ queryKey: ["messaging", "inbox"] });

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Messages</h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">Threads with the bank — reversal disputes and escalations you've raised.</p>
        </div>
        <EscalateForm onOpened={(subjectId) => { setSelected({ subjectType: "ISSUE", subjectId }); refreshInbox(); }} />
      </div>

      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        <div className="max-h-[70vh] overflow-y-auto rounded-control border border-[#E1E3E9] bg-white p-3 space-y-2">
          {q.isLoading && <p className="py-8 text-center text-[13px] text-slate-500">Loading threads…</p>}
          {q.isError && <p className="py-8 text-center text-[13px] text-rose-600">{errorDetail(q.error, "Threads could not load.")}</p>}
          {q.data && q.data.length === 0 && (
            <p className="py-8 text-center text-[12.5px] text-slate-500">No conversations yet.</p>
          )}
          {q.data?.map((row) => (
            <InboxRow
              key={row.thread_id}
              row={row}
              active={selected?.subjectId === row.subject_id && selected?.subjectType === row.subject_type}
              onSelect={() => { setSelected({ subjectType: row.subject_type, subjectId: row.subject_id }); refreshInbox(); }}
            />
          ))}
        </div>

        <div className="min-h-[60vh] rounded-control border border-[#E1E3E9] bg-white p-4">
          {selected
            ? <ThreadPanel subjectType={selected.subjectType} subjectId={selected.subjectId} onChanged={refreshInbox} />
            : <p className="py-16 text-center text-[12.5px] text-slate-500">Pick a thread from the inbox to read and reply.</p>}
        </div>
      </div>
    </div>
  );
}
