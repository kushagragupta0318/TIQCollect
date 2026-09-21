// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-21 — NEW. The agent's door onto the leave calendar. Until today the
//   only leave on the calendar was the seed's; no screen could file one.
//
//   Small form (dates, type, reason) + the agent's own requests with status
//   chips and Withdraw while still pending. Rules the API enforces are
//   mirrored here only as hints (same-day = sick only; reason required for
//   sick/earned) — the server's message is shown verbatim on refusal, so the
//   two never disagree in substance.
// ─────────────────────────────────────────────────────────────────────────────

import { useEffect, useState } from "react";
import { CalendarOff, Loader2 } from "lucide-react";
import { toast } from "react-hot-toast";
import { getMyLeaveRequests, requestLeave, withdrawLeave, type LeaveRequest, type LeaveType } from "@/api/agent";
import { errorDetail } from "@/lib/apiError";

const TYPES: { value: LeaveType; label: string; hint: string }[] = [
  { value: "SICK_LEAVE", label: "Sick leave", hint: "can start today · reason required" },
  { value: "CASUAL_LEAVE", label: "Casual leave", hint: "from tomorrow" },
  { value: "EARNED_LEAVE", label: "Earned leave", hint: "from tomorrow · reason required" },
];
const STATUS_STYLE: Record<string, { bg: string; fg: string; word: string }> = {
  REQUESTED: { bg: "#FEF3C7", fg: "#92400E", word: "Pending" },
  APPROVED: { bg: "#D1FAE5", fg: "#065F46", word: "Approved" },
  REJECTED: { bg: "#FEE2E2", fg: "#991B1B", word: "Rejected" },
  CANCELLED: { bg: "#EEF0F4", fg: "#6B6D76", word: "Withdrawn" },
};
const fmt = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
const todayIso = () => new Date().toISOString().slice(0, 10);

export function LeaveRequestsSection() {
  const [rows, setRows] = useState<LeaveRequest[] | null>(null);
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [type, setType] = useState<LeaveType>("CASUAL_LEAVE");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => getMyLeaveRequests().then((d) => setRows(d.requests)).catch(() => setRows([]));
  useEffect(() => { void load(); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!from || !to) { toast.error("Pick the dates"); return; }
    setBusy(true);
    try {
      await requestLeave({ from_date: from, to_date: to || from, leave_type: type, reason: reason.trim() || undefined });
      toast.success("Leave requested — your manager will see it");
      setOpen(false); setFrom(""); setTo(""); setReason("");
      await load();
    } catch (err) {
      toast.error(errorDetail(err, "Could not request leave"));
    } finally { setBusy(false); }
  }

  async function withdraw(id: string) {
    try { await withdrawLeave(id); toast.success("Request withdrawn"); await load(); }
    catch (err) { toast.error(errorDetail(err, "Could not withdraw")); }
  }

  const min = todayIso();
  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CalendarOff className="w-4 h-4" style={{ color: "#6B6D76" }} aria-hidden="true" />
          <h2 className="text-sm font-bold" style={{ color: "#1C1C1F" }}>Leave</h2>
        </div>
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
                className="tap-target text-xs font-semibold px-3 py-1.5 rounded-xl"
                style={{ background: "#EEF3FD", color: "#0C66E4", border: "1px solid #C7D9FA" }}>
          {open ? "Close" : "Request leave"}
        </button>
      </div>

      {open && (
        <form onSubmit={submit} className="space-y-3 rounded-xl p-3" style={{ background: "#F7F8FA", border: "1px solid #EAEBEF" }}>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide" style={{ color: "#8A8F9C" }}>
              From
              <input id="leave-from" type="date" min={min} className="input text-[13px] py-2 px-2.5 tap-target-h normal-case font-normal" value={from}
                     onChange={(e) => { setFrom(e.target.value); if (!to || e.target.value > to) setTo(e.target.value); }} required />
            </label>
            <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide" style={{ color: "#8A8F9C" }}>
              To
              <input id="leave-to" type="date" min={from || min} className="input text-[13px] py-2 px-2.5 tap-target-h normal-case font-normal" value={to}
                     onChange={(e) => setTo(e.target.value)} required />
            </label>
          </div>
          <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Leave type">
            {TYPES.map((t) => (
              <button key={t.value} type="button" role="radio" aria-checked={type === t.value} onClick={() => setType(t.value)}
                      className="tap-target rounded-xl px-2 py-2 text-left"
                      style={{ background: type === t.value ? "#fff" : "transparent", border: `1px solid ${type === t.value ? "#0C66E4" : "#E3E5EA"}` }}>
                <div className="text-xs font-semibold" style={{ color: "#1C1C1F" }}>{t.label}</div>
                <div className="text-[10px] leading-tight" style={{ color: "#8A8F9C" }}>{t.hint}</div>
              </button>
            ))}
          </div>
          <textarea id="leave-reason" className="input text-[13px] py-2 px-2.5 w-full" rows={2} placeholder="Reason (required for sick and earned leave)"
                    value={reason} onChange={(e) => setReason(e.target.value)} />
          <button type="submit" disabled={busy} className="tap-target w-full rounded-xl py-2.5 text-sm font-semibold text-white flex items-center justify-center gap-2"
                  style={{ background: "#0C66E4" }}>
            {busy && <Loader2 className="w-4 h-4 animate-spin" />} Send request
          </button>
        </form>
      )}

      {rows === null ? (
        <p className="text-xs" style={{ color: "#94a3b8" }}>Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs" style={{ color: "#94a3b8" }}>No leave requested yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.slice(0, 6).map((r) => {
            const st = STATUS_STYLE[r.status] ?? STATUS_STYLE.CANCELLED;
            return (
              <li key={r.id} className="flex items-center justify-between gap-2 rounded-xl px-3 py-2" style={{ border: "1px solid #EAEBEF" }}>
                <div className="min-w-0">
                  <div className="text-xs font-semibold tabular-nums" style={{ color: "#1C1C1F" }}>
                    {fmt(r.from_date)}{r.to_date !== r.from_date && <> – {fmt(r.to_date)}</>} <span className="font-normal" style={{ color: "#6B6D76" }}>· {r.days} day{r.days > 1 ? "s" : ""}</span>
                  </div>
                  <div className="text-[11px] truncate" style={{ color: "#6B6D76" }}>
                    {TYPES.find((t) => t.value === r.leave_type)?.label ?? r.leave_type}{r.reason ? ` · ${r.reason}` : ""}{r.decision_note && r.status !== "REQUESTED" ? ` · "${r.decision_note}"` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full" style={{ background: st.bg, color: st.fg }}>{st.word}</span>
                  {r.status === "REQUESTED" && (
                    <button type="button" onClick={() => withdraw(r.id)} className="text-[11px] font-semibold" style={{ color: "#6B6D76" }}>Withdraw</button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
