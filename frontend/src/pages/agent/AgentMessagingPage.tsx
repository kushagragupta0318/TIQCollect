// Messages: the agent's one thread with their agency manager (backend
// messaging.py / messaging_service.py's AGENT_DIRECT axis). An agent has
// exactly one such thread — there is no inbox list here, unlike the
// manager's agent-chats section, which lists one thread per agent. No
// status concept (no escalation_issues row behind an AGENT_DIRECT thread).
// Every message is part of the audit record (MESSAGE_SENT).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageSquare, ScrollText, Send } from "lucide-react";
import { getProfile } from "@/api/agent";
import { getAgentThread, postAgentMessage } from "@/api/messaging";
import type { Agent } from "@/types";
import { errorDetail } from "@/lib/apiError";
import { AGENT_MESSAGING_INBOX_KEY } from "@/lib/useMessagingInbox";
import {
  handleComposeKeyDown, useAutoScrollOnChange, useMessagingThread, type ThreadEndpoints,
} from "@/lib/useMessagingThread";
import { Button } from "@/components/ui/Button";

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function ThreadPanel({ agentId }: { agentId: string }) {
  const [body, setBody] = useState("");
  const endpoints: ThreadEndpoints = {
    getThread: () => getAgentThread(agentId),
    postMessage: (b) => postAgentMessage(agentId, b),
  };
  const t = useMessagingThread(["messaging", "agent-thread", agentId], "AGENT", endpoints, AGENT_MESSAGING_INBOX_KEY);
  const bottomRef = useAutoScrollOnChange(t.messages.length);

  if (t.isLoading) return <p className="py-10 text-center text-[13px] text-slate-500">Loading the conversation…</p>;
  if (t.isError) return <p className="py-10 text-center text-[13px] text-rose-600">{errorDetail(t.error, "This conversation could not load.")}</p>;

  const doSend = () => { if (!body.trim()) return; t.send(body.trim()); setBody(""); };

  return (
    <div className="flex h-full flex-col">
      <p className="flex items-center gap-1.5 border-b border-[#E1E3E9] pb-3 text-[11px] text-slate-500">
        <ScrollText className="size-3.5" aria-hidden="true" />
        Every message here is part of the audit record.
      </p>

      <div className="flex-1 space-y-3 overflow-y-auto py-4">
        {t.messages.map((m) => (
          <div key={m.id} className={`motion-safe:animate-card-enter max-w-[80%] rounded-control px-3.5 py-2.5 ${
            m.sender_side === "AGENT" ? "ml-auto bg-primary/10" : "bg-[#F7F8FA]"
          } ${m.pending ? "opacity-60" : ""}`}>
            <p className="text-[11px] font-semibold text-slate-500">{m.sender_side === "AGENT" ? "You" : "Your manager"}</p>
            <p className="mt-0.5 whitespace-pre-wrap text-[13px] text-slate-900">{m.body}</p>
            <p className="mt-1 text-[10.5px] text-slate-400">{m.pending ? "Sending…" : when(m.created_at)}</p>
          </div>
        ))}
        {t.messages.length === 0 && (
          <p className="py-8 text-center text-[12.5px] text-slate-500">No messages yet — say hello to your manager.</p>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-[#E1E3E9] pt-3">
        <textarea
          className="input text-[13px] w-full"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => handleComposeKeyDown(e, body, doSend)}
          placeholder="Reply… (Enter to send, Shift+Enter for a new line)"
          rows={3}
        />
        <div className="mt-2 flex justify-end">
          <Button onClick={doSend} disabled={!body.trim()} loading={t.sendPending}>
            {!t.sendPending && <Send className="size-4 shrink-0" />} Send
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function AgentMessagingPage() {
  const profile = useQuery({
    queryKey: ["agent", "profile", "messaging"],
    queryFn: () => getProfile() as Promise<Agent>,
  });

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900">
          <MessageSquare className="size-5" aria-hidden="true" /> Messages
        </h1>
        <p className="mt-0.5 text-[12.5px] text-slate-500">Your conversation with your agency manager.</p>
      </div>

      <div className="min-h-[60vh] rounded-control border border-[#E1E3E9] bg-white p-4">
        {profile.isLoading && <p className="py-10 text-center text-[13px] text-slate-500">Loading…</p>}
        {profile.isError && <p className="py-10 text-center text-[13px] text-rose-600">Could not load your profile.</p>}
        {profile.data && <ThreadPanel agentId={profile.data.id} />}
      </div>
    </div>
  );
}
