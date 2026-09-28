// ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
// 2026-09-28 — NEW (P2 G02). Suspend confirmation, wired to
//   POST /manager/agents/{agent_id}/suspend. Reason is required and capped at
//   500 characters client-side, matching agent_management_service.suspend_agent's
//   own validation (backend/app/services/agent_management_service.py) — the
//   client check only avoids a round trip for the obvious cases; the server
//   is still the one enforcing it.
// ────────────────────────────────────────────────────────────────────────────
import { useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, X } from "lucide-react";
import { toast } from "react-hot-toast";
import { Button } from "@/components/ui/Button";
import { useModalA11y } from "@/hooks/useModalA11y";
import { suspendAgent } from "@/api/manager";
import { errorDetail } from "@/lib/apiError";
import type { Agent } from "@/types";

const MAX_REASON_LENGTH = 500;

interface Props {
  agent: Agent | null;
  onClose: () => void;
  onSuspended: () => void;
}

export function SuspendAgentModal({ agent, onClose, onSuspended }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const open = agent !== null;
  useModalA11y(open, ref, onClose);

  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  if (!agent) return null;
  const agentId = agent.id;
  const agentName = agent.full_name;

  const trimmed = reason.trim();
  const tooLong = trimmed.length > MAX_REASON_LENGTH;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!trimmed || tooLong) return;
    setBusy(true);
    try {
      await suspendAgent(agentId, trimmed);
      toast.success(`${agentName} suspended`);
      setReason("");
      onSuspended();
      onClose();
    } catch (err) {
      toast.error(errorDetail(err, "Could not suspend agent"));
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="suspend-agent-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-card border border-border bg-white shadow-premium p-5 space-y-4"
      >
        <div className="flex items-center justify-between">
          <h2 id="suspend-agent-title" className="text-base font-bold flex items-center gap-2" style={{ color: "#1C1C1F" }}>
            <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: "#DC2626" }} />
            Suspend {agentName}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="tap-target" style={{ color: "#6B6D76" }}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <label className="block">
            <span className="mb-1.5 block text-[13px] font-normal text-[#98A2B3]">Reason (required)</span>
            <textarea
              className="input w-full text-[13px] py-2 px-2.5"
              rows={4}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this agent being suspended?"
              required
            />
          </label>
          <p className="text-xs" style={{ color: tooLong ? "#B42318" : "#8A8F9C" }}>
            {trimmed.length}/{MAX_REASON_LENGTH} characters
          </p>
          <p className="text-xs" style={{ color: "#6B6D76" }}>
            The agent's active sessions are ended immediately and they are signed out.
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="tap-target text-xs font-semibold px-3 py-2 rounded-xl" style={{ color: "#6B6D76" }}>
              Cancel
            </button>
            <Button type="submit" variant="danger" loading={busy} disabled={!trimmed || tooLong}>
              Suspend
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
