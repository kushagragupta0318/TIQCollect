// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-21 — NEW. The manager's side of leave: pending requests to decide,
//   approved leave to revoke, and "Mark leave" for one agent (a manager's
//   own record, e.g. ABSENT for a no-show — approved in one step).
//
//   Opens as a FLOATING WINDOW from a "Leave" button at the top right of the
//   Agents page (it sat inline above the roster for an afternoon; the
//   manager wanted the roster first and leave on demand). The header bell
//   lists pending requests and deep-links here with ?leave=1. Polls with the
//   page's LIVE cadence so a request filed on a phone shows up within a
//   minute. On approve, the API says how many of tomorrow's cases went back
//   to the pool; the toast repeats it, because "approved" alone hides that
//   the night's plan will change.
// ─────────────────────────────────────────────────────────────────────────────

import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CalendarOff, Check, Loader2, X } from "lucide-react";
import { createPortal } from "react-dom";
import { toast } from "react-hot-toast";
import { decideLeave, markAgentLeave, type LeaveRequest, type LeaveType } from "@/api/manager";
import { errorDetail } from "@/lib/apiError";
import { useModalA11y } from "@/hooks/useModalA11y";
import { LEAVE_QUERY_KEY, useLeaveRequests } from "./leaveQueries";

const TYPE_WORDS: Record<string, string> = { SICK_LEAVE: "Sick", CASUAL_LEAVE: "Casual", EARNED_LEAVE: "Earned", ABSENT: "Absent" };
const fmt = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

export function LeavePanel() {
  const q = useLeaveRequests();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const rows = q.data?.requests ?? [];
  const pending = rows.filter((r) => r.status === "REQUESTED");
  const upcoming = rows.filter((r) => r.status === "APPROVED" && r.to_date >= new Date().toISOString().slice(0, 10));
  const history = rows.filter((r) => r.status === "REJECTED" || r.status === "CANCELLED" || (r.status === "APPROVED" && !upcoming.includes(r)));

  async function decide(r: LeaveRequest, decision: "approve" | "reject" | "revoke") {
    const note = decision === "approve" ? undefined : (window.prompt(decision === "reject" ? "Reason for rejecting (shown to the agent)" : "Reason for revoking") ?? undefined);
    if (decision !== "approve" && note === undefined) return;
    setBusy(r.id);
    try {
      const out = await decideLeave(r.id, decision, note);
      const released = out.cases_released_to_pool ?? 0;
      toast.success(
        decision === "approve"
          ? `Approved — ${r.agent_name} is off ${fmt(r.from_date)}${r.to_date !== r.from_date ? ` to ${fmt(r.to_date)}` : ""}${released ? ` · ${released} planned case${released > 1 ? "s" : ""} returned to the pool` : ""}`
          : decision === "reject" ? "Request rejected" : "Leave revoked",
      );
      await qc.invalidateQueries({ queryKey: LEAVE_QUERY_KEY });
      await qc.invalidateQueries({ queryKey: ["manager", "agents"] });
    } catch (err) {
      toast.error(errorDetail(err, "Could not update the request"));
    } finally { setBusy(null); }
  }

  const empty = !!q.data && pending.length === 0 && upcoming.length === 0 && history.length === 0;

  return (
    <div className="space-y-3">
      {history.length > 0 && (
        <div className="flex justify-end">
          <button type="button" className="text-xs font-semibold" style={{ color: "#6B6D76" }} onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? "Hide history" : `History (${history.length})`}
          </button>
        </div>
      )}
      {q.isError && <p className="text-sm text-center py-6" style={{ color: "#B45309" }}>Could not load leave requests.</p>}
      {!q.data && !q.isError && <div className="h-16 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />}
      {empty && (
        <div className="text-center py-8">
          <CalendarOff className="w-6 h-6 mx-auto mb-2 opacity-25" />
          <p className="text-xs" style={{ color: "#6B6D76" }}>No leave requests yet. Agents request leave from their profile; you can also record leave from an agent row.</p>
        </div>
      )}

      {pending.length > 0 && (
        <ul className="space-y-1.5">
          {pending.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2" style={{ background: "#FFFBEB", border: "1px solid #FDE68A" }}>
              <div className="min-w-0">
                <div className="text-xs font-semibold" style={{ color: "#1C1C1F" }}>
                  {r.agent_name} <span className="font-normal" style={{ color: "#6B6D76" }}>· {TYPE_WORDS[r.leave_type] ?? r.leave_type} · {fmt(r.from_date)}{r.to_date !== r.from_date && <> – {fmt(r.to_date)}</>} · {r.days} day{r.days > 1 ? "s" : ""}</span>
                </div>
                {r.reason && <div className="text-[11px] truncate" style={{ color: "#6B6D76" }}>{r.reason}</div>}
              </div>
              <div className="flex items-center gap-1.5">
                <button type="button" disabled={busy === r.id} onClick={() => decide(r, "approve")}
                        className="tap-target inline-flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-xl text-white" style={{ background: "#059669" }}>
                  {busy === r.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Approve
                </button>
                <button type="button" disabled={busy === r.id} onClick={() => decide(r, "reject")}
                        className="tap-target inline-flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-xl" style={{ background: "#fff", color: "#B91C1C", border: "1px solid #FECACA" }}>
                  <X className="w-3 h-3" /> Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {upcoming.length > 0 && (
        <div>
          <p className="text-[10.5px] font-semibold uppercase tracking-wide mb-1" style={{ color: "#8A8F9C" }}>Approved · current and upcoming</p>
          <ul className="space-y-1">
            {upcoming.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2 text-xs px-3 py-1.5 rounded-xl" style={{ border: "1px solid #EAEBEF" }}>
                <span style={{ color: "#1C1C1F" }}><strong>{r.agent_name}</strong> <span style={{ color: "#6B6D76" }}>· {TYPE_WORDS[r.leave_type] ?? r.leave_type} · {fmt(r.from_date)}{r.to_date !== r.from_date && <> – {fmt(r.to_date)}</>}</span></span>
                <button type="button" disabled={busy === r.id} onClick={() => decide(r, "revoke")} className="text-[11px] font-semibold" style={{ color: "#6B6D76" }}>Revoke</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {showHistory && history.length > 0 && (
        <ul className="space-y-1">
          {history.slice(0, 20).map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2 text-[11px] px-3 py-1" style={{ color: "#6B6D76" }}>
              <span><strong style={{ color: "#1C1C1F" }}>{r.agent_name}</strong> · {TYPE_WORDS[r.leave_type] ?? r.leave_type} · {fmt(r.from_date)}{r.to_date !== r.from_date && <> – {fmt(r.to_date)}</>}{r.decision_note ? ` · "${r.decision_note}"` : ""}</span>
              <span className="font-semibold">{r.status === "APPROVED" ? "Taken" : r.status === "REJECTED" ? "Rejected" : "Cancelled"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** "Mark leave" for one agent — a manager's own record, approved at once. */
export function MarkLeaveModal({ agentId, agentName, onClose }: { agentId: string; agentName: string; onClose: () => void }) {
  const qc = useQueryClient();
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(true, ref, onClose);
  const today = new Date().toISOString().slice(0, 10);
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [type, setType] = useState<LeaveType>("CASUAL_LEAVE");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  // Derived, not synced in an effect (react-hooks/set-state-in-effect): a
  // "to" earlier than "from" simply reads as "from".
  const toEff = to < from ? from : to;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const out = await markAgentLeave(agentId, { from_date: from, to_date: toEff, leave_type: type, reason: reason.trim() || undefined });
      const released = out.cases_released_to_pool ?? 0;
      toast.success(`Leave recorded for ${agentName}${released ? ` · ${released} planned case${released > 1 ? "s" : ""} returned to the pool` : ""}`);
      await qc.invalidateQueries({ queryKey: LEAVE_QUERY_KEY });
      await qc.invalidateQueries({ queryKey: ["manager", "agents"] });
      onClose();
    } catch (err) {
      toast.error(errorDetail(err, "Could not record leave"));
    } finally { setBusy(false); }
  }

  return createPortal(
    <div className="fixed inset-0 z-[10000] flex items-end sm:items-center justify-center p-0 sm:p-4" style={{ background: "rgba(15,23,42,0.45)" }} onClick={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="mark-leave-title" onClick={(e) => e.stopPropagation()}
           className="card w-full sm:max-w-md p-5 space-y-4 rounded-b-none sm:rounded-b-card">
        <div className="flex items-center justify-between">
          <h2 id="mark-leave-title" className="text-base font-bold" style={{ color: "#1C1C1F" }}>Mark leave — {agentName}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="tap-target" style={{ color: "#6B6D76" }}><X className="w-4 h-4" /></button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide" style={{ color: "#8A8F9C" }}>From
              <input id="mark-from" type="date" className="input text-[13px] py-2 px-2.5 tap-target-h" value={from} onChange={(e) => setFrom(e.target.value)} required />
            </label>
            <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide" style={{ color: "#8A8F9C" }}>To
              <input id="mark-to" type="date" className="input text-[13px] py-2 px-2.5 tap-target-h" min={from} value={toEff} onChange={(e) => setTo(e.target.value)} required />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide" style={{ color: "#8A8F9C" }}>Type
            <select id="mark-type" className="input text-[13px] py-2 px-2.5 tap-target-h" value={type} onChange={(e) => setType(e.target.value as LeaveType)}>
              <option value="CASUAL_LEAVE">Casual leave</option>
              <option value="SICK_LEAVE">Sick leave</option>
              <option value="EARNED_LEAVE">Earned leave</option>
              <option value="ABSENT">Absent (no-show — may be back-dated)</option>
            </select>
          </label>
          <textarea id="mark-reason" className="input text-[13px] py-2 px-2.5 w-full" rows={2} placeholder="Remarks (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <p className="text-[11px]" style={{ color: "#8A8F9C" }}>
            Approved immediately. A planned route on any of these days is cancelled and its cases return to the pool for tonight's plan; a day that already has visits is refused.
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="tap-target text-xs font-semibold px-3 py-2 rounded-xl" style={{ color: "#6B6D76" }}>Cancel</button>
            <button type="submit" disabled={busy} className="tap-target inline-flex items-center gap-2 text-xs font-semibold px-4 py-2 rounded-xl text-white" style={{ background: "#0C66E4" }}>
              {busy && <Loader2 className="w-3 h-3 animate-spin" />} Record leave
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}


/** The Leave panel as a floating window, opened from the Agents page's
 *  top-right "Leave" button or the header bell's deep link. */
export function LeaveWindow({ onClose, pending }: { onClose: () => void; pending: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(true, ref, onClose);
  return createPortal(
    <div className="fixed inset-0 z-[10000] flex items-end sm:items-start sm:justify-end p-0 sm:p-4 sm:pt-20" style={{ background: "rgba(15,23,42,0.35)" }} onClick={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="leave-window-title" onClick={(e) => e.stopPropagation()}
           className="card w-full sm:max-w-xl p-4 sm:p-5 space-y-3 rounded-b-none sm:rounded-b-card max-h-[85svh] overflow-y-auto">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CalendarOff className="w-4 h-4" style={{ color: "#6B6D76" }} aria-hidden="true" />
            <h2 id="leave-window-title" className="text-base font-bold" style={{ color: "#1C1C1F" }}>Leave</h2>
            {pending > 0 && (
              <span className="text-[11px] font-bold px-2 py-0.5 rounded-full" style={{ background: "#FEF3C7", color: "#92400E" }}>{pending} to decide</span>
            )}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="tap-target" style={{ color: "#6B6D76" }}><X className="w-4 h-4" /></button>
        </div>
        <LeavePanel />
      </div>
    </div>,
    document.body,
  );
}
