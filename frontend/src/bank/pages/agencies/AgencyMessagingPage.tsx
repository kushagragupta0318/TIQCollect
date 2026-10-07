// Agencies > Messaging: the bank's half of bank<->agency threads (backend
// messaging.py / messaging_service.py). The bank replies and can resolve or
// close an escalation; it never opens one (messaging.escalate is agency-only
// — services/messaging_service.py's _side() derives sender_side from scope,
// and only the owning agency's manager/admin may start a new ISSUE thread).
//
// Reads live: both the inbox and an open thread poll every 6s
// (useMessagingInbox/useMessagingThread), so the agency's reply or a new
// escalation reaching the inbox needs no manual refresh. Sending is
// optimistic — the bubble and the cleared input appear before the server
// round trip returns, reconciled against the real response.
import { useState } from "react";
import { useNavigate } from "react-router";
import { MessageSquare, ScrollText, Send } from "lucide-react";
import {
  getThread, postMessage, setEscalationStatus,
  type InboxThread, type ThreadStatus, type ThreadSubjectType,
} from "@/api/messaging";
import { errorDetail } from "@/lib/apiError";
import { MESSAGING_INBOX_KEY, useMessagingInbox } from "@/lib/useMessagingInbox";
import {
  handleComposeKeyDown, useAutoScrollOnChange, useMessagingThread, type ThreadEndpoints,
} from "@/lib/useMessagingThread";
import { AnalyticsError, AnalyticsLoading, Panel } from "../../components/analytics";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Select } from "../../ui/select";
import { Textarea } from "../../ui/textarea";

const STATUS_BADGE: Record<ThreadStatus, "default" | "success" | "outline"> = {
  OPEN: "default", RESOLVED: "success", CLOSED: "outline",
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
      className={`w-full rounded-inner border px-4 py-3 text-left transition-colors ${
        active ? "border-primary bg-accent/50" : "border-border/50 bg-card hover:bg-muted/40"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className={`truncate text-[13px] text-foreground ${row.unread ? "font-bold" : "font-semibold"}`}>{row.title}</p>
        {row.unread && <span className="size-2 shrink-0 rounded-full bg-primary" aria-label="Unread" />}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <Badge variant={STATUS_BADGE[row.status]}>{row.status}</Badge>
        <span className="text-[11px] text-muted-foreground">Agency</span>
        {row.pending && <Badge variant="warning">Awaiting your reply</Badge>}
      </div>
      <p className={`mt-1.5 truncate text-[12px] ${row.unread ? "font-medium text-foreground" : "text-muted-foreground"}`}>
        {row.last_message.sender_side === "BANK" ? "You: " : "Them: "}{row.last_message.preview}
      </p>
      <p className="mt-1 text-[10.5px] text-muted-foreground">{when(row.last_message.at)}</p>
    </button>
  );
}

function ThreadPanel({ subjectType, subjectId }: { subjectType: ThreadSubjectType; subjectId: string }) {
  const [body, setBody] = useState("");
  const endpoints: ThreadEndpoints = {
    getThread: () => getThread(subjectType, subjectId),
    postMessage: (b) => postMessage(subjectType, subjectId, b),
    setStatus: subjectType === "ISSUE"
      ? (status) => setEscalationStatus(subjectId, status).then((r) => ({
          thread: r.thread, subject_type: subjectType, subject_id: subjectId, messages: r.messages,
        }))
      : undefined,
  };
  const t = useMessagingThread(["messaging", "thread", subjectType, subjectId], "BANK", endpoints, MESSAGING_INBOX_KEY);
  const bottomRef = useAutoScrollOnChange(t.messages.length);

  if (t.isLoading) return <AnalyticsLoading label="Loading the conversation…" />;
  if (t.isError) return <AnalyticsError>{errorDetail(t.error, "This conversation could not load.")}</AnalyticsError>;

  const doSend = () => { if (!body.trim()) return; t.send(body.trim()); setBody(""); };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-border/50 pb-3">
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <ScrollText className="size-3.5" aria-hidden="true" />
          Every message here is part of the audit record (MESSAGE_SENT).
        </p>
        {subjectType === "ISSUE" && t.thread && (
          <Select
            value={t.thread.status}
            onChange={(e) => t.changeStatus(e.target.value as ThreadStatus)}
            disabled={t.statusPending}
            className="w-auto"
          >
            <option value="OPEN">Open</option>
            <option value="RESOLVED">Resolved</option>
            <option value="CLOSED">Closed</option>
          </Select>
        )}
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto py-4">
        {t.messages.map((m) => (
          <div key={m.id} className={`motion-safe:animate-card-enter max-w-[80%] rounded-inner px-3.5 py-2.5 ${
            m.sender_side === "BANK" ? "ml-auto bg-accent/60" : "bg-muted/50"
          } ${m.pending ? "opacity-60" : ""}`}>
            <p className="text-[11px] font-semibold text-muted-foreground">{m.sender_side === "BANK" ? "You" : "Agency"}</p>
            <p className="mt-0.5 whitespace-pre-wrap text-[13px] text-foreground">{m.body}</p>
            <p className="mt-1 text-[10.5px] text-muted-foreground">{m.pending ? "Sending…" : when(m.created_at)}</p>
          </div>
        ))}
        {t.messages.length === 0 && (
          <p className="py-8 text-center text-[12.5px] text-muted-foreground">No messages yet.</p>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-border/50 pt-3">
        <Textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => handleComposeKeyDown(e, body, doSend)}
          placeholder="Reply… (Enter to send, Shift+Enter for a new line)"
          rows={3}
        />
        <div className="mt-2 flex justify-end">
          <Button onClick={doSend} disabled={!body.trim()}>
            <Send className="size-4" /> {t.sendPending ? "Sending…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function AgencyMessagingPage() {
  const navigate = useNavigate();
  const [selected, setSelected] = useState<{ subjectType: ThreadSubjectType; subjectId: string } | null>(null);
  const q = useMessagingInbox();

  return (
    <PageRoot>
      <ToolHeader
        title="Messaging"
        icon={MessageSquare}
        description="Threads with your agencies — payment-reversal disputes and general escalations they've raised. Every message is audited."
      />
      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        <Panel title="Inbox" hint={q.data ? `${q.data.length}` : undefined} className="max-h-[70vh] overflow-y-auto">
          {q.isLoading && <AnalyticsLoading label="Loading threads…" />}
          {q.isError && <AnalyticsError>{errorDetail(q.error, "Threads could not load.")}</AnalyticsError>}
          {q.data && q.data.length === 0 && (
            <p className="py-8 text-center text-[12.5px] text-muted-foreground">No conversations yet.</p>
          )}
          <div className="space-y-2">
            {q.data?.map((row) => (
              <InboxRow
                key={row.thread_id}
                row={row}
                active={selected?.subjectId === row.subject_id && selected?.subjectType === row.subject_type}
                onSelect={() => setSelected({ subjectType: row.subject_type, subjectId: row.subject_id })}
              />
            ))}
          </div>
        </Panel>

        <Panel title={selected ? "Conversation" : "Select a thread"} className="min-h-[60vh]">
          {selected
            ? <ThreadPanel subjectType={selected.subjectType} subjectId={selected.subjectId} />
            : <p className="py-16 text-center text-[12.5px] text-muted-foreground">Pick a thread from the inbox to read and reply.</p>}
        </Panel>
      </div>
      <button
        type="button"
        onClick={() => navigate("/bank/admin/audit?action=MESSAGE_SENT")}
        className="text-[11.5px] font-medium text-primary hover:underline"
      >
        View every message-sent entry in the audit trail →
      </button>
    </PageRoot>
  );
}
