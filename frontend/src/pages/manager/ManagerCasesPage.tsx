// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-31 — Responsive pass. The 12-column case table needs ~900px and was
//   rendering into ~200px on a phone. Below lg each row is now a card carrying
//   all eight columns (nothing dropped), reusing the dual-render pattern that
//   already existed in ManagerAgentsPage. Filters collapse behind a toggle
//   below lg, with every applied filter still shown as a removable chip so
//   nothing is hidden. CaseDetailModal becomes a full-height bottom sheet on
//   mobile and gained a focus trap + body scroll lock. See docs/frontend-guide.md.
// 2026-08-05 — Merged tiq-demo. The desktop table gained a Location column
//   (customer city, previously a second line under the name) and dropped the
//   bank short name, which repeated identically on every row; the phone card
//   still shows both. Row tint/hover moved to .row-accent. The date filter
//   now seeds from GET /manager/cases/date-range instead of a hardcoded
//   six-month window, so the applied-filter chip compares against that span.
// ─────────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { casesView } from "./casesViewState";
import { rowChip, reassignProblem, PROBLEM_TEXT } from "./reassignValidation";
import { useSearchParams } from "react-router";
import { createPortal } from "react-dom";
import {
  Search, X, MapPin, Clock, CheckCircle2, AlertTriangle,
  Calendar, User, FileText, ChevronRight, SlidersHorizontal, ListOrdered,
} from "lucide-react";
import { toast } from "react-hot-toast";
import { getCases, getCaseDetail, getCasesDateRange, getAgents, reassignCase } from "@/api/manager";
import { errorDetail } from "@/lib/apiError";
import type { ManagerCaseDetail, VisitRecord, VisitPriority } from "@/api/manager";
import { Input } from "@/components/ui/Input";
import { DPDBadge, VisitPriorityBadge, CaseStatusBadge, RecoveryBadge } from "@/components/ui/Badge";
import { lakhWords } from "@/lib/money";

/**
 * The desktop table's column widths, declared ONCE and used by both the header
 * and every row.
 *
 * Was `grid-cols-12` with col-span-N on each cell. Twelve equal units could not
 * express what this table needs: every column added had to take a whole unit
 * from Agent, and agent names truncated to "Pankaj Kumar ...". Proportional
 * tracks give Agent the room a full name needs without starving anything else,
 * and one shared constant means the header can never drift out of step with the
 * rows it labels. (Removing the "Due now" column on 2026-08-27 returned its
 * share to Customer and Agent rather than widening everything by a tenth.)
 *
 * minmax(0, …) on every track is load-bearing: without it a long unbreakable
 * value (a case number, a rupee figure) sets a floor wider than its share and
 * pushes the whole row sideways.
 */
const TABLE_COLS = [
  "minmax(0,1.05fr)",  // Case — fits DAILY20260825C01, the longest form
  "minmax(0,1.8fr)",   // Customer
  "minmax(0,0.8fr)",   // Location — city names are short
  "minmax(0,0.9fr)",   // DPD
  "minmax(0,1.25fr)",  // Visit priority — fits the fully named badge
  "minmax(0,0.85fr)",  // Outlook
  "minmax(0,0.9fr)",   // Status
  "minmax(0,1.15fr)",  // Target / Collected
  "minmax(0,1.65fr)",  // Agent — the widest text column; fits a full name
  "minmax(0,0.85fr)",  // Date
].join(" ");

import { useModalA11y } from "@/hooks/useModalA11y";
import type { Case } from "@/types";

const EASE = "cubic-bezier(0.16,1,0.3,1)";
const PAGE_SIZE = 50;

// ── Outcome helpers ──────────────────────────────────────────────────────────

const OUTCOME_COLOR: Record<string, string> = {
  PAID_FULL:    "bg-success-100 text-success-700 border-success-200",
  PART_PAID:    "bg-success-50 text-success-600 border-success-100",
  PART_PAID_PTP:"bg-brand-50 text-brand-700 border-brand-100",
  PTP:          "bg-brand-100 text-brand-700 border-brand-200",
  BROKEN_PTP:   "bg-warning-100 text-warning-700 border-warning-200",
  RTP:          "bg-danger-100 text-danger-700 border-danger-200",
  DISPUTE:      "bg-danger-50 text-danger-600 border-danger-100",
  NOT_AVAILABLE:"bg-slate-100 text-slate-600 border-slate-200",
  ADDRESS_ISSUE:"bg-warning-50 text-warning-600 border-warning-100",
  DECEASED:     "bg-slate-200 text-slate-700 border-slate-300",
  REVISIT:      "bg-slate-100 text-slate-500 border-slate-200",
};

const OUTCOME_LABEL: Record<string, string> = {
  PAID_FULL: "Paid in Full", PART_PAID: "Partial Payment", PART_PAID_PTP: "Part Pay + PTP",
  PTP: "Promise to Pay", BROKEN_PTP: "Broken PTP", RTP: "Refused to Pay",
  DISPUTE: "Amount Disputed", NOT_AVAILABLE: "Not Available",
  ADDRESS_ISSUE: "Address Issue", DECEASED: "Deceased", REVISIT: "Revisit Required",
};

const PTP_STATUS_COLOR: Record<string, string> = {
  ACTIVE: "text-brand-600 bg-brand-50", HONORED: "text-success-600 bg-success-50",
  BROKEN: "text-danger-600 bg-danger-50", CANCELLED: "text-slate-400 bg-slate-50",
};

function fmt(d: string | null | undefined) {
  if (!d) return "—";
  return new Date(d).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
}

function fmtDate(d: string | null | undefined) {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

// ── Case detail modal (centered popup) ──────────────────────────────────────

// ── Why this case sits where it does in the visit queue (2026-08-27) ─────────
// The brief for this score asks for something "simple enough that a manager can
// explain to their team why one case sits above another". That is only true if
// the three terms are visible with their points — a single number is not
// explainable, it is just authoritative-looking.
//
// Detail page only. The case table has ten proportional tracks and the docblock
// on TABLE_COLS records that the last column added there truncated agent names.
//
// A compact band chip WAS tried in the table's Priority column on 2026-08-27
// and removed on 2026-08-28. Stacked under Case.priority it produced rows
// reading "High" over "MEDIUM" — two measures answering different questions,
// which a manager reads as a rendering fault, not as extra information. If the
// band belongs in the list at all it replaces that column rather than joining
// it; do not stack them again. Sort and the Priority filter still run on the
// band, so it steers the list without being drawn in it.
//
// The value term shows POINTS, never rupees. Same rule this page has followed
// since 2026-08-24: a rupee figure on a case row is read as "collect this", and
// rate_90 x total outstanding is not that.
// Deliberately NOT the RecoveryBadge palette. There HIGH is good news (more
// money back); here HIGH means "work this first". Sharing a palette across two
// opposite meanings is how a manager reads the wrong column.
const VP_BAND_STYLE: Record<string, React.CSSProperties> = {
  HIGH:   { background: "#0C66E4", color: "#fff", border: "1px solid #0C66E4" },
  MEDIUM: { background: "#EEF3FD", color: "#0C4DB3", border: "1px solid #C7D9FA" },
  LOW:    { background: "#F5F6F9", color: "#6B6D76", border: "1px solid #EAEBEF" },
};

const VP_LABEL: Record<string, string> = {
  RECOVERABLE_VALUE: "Recoverable value",
  URGENCY: "Urgency",
  EFFORT: "Effort already spent",
};

function VisitPriorityPanel({ vp }: { vp: VisitPriority }) {
  return (
    <div className="mt-4 rounded-xl p-3"
         style={{ background: "rgba(22,119,255,0.05)", border: "1px solid rgba(22,119,255,0.15)" }}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 flex-wrap">
          <ListOrdered className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "#0C4DB3" }} />
          {/* "Why this case is visited FIRST" was wrong on almost every case it
              appeared on — only a handful rank first, and the panel showed that
              heading above a score of 11/100. The heading now states what the
              panel IS; the band and the score say where the case actually sits. */}
          <p className="text-xs font-bold" style={{ color: "#0C4DB3" }}>Visit priority</p>
          {/* States what it IS. There is no model here, and a chip that says so
              is cheaper than a reader assuming otherwise. */}
          <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
                style={{ background: "#fff", color: "#0C66E4", border: "1px solid #C7D9FA" }}>
            {vp.is_modelled ? "Model" : "Scorecard"}
          </span>
        </div>
        <span className="flex items-baseline gap-1.5">
          <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
                style={VP_BAND_STYLE[vp.band] ?? VP_BAND_STYLE.LOW}>
            {vp.band}
          </span>
          <span className="text-[15px] font-bold" style={{ color: "#0C4DB3" }}>
            {Math.round(vp.score)}<span className="text-[11px] font-semibold"> / 100</span>
          </span>
        </span>
      </div>

      <p className="text-[11.5px] mt-1.5" style={{ color: "#1677FF" }}>{vp.reason}</p>

      <div className="mt-2.5 space-y-1.5">
        {vp.components.map((comp) => (
          <div key={comp.code} className="flex items-start gap-2">
            {/* Signed and monospaced so the three lines read as arithmetic that
                sums to the number above, which is the point of showing them. */}
            <span className="text-[12px] font-mono font-bold flex-shrink-0 text-right"
                  style={{ width: 34, color: comp.points < 0 ? "#B45309" : "#0F9960" }}>
              {comp.points > 0 ? "+" : ""}{comp.points}
            </span>
            <div className="min-w-0">
              <p className="text-[11.5px] font-semibold" style={{ color: "#1C1C1F" }}>
                {VP_LABEL[comp.code] ?? comp.code}
                {comp.abstained && (
                  <span className="font-normal ml-1.5" style={{ color: "#94a3b8" }}>· not measured</span>
                )}
              </p>
              <p className="text-[11px]" style={{ color: "#6B6D76" }}>{comp.summary}</p>
            </div>
          </div>
        ))}
      </div>

      <p className="text-[10.5px] mt-2.5 leading-snug" style={{ color: "#94a3b8" }}>
        {vp.model_version}
        {vp.rate_as_of && <> · recovery estimate as of {vp.rate_as_of}</>}
        {" · decides visit order only"}
      </p>
    </div>
  );
}

/**
 * Move a case to another agent, with a reason. The only way an owned case
 * changes hands since 2026-09-11: the nightly plan keeps a case with its agent,
 * exploration cannot touch it, so if a manager wants it elsewhere they say so
 * here and the sentence goes on the audit trail.
 *
 * Validation is reassignProblem() — a pure function, tested on its own — and
 * the server enforces the same rules again plus the hard gates for the
 * incoming agent (female-agent requirement, territory, PTP fatigue, DNC), so
 * a refusal from there is shown verbatim rather than paraphrased.
 */
function ReassignDialog({ detail, onClose, onMoved }: {
  detail: ManagerCaseDetail;
  onClose: () => void;
  onMoved: (next: { agent_id: string; agent_name: string | null; reason: string; by_when: string }) => void;
}) {
  const [newAgentId, setNewAgentId] = useState<string>("");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  useModalA11y(true, panelRef, onClose);

  const agentsQ = useQuery({ queryKey: ["manager", "agents"], queryFn: getAgents, staleTime: 60_000 });
  const problem = reassignProblem({ currentAgentId: detail.agent_id, newAgentId: newAgentId || null, reason });

  const move = useMutation({
    mutationFn: () => reassignCase(detail.id, { new_agent_id: newAgentId, reason: reason.trim() }),
    onSuccess: (res) => {
      toast.success(`Moved to ${res.to_agent_name ?? "the new agent"} — takes effect at tonight's plan`);
      onMoved({ agent_id: res.to_agent_id, agent_name: res.to_agent_name, reason: res.reason, by_when: res.reassigned_at });
      onClose();
    },
    // 409s carry a sentence from the same gate the nightly run applies
    // ("The borrower is outside this agent's territory."); show it as-is.
    onError: (err) => toast.error(errorDetail(err, "Could not reassign this case")),
  });

  const agents = (agentsQ.data ?? []).filter((a) => a.id !== detail.agent_id);

  return createPortal(
    <div role="dialog" aria-modal="true" aria-label="Reassign case"
         className="fixed inset-0 z-[10000] flex items-center justify-center p-3 sm:p-4"
         style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)" }}
         onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={panelRef} className="w-full max-w-md flex flex-col rounded-2xl overflow-hidden"
           style={{ background: "#FFFFFF", boxShadow: "0 24px 64px rgba(0,0,0,0.25)" }}>
        <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3" style={{ borderBottom: "1px solid #EAEBEF" }}>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "#6B6D76" }}>Reassign case</p>
            <p className="text-base font-bold font-mono truncate mt-0.5" style={{ color: "#1C1C1F" }}>{detail.case_number}</p>
            <p className="text-xs mt-1" style={{ color: "#6B6D76" }}>
              Currently with <span className="font-semibold" style={{ color: "#1C1C1F" }}>{detail.agent_name ?? "nobody"}</span>.
              The move takes effect at tonight's plan; today's beat is unchanged.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close reassign dialog"
                  className="tap-target flex items-center justify-center flex-shrink-0 hover:bg-black/10"
                  style={{ width: 32, height: 32, borderRadius: 10, background: "rgba(0,0,0,0.06)", border: "none" }}>
            <X className="w-4 h-4" style={{ color: "#6B6D76" }} />
          </button>
        </div>

        {/* noValidate: the browser's own "required" bubble would fire before
            reassignProblem() and hide the agent-first message. One validator,
            ours, so the sentence a manager sees is the one we wrote and tested. */}
        <form className="px-5 py-4 space-y-4" noValidate
              onSubmit={(e) => { e.preventDefault(); setTouched(true); if (!problem) move.mutate(); }}>
          <label className="block">
            <span className="text-xs font-medium" style={{ color: "#6B6D76" }}>New agent</span>
            <select id="reassign-agent" value={newAgentId} onChange={(e) => setNewAgentId(e.target.value)}
                    className="mt-1 w-full rounded-lg border px-3 py-2 text-sm"
                    style={{ borderColor: "#D9DFE4", color: "#1C1C1F", background: "#fff" }}>
              <option value="">{agentsQ.isPending ? "Loading agents…" : "Choose an agent"}</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.full_name} · {a.employee_code} · {a.territory}{a.status !== "ON_DUTY" ? ` (${String(a.status).toLowerCase().replace("_", " ")})` : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-medium" style={{ color: "#6B6D76" }}>
              Reason <span style={{ color: "#A93B2C" }}>*</span>
            </span>
            <textarea id="reassign-reason" value={reason} rows={3} aria-required="true"
                      onChange={(e) => setReason(e.target.value)} onBlur={() => setTouched(true)}
                      placeholder="Why this case is moving — it is recorded on the case's audit trail"
                      className="mt-1 w-full rounded-lg border px-3 py-2 text-sm"
                      style={{ borderColor: touched && problem?.startsWith("reason") ? "#A93B2C" : "#D9DFE4", color: "#1C1C1F" }} />
          </label>

          {touched && problem && (
            <p role="alert" className="text-xs" style={{ color: "#A93B2C" }}>{PROBLEM_TEXT[problem]}</p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="px-3 py-2 text-sm rounded-lg"
                    style={{ color: "#6B6D76", background: "transparent", border: "1px solid #D9DFE4" }}>
              Cancel
            </button>
            {/* Enabled whenever nothing is in flight. Disabling on a validation
                problem looked tidy and was wrong: the focus trap blurs the
                reason field on open, which marked the form touched and locked
                the button before the manager had typed a character — with no
                message, because the message only appears on submit. Found in
                browser QA. A click now always produces either a request or a
                sentence saying why not. */}
            <button type="submit" disabled={move.isPending}
                    className="px-4 py-2 text-sm font-semibold rounded-lg disabled:opacity-50"
                    style={{ color: "#fff", background: "#0F5C5A", border: "none" }}>
              {move.isPending ? "Moving…" : "Reassign"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}

function CaseDetailModal({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ManagerCaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"visits" | "payments" | "ptps" | "photos">("visits");
  const [reassigning, setReassigning] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    getCaseDetail(caseId)
      .then(setDetail)
      .catch(() => toast.error("Failed to load case detail"))
      .finally(() => setLoading(false));
  }, [caseId]);

  // Body scroll lock, focus trap, focus restore, and Escape-to-close.
  useModalA11y(true, panelRef, onClose);

  const photos = detail?.photos ?? [];

  const onMoved = (next: { agent_id: string; agent_name: string | null; reason: string; by_when: string }) => {
    setDetail((d) => d && {
      ...d, agent_id: next.agent_id, agent_name: next.agent_name,
      last_reassignment: {
        reason: next.reason, from_agent_id: d.agent_id, from_agent_name: d.agent_name,
        to_agent_id: next.agent_id, to_agent_name: next.agent_name, by: null, at: next.by_when,
      },
    });
    // The list behind this modal carries agent_name and last_reassignment per
    // row; the prefix key invalidates every page/filter variant at once.
    queryClient.invalidateQueries({ queryKey: ["manager", "cases"] });
  };

  return createPortal(
    <>
    {reassigning && detail && (
      <ReassignDialog detail={detail} onClose={() => setReassigning(false)} onMoved={onMoved} />
    )}
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Case detail"
      // Centred dialog at every size. A bottom sheet pinned the content to the
      // bottom edge and pushed the detail out of comfortable reach; a centred
      // card that scrolls internally reads the same on phone and laptop.
      className="fixed inset-0 z-[9999] flex items-center justify-center p-3 sm:p-4"
      style={{
        background: "rgba(0,0,0,0.45)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Modal */}
      <div
        ref={panelRef}
        tabIndex={-1}
        // max-h leaves backdrop visible above and below, so it reads as a
        // floating dialog. The body below scrolls inside it.
        className="w-full sm:max-w-2xl bg-white flex flex-col overflow-hidden rounded-card max-h-[88svh] sm:max-h-[90svh] outline-none border border-slate-200"
        style={{
          boxShadow: "0 16px 40px rgba(15,23,42,0.12)",
          animation: `modalIn 240ms ${EASE} both`,
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 sm:py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid #EAEBEF", background: "#F5F6F9" }}
        >
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "#6B6D76" }}>Case Detail</p>
            {loading ? (
              <div className="h-5 w-32 bg-slate-200 rounded animate-pulse mt-1" />
            ) : (
              <p className="text-base font-bold font-mono truncate" style={{ color: "#1C1C1F" }}>{detail?.case_number}</p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close case detail"
            className="tap-target flex items-center justify-center transition-colors flex-shrink-0 hover:bg-black/10"
            style={{ width: 32, height: 32, borderRadius: 10, background: "rgba(0,0,0,0.06)", border: "none" }}
          >
            <X className="w-4 h-4" style={{ color: "#6B6D76" }} />
          </button>
        </div>

        {loading ? (
          <div className="flex-1 p-5 space-y-3 overflow-y-auto">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-12 rounded-xl animate-pulse" style={{ background: "#EFF0F4" }} />
            ))}
          </div>
        ) : detail ? (
          <div className="flex-1 overflow-y-auto">
            {/* Case summary */}
            <div className="px-4 sm:px-5 py-4" style={{ borderBottom: "1px solid #EAEBEF" }}>
              <div className="grid grid-cols-2 gap-3">
                <InfoRow label="Customer" value={detail.customer.full_name} />
                <InfoRow label="Agent">
                  <div className="flex items-center gap-2 mt-0.5">
                    <p className="text-sm font-semibold truncate" style={{ color: "#1C1C1F" }}>{detail.agent_name ?? "Unassigned"}</p>
                    <button type="button" onClick={() => setReassigning(true)}
                            className="text-xs font-semibold px-2 py-0.5 rounded-md flex-shrink-0 hover:bg-black/5"
                            style={{ color: "#0F5C5A", border: "1px solid #CFE3E1", background: "transparent" }}>
                      Reassign
                    </button>
                  </div>
                  {detail.last_reassignment?.reason && (
                    <p className="text-xs mt-1" style={{ color: "#6B6D76" }}
                       title={detail.last_reassignment.at ?? undefined}>
                      Moved from {detail.last_reassignment.from_agent_name ?? "unassigned"} by {detail.last_reassignment.by ?? "a manager"}: “{detail.last_reassignment.reason}”
                    </p>
                  )}
                </InfoRow>
                <InfoRow label="City" value={`${detail.customer.city}, ${detail.customer.state}`} />
                <InfoRow label="Collection Stage" value={detail.collection_stage ?? "—"} />
                <InfoRow label="DPD" value={`${detail.loan.dpd} days (${detail.loan.dpd_bucket.replace("_", " ")})`} />
                {/* NO ARREARS ROW, AND NO OTHER PRE-SUMMED RUPEE FIGURE.
                    A "Due now" row (overdue + penal) was added here on
                    2026-08-24 and renamed "Arrears" on 2026-08-27. Both were
                    arithmetically honest and both misled for the same reason:
                    the modal already shows Target, Collected and Loan balance,
                    and a fourth large rupee number in the grid above them left
                    a manager unable to tell which figure the case is judged on.
                    Renaming it did not fix that — only removing it did.
                    Removed 2026-08-28, restoring the grid to Customer / Agent /
                    City / Collection Stage / DPD / Status that predated the
                    2026-08-24 recovery surface.

                    DPD says how late the borrower is, which is the fact this
                    row was really carrying. overdue_amount and penal_charges
                    stay on the payload for any caller that needs the rupees. */}
                <InfoRow label="Recovery outlook">
                  <RecoveryBadge potential={detail.recovery?.recovery_potential} />
                </InfoRow>
                <InfoRow label="Status">
                  <CaseStatusBadge status={detail.status as never} />
                </InfoRow>
              </div>

              {/* Visiting restrictions the bank set on this customer. Allocation
                  already refuses to assign past these, but a manager can move a
                  case by hand — so the constraint has to be visible here too. */}
              {(detail.customer.requires_female_agent || detail.customer.do_not_contact) && (
                <div className="flex flex-wrap gap-2 mt-3">
                  {detail.customer.do_not_contact && (
                    <span className="text-[11.5px] font-semibold px-2.5 py-1 rounded-lg"
                          style={{ background: "rgba(220,38,38,0.10)", color: "#991B1B",
                                   border: "1px solid rgba(220,38,38,0.25)" }}>
                      Do not contact
                    </span>
                  )}
                  {detail.customer.requires_female_agent && (
                    <span className="text-[11.5px] font-semibold px-2.5 py-1 rounded-lg"
                          style={{ background: "rgba(180,83,9,0.10)", color: "#7C3E00",
                                   border: "1px solid rgba(180,83,9,0.25)" }}>
                      Female agent required
                    </span>
                  )}
                </div>
              )}

              {/* Financial summary */}
              <div className="mt-4 grid grid-cols-3 gap-2 sm:gap-3">
                <AmountCard label="Target" amount={detail.target_amount} color="text-slate-900" />
                <AmountCard label="Collected" amount={detail.collected_amount} color="text-success-600" />
                {/* "Outstanding" beside Target/Collected invited the reading
                    "what's left of the target". It is the whole loan balance. */}
                <AmountCard label="Loan balance" amount={detail.loan.total_outstanding} color="text-danger-600" />
              </div>

              {/* Visit priority. Present on open cases; absent on resolved ones
                  because visit_priority_service withholds a score once a case is
                  settled — there is no next visit to rank. That was rendering as
                  a silent gap, which reads as a missing feature rather than a
                  finished case, so the reason is now stated. Added 2026-09-03. */}
              {detail.visit_priority ? (
                <VisitPriorityPanel vp={detail.visit_priority} />
              ) : (
                <div className="mt-3 rounded-xl p-3" style={{ background: "rgba(100,116,139,0.06)", border: "1px solid rgba(100,116,139,0.15)" }}>
                  <p className="text-xs font-bold" style={{ color: "#475569" }}>Visit priority</p>
                  <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>
                    Not ranked — this case is closed, so there is no next visit to prioritise.
                  </p>
                </div>
              )}

              {detail.bank_agent_remarks && (
                <div className="mt-3 rounded-xl p-3" style={{ background: "rgba(22,119,255,0.06)", border: "1px solid rgba(22,119,255,0.15)" }}>
                  <p className="text-xs font-semibold mb-0.5" style={{ color: "#0C4DB3" }}>Bank Remarks</p>
                  <p className="text-xs" style={{ color: "#1677FF" }}>{detail.bank_agent_remarks}</p>
                </div>
              )}

              {detail.is_escalated && (
                <div className="mt-2 rounded-xl px-3 py-2 flex items-center gap-2" style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.20)" }}>
                  <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: "#DC2626" }} />
                  <p className="text-xs font-semibold" style={{ color: "#991B1B" }}>Case Escalated</p>
                </div>
              )}
            </div>

            {/* Tabs — sticky so they stay reachable while a long visit list scrolls */}
            <div
              className="flex overflow-x-auto scrollbar-hide sticky top-0 z-10"
              style={{ borderBottom: "1px solid #EAEBEF", background: "#fff" }}
            >
              {([
                { key: "visits",   label: `Visits (${detail.visits.length})` },
                { key: "payments", label: `Payments (${detail.payments.length})` },
                { key: "ptps",     label: `PTPs (${detail.ptps.length})` },
                ...(photos.length > 0 ? [{ key: "photos", label: `Photos (${photos.length})` }] : []),
              ] as const).map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key as typeof tab)}
                  aria-current={tab === t.key}
                  className="tap-target-h flex-shrink-0 py-3 px-4 text-xs font-semibold transition-colors whitespace-nowrap"
                  style={{
                    borderBottom: tab === t.key ? "2px solid #1677FF" : "2px solid transparent",
                    color: tab === t.key ? "#1677FF" : "#6B6D76",
                    marginBottom: -1,
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div className="px-4 sm:px-5 py-4 space-y-3">
              {tab === "visits" && (
                detail.visits.length === 0 ? (
                  <EmptyState text="No visits recorded yet" />
                ) : (
                  detail.visits.map((v) => <VisitCard key={v.id} visit={v} />)
                )
              )}

              {tab === "payments" && (
                detail.payments.length === 0 ? (
                  <EmptyState text="No payments collected" />
                ) : (
                  <div className="space-y-2">
                    {/* Column headers only make sense once the row is a grid */}
                    <div className="hidden sm:grid grid-cols-12 text-xs font-semibold uppercase px-2 pb-1" style={{ color: "#6B6D76" }}>
                      <span className="col-span-4">Receipt</span>
                      <span className="col-span-3">Mode</span>
                      <span className="col-span-3 text-right">Amount</span>
                      <span className="col-span-2 text-right">Date</span>
                    </div>
                    {detail.payments.map((p) => (
                      <div
                        key={p.id}
                        // Below sm this is a stacked block: amount and mode on
                        // one line, receipt and date beneath. A 4-column split
                        // of ~260px gives each field ~60px, which clips the
                        // receipt number and the amount alike.
                        className="sm:grid sm:grid-cols-12 text-sm sm:items-center rounded-xl px-3 py-2.5"
                        style={{ background: "#F5F6F9" }}
                      >
                        {/* col-start pins each field to its header column, so the
                            mobile-first DOM order (amount first) does not shuffle
                            the desktop grid. */}
                        <div className="flex items-baseline justify-between gap-2 sm:contents">
                          <span className="sm:col-start-8 sm:col-span-3 sm:text-right font-bold text-success-600">
                            ₹{p.amount.toLocaleString("en-IN")}
                          </span>
                          <span className="sm:col-start-5 sm:col-span-3 sm:row-start-1 text-xs flex items-center gap-1.5" style={{ color: "#6B6D76" }}>
                            {p.mode.replace("_", " ")}
                            {/* A payment with no visit behind it. Marked because
                                the tab counts otherwise look like a missing
                                visit — the money came in by transfer against a
                                promise, days after anyone called. */}
                            {!p.visit_id && (
                              <span
                                className="text-[10px] font-semibold px-1.5 py-0.5 rounded cursor-help whitespace-nowrap"
                                style={{ background: "#EEF3FD", color: "#0C4DB3", border: "1px solid #C7D9FA" }}
                                title={"Paid remotely, not at a visit — the borrower settled a promise by transfer. This is why the payment count can exceed the visit count."}
                              >
                                Remote
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="flex items-baseline justify-between gap-2 mt-1 sm:mt-0 sm:contents">
                          <span className="sm:col-start-1 sm:col-span-4 sm:row-start-1 font-mono text-xs truncate" style={{ color: "#6B6D76" }}>{p.receipt_number}</span>
                          <span className="sm:col-start-11 sm:col-span-2 sm:row-start-1 sm:text-right text-xs whitespace-nowrap" style={{ color: "#6B6D76" }}>{fmtDate(p.payment_date)}</span>
                        </div>
                      </div>
                    ))}
                    {detail.payments.some((p) => !p.visit_id) && (
                      <p className="text-[11.5px] px-2 pt-1" style={{ color: "#6B6D76" }}>
                        {detail.payments.filter((p) => !p.visit_id).length} of{" "}
                        {detail.payments.length} payments arrived remotely against a
                        promise, so the payment count here does not match the visit count.
                      </p>
                    )}
                    <div className="flex justify-end pt-2" style={{ borderTop: "1px solid #EAEBEF" }}>
                      <div className="text-right">
                        <p className="text-xs" style={{ color: "#6B6D76" }}>Total Collected</p>
                        <p className="text-lg font-bold text-success-600">
                          ₹{detail.payments.reduce((s, p) => s + p.amount, 0).toLocaleString("en-IN")}
                        </p>
                      </div>
                    </div>
                  </div>
                )
              )}

              {tab === "ptps" && (
                detail.ptps.length === 0 ? (
                  <EmptyState text="No PTPs set for this case" />
                ) : (
                  detail.ptps.map((ptp) => (
                    <div key={ptp.id} className="rounded-xl p-3.5" style={{ background: "#F5F6F9", border: "1px solid #EAEBEF" }}>
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-sm font-bold" style={{ color: "#1C1C1F" }}>₹{ptp.committed_amount.toLocaleString("en-IN")}</p>
                          <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>Committed for {fmtDate(ptp.committed_date)}</p>
                        </div>
                        <span className={`text-xs font-semibold px-2 py-1 rounded-full ${PTP_STATUS_COLOR[ptp.status] ?? "text-slate-600 bg-slate-100"}`}>
                          {ptp.status}
                        </span>
                      </div>
                      {ptp.customer_reason && <p className="text-xs mt-2 italic" style={{ color: "#6B6D76" }}>"{ptp.customer_reason}"</p>}
                      {ptp.agent_notes && <p className="text-xs mt-1" style={{ color: "#6B6D76" }}>Agent note: {ptp.agent_notes}</p>}
                      {ptp.follow_up_date && (
                        <p className="text-xs mt-1.5 flex items-center gap-1 text-brand-600">
                          <Calendar className="w-3 h-3" /> Follow-up: {fmtDate(ptp.follow_up_date)}
                        </p>
                      )}
                    </div>
                  ))
                )
              )}

              {tab === "photos" && (
                photos.length === 0 ? (
                  <EmptyState text="No photos captured for this case" />
                ) : (
                  <div className="grid grid-cols-2 gap-3">
                    {photos.map((ph) => {
                      const typeLabel: Record<string, string> = {
                        AGENT_SELFIE: "Agent Selfie", BORROWER: "Borrower Photo", VEHICLE_ASSET: "Vehicle / Asset",
                      };
                      return (
                        <div key={ph.storage_key} className="rounded-xl overflow-hidden" style={{ border: "1px solid #EAEBEF", background: "#fff" }}>
                          {ph.view_url ? (
                            <a href={ph.view_url} target="_blank" rel="noopener noreferrer">
                              <img src={ph.view_url} alt={typeLabel[ph.photo_type] ?? ph.photo_type} className="w-full aspect-square object-cover hover:opacity-90 transition-opacity" />
                            </a>
                          ) : (
                            <div className="w-full aspect-square flex items-center justify-center text-3xl opacity-30" style={{ background: "#F5F6F9" }}>📷</div>
                          )}
                          <div className="p-2">
                            <p className="text-xs font-semibold" style={{ color: "#1C1C1F" }}>{typeLabel[ph.photo_type] ?? ph.photo_type}</p>
                            {ph.captured_at && <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>{fmtDate(ph.captured_at)}</p>}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center" style={{ color: "#6B6D76" }}>Case not found</div>
        )}
      </div>

      <style>{`
        /* Same scale-in at every size — it is a centred dialog on phone and
           laptop alike, so it should not animate like a sheet on one of them. */
        @keyframes modalIn {
          from { opacity: 0; transform: scale(0.94) translateY(12px); }
          to   { opacity: 1; transform: scale(1) translateY(0); }
        }
      `}</style>
    </div>
    </>,
    document.body
  );
}

function VisitCard({ visit }: { visit: VisitRecord }) {
  const [expanded, setExpanded] = useState(false);
  const badgeClass = OUTCOME_COLOR[visit.outcome] ?? "bg-slate-100 text-slate-600 border-slate-200";
  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        border: `1px solid ${visit.outcome === "PAID_FULL" || visit.outcome === "PART_PAID" ? "#bbf7d0" : visit.outcome === "RTP" || visit.outcome === "DISPUTE" ? "#fecaca" : "#EAEBEF"}`,
      }}
    >
      <div
        className="flex items-start gap-3 p-3.5 cursor-pointer transition-colors"
        style={{ background: "transparent" }}
        onClick={() => setExpanded(!expanded)}
        onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "#F5F6F9"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
      >
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5"
          style={{ background: "#EFF0F4", color: "#6B6D76" }}
        >
          {visit.visit_number}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${badgeClass}`}>
              {OUTCOME_LABEL[visit.outcome] ?? visit.outcome}
            </span>
            {visit.customer_met && (
              <span className="text-xs text-success-600 flex items-center gap-0.5">
                <CheckCircle2 className="w-3 h-3" /> Met
              </span>
            )}
            {!visit.geo_verified && (
              <span className="text-xs text-warning-600 flex items-center gap-0.5">
                <MapPin className="w-3 h-3" /> Out of fence
              </span>
            )}
            {!visit.within_contact_hours && (
              <span className="text-xs text-warning-600 flex items-center gap-0.5">
                <Clock className="w-3 h-3" /> Off-hours
              </span>
            )}
          </div>

          <div className="flex items-center gap-3 mt-1.5 text-xs" style={{ color: "#6B6D76" }}>
            <span>{fmt(visit.check_in_time)}</span>
            <span>{visit.distance_from_customer_metres.toFixed(0)}m away</span>
            {visit.agent_name && (
              <span className="flex items-center gap-0.5" style={{ color: "#6B6D76" }}>
                <User className="w-3 h-3" /> {visit.agent_name}
              </span>
            )}
          </div>

          {visit.notes && (
            <p className="text-xs mt-1.5 line-clamp-2" style={{ color: "#6B6D76" }}>{visit.notes}</p>
          )}
        </div>

        <ChevronRight
          className={`w-4 h-4 flex-shrink-0 mt-1 transition-transform ${expanded ? "rotate-90" : ""}`}
          style={{ color: "#C4C6CF" }}
        />
      </div>

      {expanded && (
        <div className="px-4 py-3 space-y-3 text-xs" style={{ borderTop: "1px solid #EAEBEF", background: "#F5F6F9" }}>
          <div className="grid grid-cols-2 gap-2">
            {visit.person_met && <DetailItem label="Person met" value={visit.person_met.replace("_", " ")} />}
            {visit.default_reason && <DetailItem label="Default reason" value={visit.default_reason.replace(/_/g, " ")} />}
            {visit.not_met_reason && <DetailItem label="Not met reason" value={visit.not_met_reason.replace(/_/g, " ")} />}
            {visit.property_type && <DetailItem label="Property type" value={visit.property_type.replace("_", " ")} />}
            {visit.occupancy_status && <DetailItem label="Occupancy" value={visit.occupancy_status.replace("_", " ")} />}
            {visit.vehicle_present != null && <DetailItem label="Vehicle present" value={visit.vehicle_present ? "Yes" : "No"} />}
            {visit.business_running != null && <DetailItem label="Business running" value={visit.business_running ? "Yes" : "No"} />}
            {visit.check_out_time && <DetailItem label="Check-out" value={fmt(visit.check_out_time)} />}
            {visit.consent_given != null && <DetailItem label="Consent given" value={visit.consent_given ? "Yes" : "No"} />}
          </div>
          {visit.ai_visit_note && (
            <div className="rounded-xl p-3" style={{ background: "#fff", border: "1px solid #EAEBEF" }}>
              <p className="text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "#6B6D76" }}>AI Audit Report</p>
              <p className="text-xs leading-relaxed" style={{ color: "#1C1C1F" }}>{visit.ai_visit_note}</p>
            </div>
          )}
          {visit.agent_recording_transcript && (
            <div className="rounded-xl p-3" style={{ background: "rgba(22,119,255,0.05)", border: "1px solid rgba(22,119,255,0.15)" }}>
              <p className="text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "#1677FF" }}>Agent Speech Transcript</p>
              <p className="text-xs leading-relaxed italic" style={{ color: "#0C4DB3" }}>"{visit.agent_recording_transcript}"</p>
            </div>
          )}
          {visit.borrower_recording_transcript && (
            <div className="rounded-xl p-3" style={{ background: "#F5F6F9", border: "1px solid #EAEBEF" }}>
              <p className="text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "#6B6D76" }}>Borrower Speech Transcript</p>
              <p className="text-xs leading-relaxed italic" style={{ color: "#1C1C1F" }}>"{visit.borrower_recording_transcript}"</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value, children }: { label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium" style={{ color: "#6B6D76" }}>{label}</p>
      {children ?? <p className="text-sm font-semibold mt-0.5" style={{ color: "#1C1C1F" }}>{value}</p>}
    </div>
  );
}

/**
 * Target / Collected / Loan balance.
 *
 * The exact rupee amount, formatted identically to the same figure in the case
 * table — en-IN grouping, which IS lakh notation: 34,18,392 reads as thirty-four
 * lakh eighteen thousand. Until 2026-08-28 this rounded to `(amount/1000)K`,
 * so the table showed a case at ₹2,34,214.32 and the modal for that same case
 * showed ₹234K. Two numbers for one fact, and the rounded one was the one a
 * manager was asked to act on.
 *
 * The lakh/crore line beneath is the readable form, kept SECONDARY and muted:
 * it is for saying out loud, not for reconciling against. Suppressed below
 * ₹1 lakh, where the exact figure is already short enough to read at a glance.
 */
function AmountCard({ label, amount, color }: { label: string; amount: number; color: string }) {
  const short = lakhWords(amount);
  return (
    <div className="rounded-xl p-3 text-center" style={{ background: "#F5F6F9", border: "1px solid #EAEBEF" }}>
      <p className="text-xs" style={{ color: "#6B6D76" }}>{label}</p>
      <p className={`text-[15px] font-bold mt-0.5 tabular-nums ${color}`}>
        ₹{amount.toLocaleString("en-IN")}
      </p>
      {short && (
        <p className="text-[11px] mt-0.5 tabular-nums" style={{ color: "#94a3b8" }}>{short}</p>
      )}
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p style={{ color: "#6B6D76" }}>{label}</p>
      <p className="font-medium capitalize" style={{ color: "#1C1C1F" }}>{value.toLowerCase()}</p>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="py-10 text-center" style={{ color: "#6B6D76" }}>
      <FileText className="w-8 h-8 mx-auto mb-2 opacity-30" />
      <p className="text-sm">{text}</p>
    </div>
  );
}

// ── Case row — 12-col grid on desktop, card below lg ─────────────────────────
// Both renderings carry the same eight fields. A case already visited today is
// dimmed to de-emphasise it without hiding it.

function CaseRow({ c, onOpen }: { c: Case; onOpen: () => void }) {
  // Hover, base tint and the dimmed visited-today state all come from
  // .row-accent in index.css, shared with the Field Agents table. Real CSS
  // :hover rather than mouseenter/mouseleave writing inline styles: a row that
  const isPaid = c.status === "PAID" || (c.target_amount > 0 && c.collected_amount >= c.target_amount);
  // 2026-09-03 — this was `c.visit_count > 0 || c.is_visited_today`, and the
  // chip below fired on `isPartial || hasVisited`. Three faults in one line:
  // visit_count counts EVER, not today; isPartial forced the chip on its own,
  // though a part payment is already reported by the STATUS column and the
  // collected figure; and is_visited_today — which the API computes correctly —
  // was OR'd away to nothing.
  //
  // Measured on the live book the day this was found: 0 cases had been visited
  // that day and 421 were wearing the chip. Every one of them was telling a
  // manager that work had happened today when it had happened weeks earlier.
  //
  // 2026-09-11 — the grey "Visited ×N" chip that used to follow is gone. It
  // counted lifetime visits, and with sticky ownership the count stopped
  // meaning what it was put there to mean: it was added on 2026-09-03 to
  // separate "seen once" from "absorbed four visits", back when a case could
  // pass through four agents. Now the same agent keeps it until it resolves,
  // so the count is that agent's own history, already on their profile and in
  // the detail panel's visit tab — and on a book where 449 of 977 open cases
  // have been worked, it was decorating nearly half the rows with a number
  // nobody acted on. The two chips that remain each answer a live question.
  // The decision lives in rowChip() so it can be tested without a mount.
  const chip = rowChip(c);

  const accent = isPaid
    ? " row-accent-done"
    : c.is_escalated
    ? " row-accent-danger"
    : "";

  const statusChip = chip === "resolved" ? (
    <span
      className="font-semibold px-1.5 py-0.5 rounded-full flex-shrink-0"
      style={{ background: "rgba(34,197,94,0.15)", color: "#15803D", fontSize: 10 }}
    >
      ✓ Resolved
    </span>
  ) : chip === "visited_today" ? (
    // Today only. The one state a manager scanning this list is actually
    // asking about: has someone been to this door yet today.
    <span
      className="font-semibold px-1.5 py-0.5 rounded-full flex-shrink-0"
      style={{ background: "rgba(37,99,235,0.12)", color: "#1D4ED8", fontSize: 10 }}
    >
      ✓ Visited today
    </span>
  ) : null;   // never visited, or not today — no chip

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      className={`row-accent${accent} border-b last:border-0 cursor-pointer block w-full text-left`}
      style={{ borderColor: "#EAEBEF" }}
    >
      {/* ── Desktop table row ── */}
      <div className="hidden lg:grid gap-3 px-4 py-3 text-sm items-center"
           style={{ gridTemplateColumns: TABLE_COLS }}>
        <div className="min-w-0">
          <p className="text-xs font-mono font-medium truncate" style={{ color: "#6B6D76" }}>{c.case_number}</p>
          {statusChip && (
            <div className="flex items-center gap-1.5 mt-0.5 text-xs">{statusChip}</div>
          )}
        </div>
        <div className="min-w-0">
          <p className="font-semibold truncate" style={{ color: "#1C1C1F" }}>{c.customer.full_name}</p>
        </div>
        {/* City moved out from under the name into its own column, so it lines
            up down the table instead of reading as a second line of the
            customer cell. */}
        <div className="text-xs truncate min-w-0" style={{ color: "#6B6D76" }}>
          {c.customer.city}
        </div>
        <div className="min-w-0"><DPDBadge bucket={c.loan.dpd_bucket} /></div>
        {/* ONE badge, not two. The visit-priority band was stacked under
            Case.priority here from 2026-08-27 and removed on 2026-08-28: the
            two answer different questions and routinely disagree ("High" over
            "MEDIUM" on the same row), which reads as a rendering fault rather
            than as two measures. The band still drives the Sort and Priority
            filters above the table, and is shown with its three components in
            the case detail panel, where there is room to say what it means. */}
        <div className="min-w-0"><VisitPriorityBadge priority={c.visit_priority} showLabel={false} /></div>
        {/* Outlook only. "Due now" (arrears + penal) was removed on 2026-08-27:
            sitting one column from "Target / Collected" it was the largest
            number on the row, so it read as the amount to collect. The two
            components remain on the payload as loan.overdue_amount and
            loan.penal_charges for anything that genuinely needs them. */}
        <div className="min-w-0">
          <RecoveryBadge potential={c.recovery?.recovery_potential} compact />
        </div>
        <div className="min-w-0"><CaseStatusBadge status={c.status} /></div>
        <div className="min-w-0">
          <p className="font-semibold truncate" style={{ color: "#1C1C1F" }}>₹{c.target_amount.toLocaleString("en-IN")}</p>
          {c.collected_amount > 0 && (
            <p className="text-xs text-success-600 truncate">₹{c.collected_amount.toLocaleString("en-IN")} paid</p>
          )}
        </div>
        <div className="text-xs min-w-0" style={{ color: "#6B6D76" }}
             title={c.agent_name ?? "Unassigned"}>
          <p className="truncate">{c.agent_name ?? <span style={{ color: "#C4C6CF" }}>Unassigned</span>}</p>
          {/* The owner is sticky now, so when a case DID move somebody decided
              it and said why. That sentence is the most useful thing this cell
              can carry; the full text sits in the title. */}
          {c.last_reassignment?.reason && (
            <p className="truncate mt-0.5" style={{ color: "#94a3b8", fontSize: 11 }}
               title={`Reassigned by ${c.last_reassignment.by ?? "a manager"}: ${c.last_reassignment.reason}`}>
              ↳ {c.last_reassignment.reason}
            </p>
          )}
        </div>
        <div className="text-xs whitespace-nowrap min-w-0" style={{ color: "#6B6D76" }}>{c.allocation_date}</div>
      </div>

      {/* ── Mobile card ── */}
      <div className="lg:hidden px-4 py-3.5">
        {/* Case number + bank + visited */}
        <div className="flex items-center gap-2 text-xs mb-1.5">
          <span className="font-mono font-medium truncate" style={{ color: "#6B6D76" }}>{c.case_number}</span>
          <span className="truncate" style={{ color: "#94a3b8" }}>{c.loan.bank_name?.split(" ")[0]}</span>
          {statusChip}
          <ChevronRight className="w-4 h-4 ml-auto flex-shrink-0" style={{ color: "#C4C6CF" }} />
        </div>

        {/* Customer + city */}
        <p className="font-semibold text-[15px] leading-tight truncate" style={{ color: "#1C1C1F" }}>{c.customer.full_name}</p>
        <p className="text-xs mt-0.5 truncate" style={{ color: "#6B6D76" }}>{c.customer.city}</p>

        {/* The badges. A "due now" figure led this block until 2026-08-27; it
            was removed for the same reason as on the desktop row — the largest
            rupee number on the card is read as the amount to collect, and
            arrears + penal is not that. Target and collected follow below. */}
        <div className="flex flex-wrap items-center gap-1.5 mt-2">
          <DPDBadge bucket={c.loan.dpd_bucket} />
          <VisitPriorityBadge priority={c.visit_priority} />
          <RecoveryBadge potential={c.recovery?.recovery_potential} />
          <CaseStatusBadge status={c.status} />
        </div>

        {/* Money + agent + date */}
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 mt-2.5 pt-2.5" style={{ borderTop: "1px dashed #EAEBEF" }}>
          <div className="min-w-0">
            <span className="font-semibold text-sm" style={{ color: "#1C1C1F" }}>₹{c.target_amount.toLocaleString("en-IN")}</span>
            {c.collected_amount > 0 && (
              <span className="text-xs text-success-600 ml-1.5">₹{c.collected_amount.toLocaleString("en-IN")} paid</span>
            )}
          </div>
          <div className="text-xs text-right min-w-0" style={{ color: "#6B6D76" }}>
            <span className="truncate">{c.agent_name ?? <span style={{ color: "#C4C6CF" }}>Unassigned</span>}</span>
            {c.allocation_date && <span className="ml-1.5" style={{ color: "#94a3b8" }}>· {c.allocation_date}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main cases page ──────────────────────────────────────────────────────────

// The funnel stages the Cases list accepts from the URL — the server's
// field_activity_service.STAGES, restated because a TS module cannot import a
// Python tuple; the server 422s anything else, so a drift here fails loudly.
const ACTIVITY_STAGES = ["planned", "visited", "met", "paid_or_promised", "not_met", "met_no_money"];

export default function ManagerCasesPage() {
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  // Seeded from the URL, like date_from below: the overview's case-pipeline
  // donut links here with ?status=<one CaseStatus> (2026-09-16). Anything not
  // in the select's list falls back to ALL rather than filtering on a value
  // the dropdown cannot show.
  const [statusFilter, setStatusFilter] = useState(() => {
    const s = searchParams.get("status") || "ALL";
    return ["ASSIGNED", "IN_PROGRESS", "PTP_SET", "PARTIALLY_PAID", "PAID", "ESCALATED", "CLOSED"].includes(s) ? s : "ALL";
  });
  // Seeded from ?bucket= for the overview's "Today's Cases by DPD" donut
  // (2026-09-16), same guard as statusFilter above: unknown values → ALL.
  const [bucketFilter, setBucketFilter] = useState(() => {
    const b = searchParams.get("bucket") || "ALL";
    return ["BUCKET_2", "BUCKET_3", "NPA"].includes(b) ? b : "ALL";
  });
  // Server-side, unlike bucketFilter: the recovery label lives in the snapshot
  // table, so narrowing it client-side would only filter the 50 rows already
  // fetched and leave a HIGH-recovery case on page 3 sitting on page 3.
  const [recoveryFilter, setRecoveryFilter] = useState("ALL");
  // Visit priority. Default OFF: the legacy allocation_date ordering is the page
  // a manager already knows, and switching it silently would move every row
  // under them. Turning it on is the deliberate act of asking "what should my
  // team work first".
  const [prioritySort, setPrioritySort] = useState("OFF");
  const [priorityBand, setPriorityBand] = useState("ALL");
  const [dateFrom, setDateFrom] = useState(() => searchParams.get("date_from") || "");
  const [dateTo, setDateTo] = useState(() => searchParams.get("date_to") || "");
  // The date filter used to default to [six months ago, today]. Cases are
  // filtered on allocation_date, whose newest value trails the wall clock on
  // seeded data, so that window both clipped the earliest days and included a
  // stretch with nothing in it. The defaults now come from the data itself, via
  // GET /manager/cases/date-range.
  //
  // Gate the first fetch until they are known, so the page doesn't load
  // unfiltered and then immediately reload with the range applied. A URL that
  // already carries dates (the leaderboard drill-through) needs no lookup.
  const [datesReady, setDatesReady] = useState(
    () => Boolean(searchParams.get("date_from") || searchParams.get("date_to"))
  );
  // The data's own span, kept so the applied-filter chip can tell "the user
  // narrowed the range" from "these are just the defaults", and so clearing
  // that chip restores the full span rather than a hardcoded window.
  const [defaultRange, setDefaultRange] = useState<{ min: string; max: string } | null>(null);
  const [agentId, setAgentId] = useState<string | null>(() => searchParams.get("agent_id"));
  // ── Field activity (2026-09-17) ──────────────────────────────────────────
  // The overview funnel links here with ?activity=<stage>&activity_window=
  // today|7d|30d (&visit_outcome=…). Resolved SERVER-SIDE through the same
  // service that counted the funnel, so the rows are the number — and
  // window-scoped by construction: with activity_window=today a case visited
  // yesterday is not "visited". Held as one object so the three travel and
  // clear together; an unknown stage is dropped rather than sent.
  const [activity, setActivity] = useState<{ stage: string; window: string; outcome: string | null } | null>(() => {
    const stage = searchParams.get("activity");
    if (!stage || !ACTIVITY_STAGES.includes(stage)) return null;
    const w = searchParams.get("activity_window") || "today";
    return { stage, window: ["today", "7d", "30d"].includes(w) ? w : "today", outcome: searchParams.get("visit_outcome") };
  });
  // ── Promises due (2026-09-17) ────────────────────────────────────────────
  // The Promises card's "Due this week" links here with ?ptp_due_from&ptp_due_to.
  const [ptpDue, setPtpDue] = useState<{ from: string; to: string } | null>(() => {
    const from = searchParams.get("ptp_due_from"), to = searchParams.get("ptp_due_to");
    return from && to ? { from, to } : null;
  });
  const [agentName] = useState<string | null>(() => searchParams.get("agent_name"));
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // ─── React Query, 2026-09-10 ────────────────────────────────────────────
  //
  // The effect this replaces did two things in one breath: `setPage(0)` and
  // `fetchCases(0)`, on every change to any filter. The `setPage(0)` was the
  // synchronous write `react-hooks/set-state-in-effect` flagged.
  //
  // PAGE IS NOW DERIVED AGAINST THE FILTER SIGNATURE. Storing which filters a
  // page number belongs to makes the reset fall out of a comparison instead of
  // an effect: change any filter and `pageState.sig` no longer matches, so
  // `page` reads 0 without anything being written. Same behaviour, no cascade.
  // JSON.stringify, not join(): the separator has to be a character no filter
  // value can contain, and reaching for one is how a literal NUL ended up in
  // this file on the first attempt — valid TypeScript, but it made the source
  // read as binary to grep and diff.
  const filterSig = JSON.stringify([statusFilter, recoveryFilter, prioritySort,
                                    priorityBand, dateFrom, dateTo, agentId ?? "",
                                    activity, ptpDue]);
  const [pageState, setPageState] = useState({ sig: filterSig, page: 0 });
  const page = pageState.sig === filterSig ? pageState.page : 0;
  const setPage = useCallback(
    (p: number) => setPageState({ sig: filterSig, page: p }), [filterSig]);

  // Only primitives in the key, so two renders with the same selection hash to
  // the same string and React Query does not treat them as different queries.
  const casesQ = useQuery({
    queryKey: ["manager", "cases", {
      status: statusFilter, recovery: recoveryFilter, sort: prioritySort,
      band: priorityBand, from: dateFrom, to: dateTo,
      agent: agentId ?? null, page,
      activity: activity ? `${activity.stage}|${activity.window}|${activity.outcome ?? ""}` : null,
      ptpDue: ptpDue ? `${ptpDue.from}|${ptpDue.to}` : null,
    }],
    queryFn: async () => {
      try {
        return await getCases({
          status: statusFilter !== "ALL" ? statusFilter : undefined,
          recovery: recoveryFilter !== "ALL" ? recoveryFilter : undefined,
          sort: prioritySort !== "OFF" ? prioritySort : undefined,
          priority_band: priorityBand !== "ALL" ? priorityBand : undefined,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          agent_id: agentId || undefined,
          activity: activity?.stage,
          activity_window: activity?.window,
          visit_outcome: activity?.outcome ?? undefined,
          ptp_due_from: ptpDue?.from,
          ptp_due_to: ptpDue?.to,
          offset: page * PAGE_SIZE,
          limit: PAGE_SIZE,
        });
      } catch (e) {
        toast.error("Failed to load cases");
        throw e;
      }
    },
    // The old code gated the first fetch on the seeded date range so the page
    // did not load every case and then immediately reload a filtered set.
    enabled: datesReady,
    // WITHOUT THIS, `total` FLASHES TO ZERO ON EVERY PAGE CHANGE. The rows are
    // hidden behind the `loading` skeleton either way, but `total` is printed in
    // the header and drives `totalPages > 1` — so the pagination row would
    // disappear and reappear on each click. The old code kept the previous
    // `total` in state until the new response landed; this is that.
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const cases = (casesQ.data?.cases as Case[] | undefined) ?? [];
  const total = casesQ.data?.total ?? 0;
  // `isFetching`, not `isPending`: the old `fetchCases` set `loading` true on
  // every call, so the skeleton showed on page changes too, not just first load.
  //
  // NOT SUFFICIENT ON ITS OWN — see casesViewState.ts. While `datesReady` is
  // false the query is disabled, so `isFetching` is false too, and the table
  // used to claim "No cases match the current filters" before it had asked.
  const loading = casesQ.isFetching;

  // Seed the filter from the data's own span. Runs once; failure just leaves the
  // inputs empty, which means "no date filter" — every case, not zero cases.
  useEffect(() => {
    if (datesReady) return;
    let cancelled = false;
    getCasesDateRange()
      .then((r) => {
        if (cancelled) return;
        if (r.min) setDateFrom(r.min);
        if (r.max) setDateTo(r.max);
        if (r.min && r.max) setDefaultRange({ min: r.min, max: r.max });
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setDatesReady(true); });
    return () => { cancelled = true; };
  }, [datesReady]);

  const displayed = cases.filter((c) => {
    if (bucketFilter !== "ALL" && c.loan.dpd_bucket !== bucketFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        c.customer.full_name.toLowerCase().includes(q) ||
        c.case_number.toLowerCase().includes(q) ||
        (c.agent_name?.toLowerCase() ?? "").includes(q)
      );
    }
    return true;
  });

  // The three-way choice the table body makes, in one place so it can be tested
  // without mounting this page. `!datesReady` is the term whose absence let the
  // empty-state message render before anything had been asked — see
  // casesViewState.ts for the measurement and the rule.
  const view = casesView({
    datesReady,
    isFetching: loading,
    rowCount: displayed.length,
  });

  const totalPages = Math.ceil(total / PAGE_SIZE);

  // Applied filters, surfaced as removable chips whenever the panel is
  // collapsed — the panel hides the controls, never the state.
  const activeFilters = useMemo(() => {
    const out: Array<{ key: string; label: string; clear: () => void }> = [];
    if (statusFilter !== "ALL") {
      out.push({ key: "status", label: statusFilter.replace(/_/g, " "), clear: () => setStatusFilter("ALL") });
    }
    if (recoveryFilter !== "ALL") {
      const labels: Record<string, string> = {
        HIGH: "High recovery", MEDIUM: "Medium recovery", LOW: "Low recovery",
      };
      out.push({
        key: "recovery",
        label: labels[recoveryFilter] ?? recoveryFilter,
        clear: () => setRecoveryFilter("ALL"),
      });
    }
    if (prioritySort !== "OFF") {
      out.push({
        key: "prioritySort",
        // Says the view is narrowed too, not just reordered — resolved cases
        // drop out of the priority view, so the row count changes and a
        // manager who did not read this would think cases had vanished.
        label: prioritySort === "priority_desc"
          ? "Visit priority high → low (open cases only)"
          : "Visit priority low → high (open cases only)",
        clear: () => setPrioritySort("OFF"),
      });
    }
    if (priorityBand !== "ALL") {
      out.push({
        key: "priorityBand",
        label: `Visit priority ${priorityBand}`,
        clear: () => setPriorityBand("ALL"),
      });
    }
    if (bucketFilter !== "ALL") {
      const labels: Record<string, string> = { BUCKET_2: "31–60 DPD", BUCKET_3: "61–90 DPD", NPA: "NPA 90+" };
      out.push({ key: "bucket", label: labels[bucketFilter] ?? bucketFilter, clear: () => setBucketFilter("ALL") });
    }
    if (activity) {
      // Says the WINDOW, because that is what makes the set what it is.
      const stageWords: Record<string, string> = {
        planned: "Planned", visited: "Visited", met: "Met", paid_or_promised: "Paid or promised",
        not_met: "Not met", met_no_money: "Met, no payment or promise",
      };
      const windowWords: Record<string, string> = { today: "today", "7d": "last 7 days", "30d": "last 30 days" };
      const outcome = activity.outcome
        ? ` · ${activity.outcome.split(",").map((o) => OUTCOME_LABEL[o] ?? o.replace(/_/g, " ").toLowerCase()).join(", ")}`
        : "";
      out.push({
        key: "activity",
        label: `Field activity: ${stageWords[activity.stage] ?? activity.stage}${outcome} · ${windowWords[activity.window] ?? activity.window}`,
        clear: () => setActivity(null),
      });
    }
    if (ptpDue) {
      out.push({ key: "ptpDue", label: `Promise due ${fmtDate(ptpDue.from)} → ${fmtDate(ptpDue.to)}`, clear: () => setPtpDue(null) });
    }
    // A chip only when the user has actually narrowed the range. The baseline
    // is the data's own span once we know it; arriving from the leaderboard
    // with dates in the URL counts as narrowed, and clearing then means "no
    // date filter" since we never looked the full span up.
    const narrowed = defaultRange
      ? dateFrom !== defaultRange.min || dateTo !== defaultRange.max
      : Boolean(dateFrom || dateTo);
    if (narrowed) {
      out.push({
        key: "dates",
        label: [dateFrom && fmtDate(dateFrom), dateTo && fmtDate(dateTo)].filter(Boolean).join(" → "),
        clear: () => { setDateFrom(defaultRange?.min ?? ""); setDateTo(defaultRange?.max ?? ""); },
      });
    }
    return out;
  }, [statusFilter, bucketFilter, recoveryFilter, prioritySort, priorityBand,
      dateFrom, dateTo, defaultRange]);

  return (
    <>
      {selectedCaseId && (
        <CaseDetailModal caseId={selectedCaseId} onClose={() => setSelectedCaseId(null)} />
      )}

      <div className="space-y-4">
        <div className="flex items-center justify-between" style={{ animation: `enter 420ms ${EASE} 0ms both` }}>
          <div className="min-w-0">
            <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>Case Management</h1>
            <p className="text-[13px] sm:text-sm" style={{ color: "#6B6D76" }}>
              {total.toLocaleString()} total cases · {cases.filter((c) => c.is_escalated).length} escalated (this page)
            </p>
          </div>
        </div>

        {/* Agent filter chip */}
        {agentId && (
          <div className="flex items-center gap-2" style={{ animation: `enter 300ms ${EASE} 0ms both` }}>
            <div
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium"
              style={{ background: "#EEF3FD", color: "#0C66E4", border: "1px solid #C7D9FA" }}
            >
              <span>Showing cases for: <strong>{agentName || `Agent ${agentId}`}</strong></span>
              <button
                onClick={() => setAgentId(null)}
                style={{ marginLeft: 4, lineHeight: 1, color: "#0C66E4", background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 14 }}
                aria-label="Clear agent filter"
              >
                ×
              </button>
            </div>
          </div>
        )}

        {/* Filters — search is always visible; the rest collapse below lg so
            the list starts near the top of a phone screen. Applied filters
            stay on screen as chips, so collapsing hides controls, not state. */}
        <div className="space-y-3" style={{ animation: `enter 420ms ${EASE} 60ms both` }}>
          <div className="flex gap-2 items-start">
            <div className="flex-1 min-w-0">
              <Input
                placeholder="Search customer, case no, agent..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <button
              type="button"
              onClick={() => setFiltersOpen((v) => !v)}
              aria-expanded={filtersOpen}
              className="tap-target lg:hidden flex items-center gap-1.5 px-3 rounded-xl text-xs font-semibold flex-shrink-0 transition-colors"
              style={{
                background: activeFilters.length > 0 ? "#EEF3FD" : "#fff",
                border: `1px solid ${activeFilters.length > 0 ? "#C7D9FA" : "hsl(var(--border) / 0.6)"}`,
                color: activeFilters.length > 0 ? "#0C66E4" : "#6B6D76",
              }}
            >
              <SlidersHorizontal className="w-4 h-4 flex-shrink-0" />
              Filters
              {activeFilters.length > 0 && (
                <span
                  className="flex items-center justify-center rounded-full text-white font-bold"
                  style={{ minWidth: 16, height: 16, fontSize: 10, background: "#0C66E4", padding: "0 4px" }}
                >
                  {activeFilters.length}
                </span>
              )}
            </button>
          </div>

          {/* Active-filter chips — visible whether or not the panel is open */}
          {activeFilters.length > 0 && (
            <div className="flex flex-wrap gap-2 lg:hidden">
              {activeFilters.map((f) => (
                <span
                  key={f.key}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium"
                  style={{ background: "#EEF3FD", color: "#0C66E4", border: "1px solid #C7D9FA" }}
                >
                  {f.label}
                  <button
                    onClick={f.clear}
                    aria-label={`Remove ${f.label} filter`}
                    className="flex items-center justify-center"
                    style={{ width: 16, height: 16, background: "none", border: "none", color: "#0C66E4", cursor: "pointer", padding: 0 }}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className={`${filtersOpen ? "grid" : "hidden"} lg:flex grid-cols-2 gap-2 lg:gap-3 lg:flex-wrap lg:items-center`}>
            <select className="input w-full lg:w-auto tap-target-h" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Filter by status">
              <option value="ALL">All Status</option>
              {["ASSIGNED", "IN_PROGRESS", "PTP_SET", "PARTIALLY_PAID", "PAID", "ESCALATED", "CLOSED"].map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </select>
            <select className="input w-full lg:w-auto tap-target-h" value={recoveryFilter} onChange={(e) => setRecoveryFilter(e.target.value)} aria-label="Filter by recovery potential">
              <option value="ALL">All Recovery</option>
              <option value="HIGH">High recovery</option>
              <option value="MEDIUM">Medium recovery</option>
              <option value="LOW">Low recovery</option>
            </select>
            {/* Visit priority. Two controls rather than one combined dropdown:
                a manager sorting the whole book and a manager narrowing to the
                HIGH band are different questions, and either is useful without
                the other. Both are server-side — the list paginates there, so a
                client-side sort would only reorder the 50 rows on screen. */}
            <select className="input w-full lg:w-auto tap-target-h" value={prioritySort}
                    onChange={(e) => setPrioritySort(e.target.value)}
                    aria-label="Sort by visit priority">
              <option value="OFF">Sort: Latest first</option>
              <option value="priority_desc">Sort: Visit priority high → low</option>
              <option value="priority_asc">Sort: Visit priority low → high</option>
            </select>
            <select className="input w-full lg:w-auto tap-target-h" value={priorityBand}
                    onChange={(e) => setPriorityBand(e.target.value)}
                    aria-label="Filter by visit priority band">
              <option value="ALL">All visit priorities</option>
              <option value="HIGH">Visit priority HIGH</option>
              <option value="MEDIUM">Visit priority MEDIUM</option>
              <option value="LOW">Visit priority LOW</option>
            </select>
            <select className="input w-full lg:w-auto tap-target-h" value={bucketFilter} onChange={(e) => setBucketFilter(e.target.value)} aria-label="Filter by DPD bucket">
              <option value="ALL">All Buckets</option>
              <option value="BUCKET_2">31–60 DPD</option>
              <option value="BUCKET_3">61–90 DPD</option>
              <option value="NPA">NPA 90+</option>
            </select>
            <label className="flex items-center gap-2 text-xs min-w-0" style={{ color: "#6B6D76" }}>
              <span className="flex-shrink-0">Visit day from</span>
              <input type="date" className="input text-xs py-1.5 px-2 w-full lg:w-36 tap-target-h" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </label>
            <label className="flex items-center gap-2 text-xs min-w-0" style={{ color: "#6B6D76" }}>
              <span className="flex-shrink-0">to</span>
              <input type="date" className="input text-xs py-1.5 px-2 w-full lg:w-36 tap-target-h" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </label>
          </div>
        </div>

        {/* Cases — 12-col table on desktop, cards below lg. Same eight fields
            either way; only the arrangement changes. */}
        <div className="card p-0 overflow-hidden" style={{ animation: `enter 420ms ${EASE} 120ms both` }}>
          <div
            className="hidden lg:grid gap-3 px-4 py-3 border-b text-xs font-semibold uppercase tracking-wide items-center"
            style={{ background: "#F5F6F9", borderColor: "#EAEBEF", color: "#6B6D76",
                     gridTemplateColumns: TABLE_COLS }}
          >
            <span>Case</span>
            <span>Customer</span>
            <span>Location</span>
            <span>DPD</span>
            <span>Visit priority</span>
            <span>Outlook</span>
            <span>Status</span>
            <span>Target / Collected</span>
            <span>Agent</span>
            {/* "Next Visit Day" (was "Next Beat" until 2026-09-16, when the
                manager pages dropped the word "beat"), not "Date". 2026-09-10.
                The column renders `allocation_date`, which is the day the case
                is NEXT SCHEDULED for — not the day it was created or worked.
                Every still-open case is re-stamped by each nightly plan run, so
                a case an agent visited this morning shows TOMORROW here, and a
                reader seeing "Date: 2026-09-11" on a list they are working today
                reasonably concluded the page was broken.

                Measured that day: 44 of the 45 cases visited, and 16 of the 16
                that collected money, all carried tomorrow's date. The first fix
                attempted was to clamp the range to today — which hid every one
                of them, i.e. exactly the rows a manager looks for after a day in
                the field. Reverted. The column was never showing the wrong data,
                it was answering a different question than its label implied. */}
            <span>Next Visit Day</span>
          </div>

          {view === "loading" ? (
            Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="h-20 lg:h-12 border-b animate-pulse" style={{ borderColor: "#EAEBEF", background: "#F5F6F9" }} />
            ))
          ) : view === "empty" ? (
            <div className="py-16 px-4 text-center" style={{ color: "#6B6D76" }}>
              <FileText className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="text-sm">No cases match the current filters</p>
              <p className="text-xs mt-1" style={{ color: "#94a3b8" }}>Try widening the date range or clearing a filter.</p>
            </div>
          ) : (
            displayed.map((c) => <CaseRow key={c.id} c={c} onOpen={() => setSelectedCaseId(c.id)} />)
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          // Count above the controls on a phone; single row once there is room.
          <div className="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-2 text-sm">
            <span className="text-xs sm:text-sm text-center sm:text-left" style={{ color: "#6B6D76" }}>
              Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
            </span>
            <div className="flex items-center gap-2 justify-between sm:justify-end">
              <button
                disabled={page === 0}
                onClick={() => { setPage(page - 1); window.scrollTo({ top: 0 }); }}
                className="tap-target flex-1 sm:flex-none px-4 rounded-xl border text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                style={{ borderColor: "#EAEBEF", color: "#6B6D76" }}
              >
                Previous
              </button>
              <span className="px-2 text-xs whitespace-nowrap flex-shrink-0" style={{ color: "#6B6D76" }}>{page + 1} / {totalPages}</span>
              <button
                disabled={page >= totalPages - 1}
                onClick={() => { setPage(page + 1); window.scrollTo({ top: 0 }); }}
                className="tap-target flex-1 sm:flex-none px-4 rounded-xl border text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                style={{ borderColor: "#EAEBEF", color: "#6B6D76" }}
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
