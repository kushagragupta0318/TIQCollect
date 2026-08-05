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
import { useSearchParams } from "react-router";
import { createPortal } from "react-dom";
import {
  Search, X, MapPin, Clock, CheckCircle2, AlertTriangle,
  Calendar, User, FileText, ChevronRight, SlidersHorizontal,
} from "lucide-react";
import { toast } from "react-hot-toast";
import { getCases, getCaseDetail, getCasesDateRange } from "@/api/manager";
import type { ManagerCaseDetail, VisitRecord } from "@/api/manager";
import { Input } from "@/components/ui/Input";
import { DPDBadge, PriorityBadge, CaseStatusBadge } from "@/components/ui/Badge";
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

function CaseDetailModal({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ManagerCaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"visits" | "payments" | "ptps" | "photos">("visits");
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getCaseDetail(caseId)
      .then(setDetail)
      .catch(() => toast.error("Failed to load case detail"))
      .finally(() => setLoading(false));
  }, [caseId]);

  // Body scroll lock, focus trap, focus restore, and Escape-to-close.
  useModalA11y(true, panelRef, onClose);

  const photos = detail?.photos ?? [];

  return createPortal(
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
        className="w-full sm:max-w-2xl bg-white flex flex-col overflow-hidden rounded-[22px] sm:rounded-[26px] max-h-[88svh] sm:max-h-[90svh] outline-none"
        style={{
          boxShadow: "0 24px 80px rgba(0,0,0,0.22), 0 4px 16px rgba(0,0,0,0.10)",
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
                <InfoRow label="Agent" value={detail.agent_name ?? "Unassigned"} />
                <InfoRow label="City" value={`${detail.customer.city}, ${detail.customer.state}`} />
                <InfoRow label="Collection Stage" value={detail.collection_stage ?? "—"} />
                <InfoRow label="DPD" value={`${detail.loan.dpd} days (${detail.loan.dpd_bucket.replace("_", " ")})`} />
                <InfoRow label="Status">
                  <CaseStatusBadge status={detail.status as never} />
                </InfoRow>
              </div>

              {/* Financial summary */}
              <div className="mt-4 grid grid-cols-3 gap-2 sm:gap-3">
                <AmountCard label="Target" amount={detail.target_amount} color="text-slate-900" />
                <AmountCard label="Collected" amount={detail.collected_amount} color="text-success-600" />
                <AmountCard label="Outstanding" amount={detail.loan.total_outstanding} color="text-danger-600" />
              </div>

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
                          <span className="sm:col-start-5 sm:col-span-3 sm:row-start-1 text-xs" style={{ color: "#6B6D76" }}>{p.mode.replace("_", " ")}</span>
                        </div>
                        <div className="flex items-baseline justify-between gap-2 mt-1 sm:mt-0 sm:contents">
                          <span className="sm:col-start-1 sm:col-span-4 sm:row-start-1 font-mono text-xs truncate" style={{ color: "#6B6D76" }}>{p.receipt_number}</span>
                          <span className="sm:col-start-11 sm:col-span-2 sm:row-start-1 sm:text-right text-xs whitespace-nowrap" style={{ color: "#6B6D76" }}>{fmtDate(p.payment_date)}</span>
                        </div>
                      </div>
                    ))}
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
    </div>,
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

function AmountCard({ label, amount, color }: { label: string; amount: number; color: string }) {
  return (
    <div className="rounded-xl p-3 text-center" style={{ background: "#F5F6F9", border: "1px solid #EAEBEF" }}>
      <p className="text-xs" style={{ color: "#6B6D76" }}>{label}</p>
      <p className={`text-base font-bold mt-0.5 ${color}`}>₹{(amount / 1000).toFixed(0)}K</p>
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
  // scrolls out from under a stationary cursor never fires mouseleave and stays
  // stuck lit. Nothing here may set background inline — an inline value would
  // outrank the class rules and :hover would never paint.
  const accent = c.is_visited_today
    ? " row-accent-done"
    : c.is_escalated
    ? " row-accent-danger"
    : "";

  const visitedChip = c.is_visited_today && (
    <span
      className="font-semibold px-1.5 py-0.5 rounded-full flex-shrink-0"
      style={{ background: "rgba(34,197,94,0.15)", color: "#15803D", fontSize: 10 }}
    >
      ✓ Visited
    </span>
  );

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
      <div className="hidden lg:grid grid-cols-12 gap-3 px-4 py-3 text-sm items-center">
        <div className="col-span-1 min-w-0">
          <p className="text-xs font-mono font-medium truncate" style={{ color: "#6B6D76" }}>{c.case_number}</p>
          {/* The bank's short name used to sit under the case number. Dropped
              here: every case in this list belongs to the same agency, so it
              repeated on every row without separating anything. The phone card
              below still shows it, where the row has space to spare. The
              visited chip keeps this line, rendered only when set so unvisited
              rows don't carry a blank one. */}
          {visitedChip && (
            <div className="flex items-center gap-1.5 mt-0.5 text-xs">{visitedChip}</div>
          )}
        </div>
        <div className="col-span-2 min-w-0">
          <p className="font-semibold truncate" style={{ color: "#1C1C1F" }}>{c.customer.full_name}</p>
        </div>
        {/* City moved out from under the name into its own column, so it lines
            up down the table instead of reading as a second line of the
            customer cell. */}
        <div className="col-span-1 text-xs truncate min-w-0" style={{ color: "#6B6D76" }}>
          {c.customer.city}
        </div>
        <div className="col-span-1"><DPDBadge bucket={c.loan.dpd_bucket} /></div>
        <div className="col-span-1"><PriorityBadge priority={c.priority} /></div>
        <div className="col-span-1"><CaseStatusBadge status={c.status} /></div>
        <div className="col-span-2 min-w-0">
          <p className="font-semibold truncate" style={{ color: "#1C1C1F" }}>₹{c.target_amount.toLocaleString("en-IN")}</p>
          {c.collected_amount > 0 && (
            <p className="text-xs text-success-600 truncate">₹{c.collected_amount.toLocaleString("en-IN")} paid</p>
          )}
        </div>
        <div className="col-span-2 text-xs truncate" style={{ color: "#6B6D76" }}>
          {c.agent_name ?? <span style={{ color: "#C4C6CF" }}>Unassigned</span>}
        </div>
        <div className="col-span-1 text-xs" style={{ color: "#6B6D76" }}>{c.allocation_date}</div>
      </div>

      {/* ── Mobile card ── */}
      <div className="lg:hidden px-4 py-3.5">
        {/* Case number + bank + visited */}
        <div className="flex items-center gap-2 text-xs mb-1.5">
          <span className="font-mono font-medium truncate" style={{ color: "#6B6D76" }}>{c.case_number}</span>
          <span className="truncate" style={{ color: "#94a3b8" }}>{c.loan.bank_name?.split(" ")[0]}</span>
          {visitedChip}
          <ChevronRight className="w-4 h-4 ml-auto flex-shrink-0" style={{ color: "#C4C6CF" }} />
        </div>

        {/* Customer + city */}
        <p className="font-semibold text-[15px] leading-tight truncate" style={{ color: "#1C1C1F" }}>{c.customer.full_name}</p>
        <p className="text-xs mt-0.5 truncate" style={{ color: "#6B6D76" }}>{c.customer.city}</p>

        {/* All three badges */}
        <div className="flex flex-wrap items-center gap-1.5 mt-2">
          <DPDBadge bucket={c.loan.dpd_bucket} />
          <PriorityBadge priority={c.priority} />
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

export default function ManagerCasesPage() {
  const [searchParams] = useSearchParams();
  const [cases, setCases] = useState<Case[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [bucketFilter, setBucketFilter] = useState("ALL");
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
  const [agentName] = useState<string | null>(() => searchParams.get("agent_name"));
  const [page, setPage] = useState(0);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const fetchCases = useCallback(
    (pg: number) => {
      setLoading(true);
      getCases({
        status: statusFilter !== "ALL" ? statusFilter : undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        agent_id: agentId || undefined,
        offset: pg * PAGE_SIZE,
        limit: PAGE_SIZE,
      })
        .then((r) => {
          setCases((r.cases as Case[]) ?? []);
          setTotal(r.total);
        })
        .catch(() => toast.error("Failed to load cases"))
        .finally(() => setLoading(false));
    },
    [statusFilter, dateFrom, dateTo, agentId]
  );

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

  useEffect(() => {
    if (!datesReady) return;
    setPage(0);
    fetchCases(0);
  }, [fetchCases, datesReady]);

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

  const totalPages = Math.ceil(total / PAGE_SIZE);

  // Applied filters, surfaced as removable chips whenever the panel is
  // collapsed — the panel hides the controls, never the state.
  const activeFilters = useMemo(() => {
    const out: Array<{ key: string; label: string; clear: () => void }> = [];
    if (statusFilter !== "ALL") {
      out.push({ key: "status", label: statusFilter.replace(/_/g, " "), clear: () => setStatusFilter("ALL") });
    }
    if (bucketFilter !== "ALL") {
      const labels: Record<string, string> = { BUCKET_2: "31–60 DPD", BUCKET_3: "61–90 DPD", NPA: "NPA 90+" };
      out.push({ key: "bucket", label: labels[bucketFilter] ?? bucketFilter, clear: () => setBucketFilter("ALL") });
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
  }, [statusFilter, bucketFilter, dateFrom, dateTo, defaultRange]);

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
            <select className="input w-full lg:w-auto tap-target-h" value={bucketFilter} onChange={(e) => setBucketFilter(e.target.value)} aria-label="Filter by DPD bucket">
              <option value="ALL">All Buckets</option>
              <option value="BUCKET_2">31–60 DPD</option>
              <option value="BUCKET_3">61–90 DPD</option>
              <option value="NPA">NPA 90+</option>
            </select>
            <label className="flex items-center gap-2 text-xs min-w-0" style={{ color: "#6B6D76" }}>
              <span className="flex-shrink-0">From</span>
              <input type="date" className="input text-xs py-1.5 px-2 w-full lg:w-36 tap-target-h" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </label>
            <label className="flex items-center gap-2 text-xs min-w-0" style={{ color: "#6B6D76" }}>
              <span className="flex-shrink-0">To</span>
              <input type="date" className="input text-xs py-1.5 px-2 w-full lg:w-36 tap-target-h" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </label>
          </div>
        </div>

        {/* Cases — 12-col table on desktop, cards below lg. Same eight fields
            either way; only the arrangement changes. */}
        <div className="card p-0 overflow-hidden" style={{ animation: `enter 420ms ${EASE} 120ms both` }}>
          <div
            className="hidden lg:grid grid-cols-12 gap-3 px-4 py-3 border-b text-xs font-semibold uppercase tracking-wide"
            style={{ background: "#F5F6F9", borderColor: "#EAEBEF", color: "#6B6D76" }}
          >
            <span className="col-span-1">Case</span>
            <span className="col-span-2">Customer</span>
            <span className="col-span-1">Location</span>
            <span className="col-span-1">DPD</span>
            <span className="col-span-1">Priority</span>
            <span className="col-span-1">Status</span>
            <span className="col-span-2">Target / Collected</span>
            <span className="col-span-2">Agent</span>
            <span className="col-span-1">Date</span>
          </div>

          {loading ? (
            Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="h-20 lg:h-12 border-b animate-pulse" style={{ borderColor: "#EAEBEF", background: "#F5F6F9" }} />
            ))
          ) : displayed.length === 0 ? (
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
                onClick={() => { const np = page - 1; setPage(np); fetchCases(np); window.scrollTo({ top: 0 }); }}
                className="tap-target flex-1 sm:flex-none px-4 rounded-xl border text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                style={{ borderColor: "#EAEBEF", color: "#6B6D76" }}
              >
                Previous
              </button>
              <span className="px-2 text-xs whitespace-nowrap flex-shrink-0" style={{ color: "#6B6D76" }}>{page + 1} / {totalPages}</span>
              <button
                disabled={page >= totalPages - 1}
                onClick={() => { const np = page + 1; setPage(np); fetchCases(np); window.scrollTo({ top: 0 }); }}
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
