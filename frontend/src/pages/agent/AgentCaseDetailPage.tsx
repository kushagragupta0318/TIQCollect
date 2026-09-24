// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-07-13 — New "Exact window today" UI in the Log Call modal (Scheduling
//   Intelligence section): three urgent-preset chips + From/Until time
//   pickers + Clear-window link (line 841-882), backed by timeTodayToISO/
//   isoToLocalTime helpers (line 74-87) and URGENT_PRESETS (line 89-93).
//   Auto-reoptimizes after logging a call only if a window/urgent preset was
//   actually set (line ~240) — a plain outcome log doesn't trigger it.
//   Closes the loop: an agent tapping "ASAP (30 min)" now flows all the way
//   through to CaseService.reoptimize_beat()'s forced_next override.
// 2026-07-30 — Payments tab: PENDING_VERIFICATION payments (recorded offline,
//   without a borrower OTP at visit time) now carry a "Verify now" affordance
//   (PendingPaymentVerify, bottom of file) that runs sendPaymentOtp({payment_id})
//   → verifyPaymentOtp once the borrower has signal, promoting the payment to
//   VERIFIED. Closes the deferred half of the OTP feature. See
//   prototype_to_product/30.07.md.
//   Full detail + why: /changelog.md
// 2026-08-05 - The bottom "Record Visit" bar is position:fixed but behaved as
//   if absolute, appearing only after scrolling to the end of the page. Cause
//   was not in this file: .page-fade-in on the layout's <main> retained a
//   transform, which makes it the containing block for fixed descendants.
//   Fixed in index.css; no change needed here.
// 2026-08-17 — Overview tab: the Collection Target progress bar and the badge
//   row below it are now one card — CollectionDonut (bottom of file) on the
//   left, the DPD/priority/loan-type/language/SLA chips on the right. Two
//   stacked full-width rows became one glance.
//   The donut is deliberately a METER (one arc over a recessive track), not a
//   two-slice pie: the data is a single ratio against a limit, and slicing it
//   into collected-vs-remaining would present them as peer categories to be
//   compared by area. That is why the rupee figures stay as text rather than
//   becoming a second segment. Arc #2563EB validated for contrast against the
//   card surface; the ring geometry is copied from RankingRing on the profile
//   page so both agent-side rings match.
// ──────────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAnimatedValue } from "@/hooks/useAnimatedValue";
import { useParams, useNavigate } from "react-router";
import { ArrowLeft, Phone, Navigation, Calendar, MapPin, CheckCircle, MessageCircle, Lock, Unlock, Sparkles, RefreshCw, Clock, AlertTriangle, TrendingUp, Zap, PhoneCall, X, ShieldCheck, Send, Languages, Flag, Ban, ClipboardList, CreditCard, Camera, CalendarClock, type LucideIcon } from "lucide-react";
import { toast } from "react-hot-toast";
import { AiBadge } from "@/components/ui/AiBadge";
import { RepaymentScore, type RepaymentScoreData } from "@/components/ui/RepaymentScore";
import { getCaseDetail, handoverCase, getVisitStrategy, logCall, notifyCase, reoptimizeBeat, sendPaymentOtp, verifyPaymentOtp, type LogCallPayload } from "@/api/agent";
import { useVoiceCall } from "@/hooks/useVoiceCall";
import CallModal from "@/components/ui/CallModal";
import { useBeat } from "@/contexts/useBeat";
import { useModalA11y } from "@/hooks/useModalA11y";
import { DPDBadge, VisitPriorityBadge, CaseStatusBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import OtpInput from "@/components/ui/OtpInput";
import { haversineM } from "@/lib/geo";
import { geo, geoAvailable } from "@/lib/deviceLocation";
import { errorDetail } from "@/lib/apiError";
import type { CaseStatus, DPDBucket, VisitPriority } from "@/types";

interface CaseDetail {
  // Repayment likelihood, computed live per case by case_service.
  // Optional: the block degrades to null rather than failing the page.
  repayment?: RepaymentScoreData | null;
  id: string; case_number: string;
  // The shared unions, not bare strings. They were `string`, so every
  // badge that takes the union needed `as any` at the call site — which
  // also meant a value outside the union would have rendered blank
  // rather than failing the build.
  status: CaseStatus;
  target_amount: number; collected_amount: number; visit_count: number;
  max_visits_allowed: number; is_escalated: boolean; allocation_date: string | null;
  handover_notes: string | null; collection_stage: string | null;
  visit_priority?: VisitPriority | null;
  customer: {
    id: string; full_name: string; phone_primary: string; phone_alternate: string | null;
    address_line1: string | null; address_line2: string | null;
    city: string; state: string; pincode: string | null;
    latitude: number; longitude: number;
    pan_masked: string | null; aadhaar_masked: string | null;
    is_hostile: boolean; do_not_contact: boolean; requires_female_agent: boolean;
    customer_segment: string | null; language_preference: string; customer_ref: string;
  };
  loan: {
    loan_account_number: string; loan_account_masked: string | null;
    loan_type: string; bank_name: string; dpd: number; dpd_bucket: DPDBucket; status: string; npa_flag: boolean;
    sanctioned_amount: number; outstanding_principal: number; outstanding_interest: number;
    penal_charges: number; total_outstanding: number; overdue_amount: number;
    emi_amount: number; tenure_months: number; interest_rate: number;
    last_payment_date: string | null; last_payment_amount: number | null;
    next_due_date: string | null; legal_status: string; settlement_status: string;
  };
  visits: Array<{ id: string; check_in_time: string; outcome: string; customer_met: boolean; visit_number: number; geo_verified: boolean; person_met?: string; not_met_reason?: string; ai_visit_note?: string | null; agent_recording_transcript?: string | null; borrower_recording_transcript?: string | null }>;
  payments: Array<{ id: string; amount: number; mode: string; receipt_number: string; payment_date: string; status: string }>;
  ptps: Array<{ id: string; committed_amount: number; committed_date: string; status: string; customer_reason: string }>;
  photos: Array<{ photo_type: string; view_url: string | null; storage_key: string; captured_at: string | null; visit_id: string }>;
}

const OUTCOME_LABELS: Record<string, { label: string; tag: string; color: string }> = {
  PAID_FULL:       { label: "Paid in Full",         tag: "PAID FULL",     color: "text-success-700 bg-success-50" },
  PART_PAID:       { label: "Partial Payment",      tag: "PART PAID",     color: "text-success-600 bg-success-50" },
  PTP:             { label: "Promise to Pay",       tag: "PTP",           color: "text-warning-700 bg-warning-50" },
  PART_PAID_PTP:   { label: "Part Paid + PTP",      tag: "PART+PTP",      color: "text-warning-600 bg-warning-50" },
  BROKEN_PTP:      { label: "PTP Broken",           tag: "BROKEN PTP",    color: "text-danger-600 bg-danger-50" },
  RTP:             { label: "Refuse to Pay",        tag: "RTP",           color: "text-danger-700 bg-danger-100" },
  DISPUTE:         { label: "Dispute",              tag: "DISPUTE",       color: "text-danger-700 bg-danger-100" },
  NOT_AVAILABLE:   { label: "Not Available",        tag: "NOT AVAIL",     color: "text-slate-600 bg-slate-50" },
  ADDRESS_ISSUE:   { label: "Address Issue",        tag: "ADDR ISSUE",    color: "text-slate-600 bg-slate-50" },
  DECEASED:        { label: "Deceased",             tag: "DECEASED",      color: "text-slate-700 bg-slate-100" },
  REVISIT:         { label: "Revisit Required",     tag: "REVISIT",       color: "text-brand-600 bg-brand-50" },
};

// 2026-09-22 — the per-language flag/flower emoji set is gone. Flags stand
// for countries, not languages, and the rest were decoration; one language
// glyph beside the name says the same thing without the guesswork.

// Combine a "HH:MM" from a <input type="time"> with today's date -> ISO string
function timeTodayToISO(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
}

// Reverse of the above, for displaying a stored ISO value back in a time input
function isoToLocalTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const URGENT_PRESETS = [
  { label: "ASAP (30 min)", minutes: 30 },
  { label: "Within 1 hr", minutes: 60 },
  { label: "Within 2 hrs", minutes: 120 },
] as const;

export default function AgentCaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { refresh: refreshBeat } = useBeat();
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"overview" | "visits" | "payments" | "ptps" | "strategy" | "photos">("overview");
  const [handoverNotes, setHandoverNotes] = useState("");
  const [handoverSubmitting, setHandoverSubmitting] = useState(false);
  const [handoverDone, setHandoverDone] = useState(false);
  const [userLoc, setUserLoc] = useState<{ lat: number; lon: number } | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [showCallModal, setShowCallModal] = useState(false);
  const callModalRef = useRef<HTMLDivElement>(null);
  const { activeCall, startCall, hangUp } = useVoiceCall();
  const [callSubmitting, setCallSubmitting] = useState(false);
  const initCallForm = (): LogCallPayload => ({ outcome: "ANSWERED", phone_used: "PRIMARY" });
  const [callForm, setCallForm] = useState<LogCallPayload>(initCallForm());

  // Declared after callForm — it resets that state, so it cannot be hoisted
  // above the useState that creates it.
  const closeCallModal = useCallback(() => {
    setShowCallModal(false);
    setCallForm(initCallForm());
  }, []);
  // Body scroll lock, focus trap, focus restore and Escape for the call modal.
  useModalA11y(showCallModal, callModalRef, closeCallModal);

  const requestLocation = useCallback((): number | undefined => {
    if (!geoAvailable()) {
      setGeoError("This device/browser does not support location.");
      return undefined;
    }
    setGeoError(null);
    const onPos = (p: GeolocationPosition) => { setUserLoc({ lat: p.coords.latitude, lon: p.coords.longitude }); setGeoError(null); };
    const onErr = (e: GeolocationPositionError) => {
      setGeoError(
        e.code === e.PERMISSION_DENIED
          ? "Location is blocked. Click the tune/lock icon next to the URL → Location → Allow, then tap Retry. (Geolocation also needs HTTPS or localhost.)"
          : e.code === e.POSITION_UNAVAILABLE
            ? "Location unavailable. Move to an open area and tap Retry."
            : "Location timed out. Tap Retry.",
      );
    };
    // Fast first fix: coarse/cached, ~1s, hard-capped at 5s so it never hangs.
    geo.getCurrentPosition(onPos, onErr, {
      enableHighAccuracy: false, timeout: 5000, maximumAge: 60000,
    });
    // Then refine to a precise fix for the 100m geo-fence.
    return geo.watchPosition(onPos, onErr, {
      enableHighAccuracy: true, timeout: 10000, maximumAge: 5000,
    });
  }, []);

  useEffect(() => {
    const watchId = requestLocation();
    return () => { if (watchId !== undefined) geo.clearWatch(watchId); };
  }, [requestLocation]);

  const reloadCase = useCallback(async () => {
    if (!id) return;
    try {
      const data = await getCaseDetail(id);
      setCaseData(data as CaseDetail);
    } catch { /* keep current view */ }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    getCaseDetail(id)
      .then((data) => setCaseData(data as CaseDetail))
      .catch(() => toast.error("Case not found"))
      .finally(() => setLoading(false));
  }, [id]);

  // ─── React Query, 2026-09-10 ────────────────────────────────────────────
  //
  // WAS: an effect that fired `fetchStrategy()` whenever `tab` became
  // "strategy" and no brief was loaded, guarding itself with `!strategyLoading`
  // against re-entering while a request was in flight. That guard is exactly
  // what `enabled` plus React Query's in-flight deduplication give for free,
  // and the `setStrategyLoading(true)` inside it was one of this page's two
  // `react-hooks/set-state-in-effect` errors.
  //
  // Each option reproduces a specific line of the old behaviour:
  //
  //   enabled              fetch only once the Strategy tab is open — the brief
  //                        is an LLM call and has to stay lazy.
  //   staleTime: Infinity  the old code returned early when `strategy` was
  //                        already set, so tabbing away and back never
  //                        re-fetched. An ERRORED query is stale regardless, so
  //                        returning after a failure does retry — which is what
  //                        the old `!strategy` check did too.
  //   retry: false         the old catch fired one toast per attempt. The
  //                        client's global `retry: 1` would have made two
  //                        requests and two toasts for one failure.
  //
  // The toast lives in the queryFn rather than in an `isError` effect because
  // useQuery has no `onError` in v5, and an effect watching `isError` fires
  // twice under StrictMode — a duplicate toast for a single failure.
  const strategyQ = useQuery({
    queryKey: ["agent", "case", id, "visit-strategy"],
    queryFn: async () => {
      try {
        return await getVisitStrategy(id!);
      } catch (e) {
        toast.error("Could not generate strategy — please try again");
        throw e;
      }
    },
    enabled: tab === "strategy" && !!id,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const strategy = strategyQ.data ?? null;
  const strategyLoading = strategyQ.isFetching;

  const progressPct = caseData
    ? Math.min(Math.round((caseData.collected_amount / Math.max(caseData.target_amount, 1)) * 100), 100)
    : 0;
  const animatedProgressPct = useAnimatedValue(progressPct);

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <div className="flex items-center justify-center h-screen"><div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" /></div>;
  if (!caseData) return <div className="flex flex-col items-center justify-center h-screen gap-4"><p className="text-slate-500">Case not found</p><Button onClick={() => navigate("/agent/cases")}>Back</Button></div>;

  const c = caseData;
  const activePTP = c.ptps.find((p) => p.status === "ACTIVE");
  const maxVisitsReached = c.visit_count >= c.max_visits_allowed;
  const hasVerifiedContact = c.visits.some((v) => v.customer_met);
  const photos = c.photos ?? [];

  // 2026-09-07 — this block called Date.now() DURING RENDER. Rendering has to be
  // a pure function of props and state: an impure read makes two renders of the
  // same state produce different output, which is exactly what React's
  // memoization and concurrent rendering are allowed to assume cannot happen.
  //
  // The clock is now state, so render is pure, and a slow interval keeps a
  // long-open page from showing a countdown frozen at mount. Sixty seconds is
  // chosen against what the value is: hours remaining, displayed to the hour.
  const slaInfo = (() => {
    if (!c.allocation_date) return null;
    const deadline = new Date(c.allocation_date + "T00:00:00");
    deadline.setDate(deadline.getDate() + 3);
    const h = Math.round((deadline.getTime() - nowMs) / 3_600_000);
    if (h < 0) return { label: "SLA Overdue", cls: "text-danger-700 bg-danger-50 border-danger-200", desc: `${Math.abs(h)}h past deadline` };
    if (h < 24) return { label: `${h}h remaining`, cls: "text-warning-700 bg-warning-50 border-warning-200", desc: "SLA expires today" };
    return { label: `${Math.ceil(h / 24)} days left`, cls: "text-slate-700 bg-slate-50 border-slate-200", desc: `SLA deadline: ${deadline.toLocaleDateString("en-IN")}` };
  })();

  // 2026-09-07 — handleFlagCustomer was removed from here. It called
  // api/agent.flagCustomer to mark a borrower hostile or do-not-contact, and it
  // was wired to NOTHING: no button, no menu item, no caller anywhere in the
  // page. It had been dead since it was written.
  //
  // THE CAPABILITY STILL EXISTS. `flagCustomer` is a real endpoint and the
  // allocator reads both flags as hard gates — a hostile or DNC borrower is
  // blocked from allocation entirely. What is missing is only the control that
  // would let an agent set them from the field, which is a product decision
  // about who may flag a borrower, not a lint fix.

  async function handleHandover(returnToPool: boolean) {
    if (!handoverNotes.trim()) { toast.error("Please write handover notes first"); return; }
    setHandoverSubmitting(true);
    try {
      await handoverCase(c.id, handoverNotes, returnToPool);
      setHandoverDone(true);
      toast.success(returnToPool ? "Case returned to pool with handover notes" : "Handover notes saved");
      if (returnToPool) {
        await refreshBeat();
        setTimeout(() => navigate("/agent/cases"), 1500);
      }
    } catch {
      toast.error("Failed to submit handover");
    } finally {
      setHandoverSubmitting(false);
    }
  }
  async function handleLogCall() {
    if (!id) return;
    setCallSubmitting(true);
    try {
      const payload: LogCallPayload = { ...callForm };
      if (payload.outcome !== "ANSWERED") {
        delete payload.duration_seconds;
        delete payload.customer_response_notes;
        delete payload.visit_feasible_today;
        delete payload.best_time_to_visit;
        delete payload.available_from;
        delete payload.available_until;
        delete payload.blocked_until_date;
        delete payload.payment_intent_signalled;
        delete payload.verbal_payment_date;
        delete payload.alternate_location_hint;
      }
      if (payload.blocked_until_date === "") delete payload.blocked_until_date;
      if (payload.verbal_payment_date === "") delete payload.verbal_payment_date;
      if (payload.best_time_to_visit === "") delete payload.best_time_to_visit;
      if (payload.alternate_location_hint === "") delete payload.alternate_location_hint;
      if (payload.customer_response_notes === "") delete payload.customer_response_notes;
      await logCall(id, payload);
      toast.success("Call logged — intel saved for AI ranking");
      setShowCallModal(false);
      setCallForm(initCallForm());

      // Auto-reoptimize only when this call actually changed routing-relevant
      // state (a real window or urgent preset) — skip it for plain outcome
      // logs (No Answer, or Answered with no scheduling info) where nothing
      // about the route needs to change.
      if ((payload.available_from || payload.available_until) && userLoc) {
        try {
          const result = await reoptimizeBeat(userLoc.lat, userLoc.lon);
          if (result.optimized) toast.success("Route updated for the new time window");
          await refreshBeat();
        } catch {
          // agent can still re-optimize manually from the beat map
        }
      }
    } catch {
      toast.error("Failed to log call");
    } finally {
      setCallSubmitting(false);
    }
  }

  const canRecordVisit = !["PAID", "CLOSED", "WRITTEN_OFF"].includes(c.status);



  const distanceM = userLoc
    ? Math.round(haversineM(userLoc.lat, userLoc.lon, c.customer.latitude, c.customer.longitude))
    : null;
  const withinFence = distanceM !== null && distanceM <= 100;

  async function openWhatsApp(type: "reminder" | "ptp" | "receipt") {
    try {
      await notifyCase(c.id, type);
      toast.success("Message sent via WhatsApp & SMS");
    } catch {
      toast.error("Could not send message");
    }
  }

  const tabs = [
    { id: "overview",  label: "Overview" },
    { id: "strategy",  label: "Strategy" },
    { id: "visits",    label: `Visits (${c.visits.length})` },
    { id: "payments",  label: `Payments (${c.payments.length})` },
    { id: "ptps",      label: `PTPs (${c.ptps.length})` },
    // The inner array needs its own `as const`: the outer one does not reach
    // through a conditional spread, so `id` widened to `string` and broke
    // setTab's parameter type.
    ...(photos.length > 0 ? [{ id: "photos", label: `Photos (${photos.length})` }] as const : [] as const),
  ] as const;

  return (
    <div className="min-h-svh bg-slate-50 pb-28 lg:pb-24">
      {activeCall && <CallModal call={activeCall} onHangUp={hangUp} />}
      {/* Header */}
      <div className="tiq-glass-bar border-b border-slate-100/70 sticky top-0 z-20">
        <div className="flex items-center gap-3 p-4">
          <button onClick={() => navigate(-1)} className="p-1 -ml-1 text-slate-400 hover:text-slate-700"><ArrowLeft className="w-5 h-5" /></button>
          <div className="flex-1 min-w-0">
            <h1 className="font-bold text-slate-900 truncate">{c.customer.full_name}</h1>
            <p className="text-xs text-slate-400">{c.case_number} · {c.loan.bank_name}</p>
          </div>
          <CaseStatusBadge status={c.status} />
        </div>
        <div className="flex px-4 gap-3 border-t border-slate-50 overflow-x-auto scrollbar-hide">
          {tabs.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className={`py-2.5 text-xs font-medium border-b-2 whitespace-nowrap transition-colors ${tab === t.id ? "border-brand-500 text-brand-600" : "border-transparent text-slate-400 hover:text-slate-600"}`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4 space-y-4">
        {tab === "overview" && (
          <>
            {/* Alert banners */}
            {c.is_escalated && <AlertBanner icon={Flag} color="danger" message="Case escalated — Manager review pending" />}
            {c.customer.is_hostile && <AlertBanner icon={AlertTriangle} color="warning" message="Customer marked hostile — exercise caution" />}
            {/* The API has always sent requires_female_agent and nothing rendered
                it. Allocation now respects the flag, but a case can still reach an
                agent by handover or manual reassignment — so the person at the door
                needs to see it, not just the scheduler. */}
            {c.customer.requires_female_agent && <AlertBanner icon={Ban} color="danger" message="Female agent required — do not visit; hand this case back to your manager" />}
            {/* Repayment likelihood. Sits BELOW the eligibility banners on
                purpose: do-not-contact and female-agent are instructions, this
                is only a steer on how to approach the conversation. It never
                tells an agent to skip a visit. */}
            {c.repayment && <RepaymentScore data={c.repayment} />}
            {activePTP && (
              <div className="card border-brand-200 bg-brand-50 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Calendar className="w-4 h-4 text-brand-600" />
                  <p className="text-sm text-brand-700 font-medium">
                    Active PTP due {new Date(activePTP.committed_date).toLocaleDateString("en-IN")}
                  </p>
                </div>
                <button onClick={() => openWhatsApp("ptp")} className="text-xs flex items-center gap-1 text-green-600 font-medium hover:underline">
                  <MessageCircle className="w-3.5 h-3.5" /> Remind
                </button>
              </div>
            )}

            {/* Collection target + case metadata — one card: meter on the left,
                the chips that qualify it on the right. Previously a full-width
                progress bar with the badges as a separate un-carded row below,
                which split "how far along" from the context that explains it
                (bucket, priority, SLA) across two glances instead of one. */}
            <div className="card">
              <p className="text-sm font-semibold text-slate-700 mb-3">Collection Target</p>
              <div className="flex items-center gap-4">
                <CollectionDonut pct={animatedProgressPct} />

                <div className="min-w-0 flex-1 space-y-2.5">
                  <div className="flex flex-wrap gap-2 items-center">
                    <DPDBadge bucket={c.loan.dpd_bucket} />
                    <VisitPriorityBadge priority={c.visit_priority} />
                    <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-full">{c.loan.dpd} DPD</span>
                    <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-full">{c.loan.loan_type}</span>
                    <span className="text-xs bg-brand-50 text-brand-700 border border-brand-100 px-2 py-1 rounded-full font-medium">
                      <Languages className="w-3 h-3 inline-block -mt-0.5 mr-1" />{c.customer.language_preference}
                    </span>
                    {slaInfo && (
                      <span className={`text-xs px-2 py-1 rounded-full border font-medium ${slaInfo.cls}`} title={slaInfo.desc}>
                        ⏱ {slaInfo.label}
                      </span>
                    )}
                  </div>

                  {/* The arc carries the ratio; these carry the two rupee
                      figures behind it. Kept as text on purpose — the meter is
                      deliberately one series, so the amounts are not a second
                      encoding competing with it. */}
                  <div className="flex flex-wrap gap-x-4 text-xs text-slate-500 border-t border-slate-100 pt-2">
                    <span>Target: <span className="font-semibold text-slate-700">₹{c.target_amount.toLocaleString("en-IN")}</span></span>
                    <span>Collected: <span className="font-semibold text-success-600">₹{c.collected_amount.toLocaleString("en-IN")}</span></span>
                  </div>
                </div>
              </div>
            </div>

            {/* Handover Notes — shown when max visits reached */}
            {maxVisitsReached && !handoverDone && (
              <div className="card border-warning-200 bg-warning-50 space-y-3">
                <div className="flex items-start gap-2">
                  <RefreshCw className="w-4 h-4 mt-0.5 text-warning-600 flex-shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-warning-800">Max Visits Reached ({c.visit_count}/{c.max_visits_allowed})</p>
                    <p className="text-xs text-warning-700 mt-0.5">Write handover notes for the next agent before returning this case to the pool.</p>
                  </div>
                </div>
                {c.handover_notes && (
                  <div className="bg-white rounded-lg p-2.5 text-xs text-slate-600 border border-warning-200">
                    <p className="font-medium text-slate-700 mb-1">Previous notes:</p>
                    <p className="italic">"{c.handover_notes}"</p>
                  </div>
                )}
                <textarea
                  className="w-full rounded-xl border border-warning-200 bg-white text-sm p-3 focus:outline-none focus:ring-2 focus:ring-warning-300 resize-none placeholder-slate-400"
                  placeholder="Customer situation, best time to visit, what to try next, key information for successor agent..."
                  rows={4}
                  value={handoverNotes}
                  onChange={(e) => setHandoverNotes(e.target.value)}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => handleHandover(false)}
                    disabled={handoverSubmitting}
                    className="flex-1 py-2 rounded-lg text-sm font-medium border border-slate-200 bg-white text-slate-700 hover:border-slate-300 disabled:opacity-50"
                  >
                    Save Notes
                  </button>
                  <button
                    onClick={() => handleHandover(true)}
                    disabled={handoverSubmitting}
                    className="flex-1 py-2 rounded-lg text-sm font-medium border border-warning-400 bg-warning-500 text-white hover:bg-warning-600 disabled:opacity-50"
                  >
                    {handoverSubmitting ? "Submitting…" : "Return to Pool"}
                  </button>
                </div>
              </div>
            )}
            {handoverDone && (
              <div className="card border-success-200 bg-success-50 flex items-center gap-3">
                <CheckCircle className="w-5 h-5 text-success-600 flex-shrink-0" />
                <p className="text-sm font-medium text-success-700">Handover submitted. Returning to cases list…</p>
              </div>
            )}

            {/* Quick actions — four buttons, so the column count has to divide
                four or the last one is left stranded on its own row. lg:3 did
                exactly that: 3 + 1. 2 on a phone, 4 in a row once there is
                width for it; both are symmetric. */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <ActionBtn icon={<Phone className="w-4 h-4" />} label="Call" color="bg-success-50 text-success-700 border-success-100" onClick={() => startCall(c.customer.phone_primary, c.customer.full_name)} />
              <ActionBtn icon={<Navigation className="w-4 h-4" />} label="Navigate" color="bg-brand-50 text-brand-700 border-brand-100" onClick={() => window.open(`https://www.google.com/maps/dir/?api=1&destination=${c.customer.latitude},${c.customer.longitude}&travelmode=driving`, "_blank")} />
              <ActionBtn icon={<MessageCircle className="w-4 h-4" />} label="WhatsApp" color="bg-green-50 text-green-700 border-green-100" onClick={() => openWhatsApp("reminder")} />
              <ActionBtn icon={<PhoneCall className="w-4 h-4" />} label="Log Call" color="bg-brand-50 text-brand-700 border-brand-100" onClick={() => setShowCallModal(true)} />
            </div>

            {hasVerifiedContact ? (
              <InfoSection title="Loan Financials">
                <InfoRow label="Total Outstanding" value={`₹${c.loan.total_outstanding.toLocaleString("en-IN")}`} />
                <InfoRow label="Overdue Amount" value={`₹${c.loan.overdue_amount.toLocaleString("en-IN")}`} highlight />
                <InfoRow label="EMI Amount" value={`₹${c.loan.emi_amount.toLocaleString("en-IN")}`} />
                <InfoRow label="Interest Rate" value={`${c.loan.interest_rate}% p.a.`} />
                {c.loan.penal_charges > 0 && <InfoRow label="Penal Charges" value={`₹${c.loan.penal_charges.toLocaleString("en-IN")}`} highlight />}
                {c.loan.last_payment_date && (
                  <InfoRow
                    label="Last Payment"
                    value={`₹${c.loan.last_payment_amount?.toLocaleString("en-IN") ?? "–"} on ${new Date(c.loan.last_payment_date).toLocaleDateString("en-IN")}`}
                  />
                )}
                {c.loan.next_due_date && <InfoRow label="Next Due Date" value={new Date(c.loan.next_due_date).toLocaleDateString("en-IN")} />}
              </InfoSection>
            ) : (
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-slate-50 border border-slate-200">
                <Lock className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
                <p className="text-xs text-slate-500">Tap <strong>Record Visit</strong> and confirm you are speaking with the borrower to see loan amounts</p>
              </div>
            )}

            {/* Customer Information */}
            <InfoSection title="Customer Information">
              <InfoRow label="Ref" value={c.customer.customer_ref} />
              {/* Phone numbers — masked display, real number only passed to dialer */}
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-500">Phone</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-slate-700 font-mono tracking-wide">{maskPhone(c.customer.phone_primary)}</span>
                  <button onClick={() => startCall(c.customer.phone_primary, c.customer.full_name)} className="p-1.5 rounded-lg bg-success-50 text-success-700 hover:bg-success-100 active:scale-95 transition-all" title="Call">
                    <Phone className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              {c.customer.phone_alternate && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-500">Alt Phone</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-slate-700 font-mono tracking-wide">{maskPhone(c.customer.phone_alternate)}</span>
                    <a href={`tel:${c.customer.phone_alternate}`} className="p-1.5 rounded-lg bg-success-50 text-success-700 hover:bg-success-100 active:scale-95 transition-all" title="Call">
                      <Phone className="w-3.5 h-3.5" />
                    </a>
                  </div>
                </div>
              )}
              {c.customer.address_line1 && (
                <InfoRow
                  label="Address"
                  value={[c.customer.address_line1, c.customer.address_line2, c.customer.pincode].filter(Boolean).join(", ")}
                  action={() => window.open(`https://www.google.com/maps/dir/?api=1&destination=${c.customer.latitude},${c.customer.longitude}&travelmode=driving`, "_blank")}
                  actionIcon={<Navigation className="w-3.5 h-3.5" />}
                />
              )}
              <InfoRow label="Area" value={`${c.customer.city}, ${c.customer.state}`} />
              <InfoRow label="Language" value={c.customer.language_preference} />
              {c.customer.customer_segment && <InfoRow label="Segment" value={c.customer.customer_segment} />}
            </InfoSection>

            {/* Loan Details — no financial amounts shown here */}
            <InfoSection title="Loan Details">
              <InfoRow label="Bank" value={c.loan.bank_name} />
              <InfoRow label="Type" value={c.loan.loan_type?.replace("_", " ")} />
              <InfoRow label="DPD" value={`${c.loan.dpd} days`} />
              <InfoRow label="Account" value={c.loan.loan_account_masked ?? ("XXXX" + c.loan.loan_account_number.slice(-4))} />
              {c.loan.npa_flag && <InfoRow label="NPA" value="Yes — Account classified NPA" highlight />}
              {c.loan.legal_status && c.loan.legal_status !== "NONE" && <InfoRow label="Legal Status" value={c.loan.legal_status.replace("_", " ")} highlight />}
            </InfoSection>
          </>
        )}

        {tab === "strategy" && (
          <div className="space-y-4">
            {/* Header with regenerate */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-brand-600" />
                <h2 className="text-sm font-semibold text-slate-800">AI Visit Strategy</h2>
                <AiBadge aiGenerated={strategy?.ai_generated} status={strategy?.ai_status} />
              </div>
              {strategy && !strategyLoading && (
                <button
                  onClick={() => void strategyQ.refetch()}
                  className="flex items-center gap-1.5 text-xs text-brand-600 font-medium hover:text-brand-800 active:scale-95 transition-all"
                >
                  <RefreshCw className="w-3.5 h-3.5" /> Regenerate
                </button>
              )}
            </div>

            {/* Loading state */}
            {strategyLoading && (
              <div className="card flex flex-col items-center justify-center py-10 gap-3">
                <div className="w-8 h-8 border-3 border-brand-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-slate-500">Analysing case history…</p>
                <p className="text-xs text-slate-400 text-center max-w-[200px]">Reading visit transcripts, call intel, and customer behaviour</p>
              </div>
            )}

            {/* Strategy content */}
            {!strategyLoading && strategy && (
              <>
                {/* DO NOT CONTACT hard block */}
                {c.customer.do_not_contact && (
                  <div className="card border-danger-300 bg-danger-50 flex items-start gap-3">
                    <AlertTriangle className="w-5 h-5 text-danger-600 mt-0.5 flex-shrink-0" />
                    <div>
                      <p className="text-sm font-bold text-danger-700">Do Not Contact</p>
                      <p className="text-xs text-danger-600 mt-0.5">This customer has a legal complaint on file. No field visit is permitted. Escalate to manager immediately.</p>
                    </div>
                  </div>
                )}

                {/* Payment readiness + best time row */}
                <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                  <div className="card flex flex-col gap-1.5">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      <TrendingUp className="w-3.5 h-3.5" /> Payment Signal
                    </div>
                    <span className={`text-sm font-bold ${strategy.payment_readiness === "HIGH" ? "text-success-600" : strategy.payment_readiness === "MEDIUM" ? "text-warning-600" : "text-danger-600"}`}>
                      {strategy.payment_readiness === "HIGH" ? "High" : strategy.payment_readiness === "MEDIUM" ? "Medium" : "Low"}
                    </span>
                    <p className="text-xs text-slate-400">readiness to pay</p>
                  </div>
                  <div className="card flex flex-col gap-1.5">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      <Clock className="w-3.5 h-3.5" /> Best Time
                    </div>
                    <p className="text-sm font-semibold text-slate-800 leading-snug">{strategy.best_time_to_visit}</p>
                  </div>
                </div>

                {/* Opening line */}
                <div className="card border-brand-200 bg-brand-50">
                  <p className="text-xs font-semibold text-brand-600 uppercase tracking-wide mb-2 flex items-center gap-1.5">
                    <Zap className="w-3.5 h-3.5" /> Suggested Opening Line
                  </p>
                  <p className="text-sm text-slate-800 italic leading-relaxed">"{strategy.opening_line}"</p>
                </div>

                {/* Customer situation */}
                <div className="card">
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Customer Situation</p>
                  <p className="text-sm text-slate-700 leading-relaxed">{strategy.customer_situation}</p>
                </div>

                {/* Recommended approach */}
                <div className="card">
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Recommended Approach</p>
                  <p className="text-sm text-slate-700 leading-relaxed">{strategy.recommended_approach}</p>
                </div>

                {/* Key leverage points */}
                {strategy.key_leverage_points?.length > 0 && (
                  <div className="card">
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2.5">Key Leverage Points</p>
                    <ul className="space-y-2">
                      {strategy.key_leverage_points.map((pt, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <span className="mt-1 w-1.5 h-1.5 rounded-full bg-brand-500 flex-shrink-0" />
                          <p className="text-sm text-slate-700">{pt}</p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Risk flags */}
                {strategy.risk_flags?.length > 0 && (
                  <div className="card border-warning-200">
                    <p className="text-xs font-semibold text-warning-600 uppercase tracking-wide mb-2.5 flex items-center gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5" /> Risk Flags
                    </p>
                    <ul className="space-y-1.5">
                      {strategy.risk_flags.map((flag, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <span className="mt-1 w-1.5 h-1.5 rounded-full bg-warning-500 flex-shrink-0" />
                          <p className="text-xs text-warning-800">{flag}</p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Footer */}
                <p className="text-center text-xs text-slate-300 pb-2">
                  Generated {new Date(strategy.generated_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                </p>
              </>
            )}

            {/* Empty — not yet fetched */}
            {!strategyLoading && !strategy && (
              <div className="card flex flex-col items-center gap-3 py-8">
                <Sparkles className="w-8 h-8 text-brand-300" />
                <p className="text-sm text-slate-500 text-center">Tap to generate your AI visit strategy</p>
                <Button onClick={() => void strategyQ.refetch()}>Generate Strategy</Button>
              </div>
            )}
          </div>
        )}

        {tab === "visits" && (
          <div className="space-y-3">
            {c.visits.length === 0 ? <EmptyState icon={ClipboardList} message="No visits recorded yet" /> : c.visits.slice().reverse().map((v) => {
              const meta = OUTCOME_LABELS[v.outcome] ?? { label: v.outcome, color: "text-slate-600 bg-slate-50" };
              return (
                <div key={v.id} className="card">
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <p className="text-sm font-semibold text-slate-900">Visit #{v.visit_number}</p>
                      <p className="text-xs text-slate-400">{new Date(v.check_in_time).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}</p>
                    </div>
                    <span className={`text-xs font-medium px-2 py-1 rounded-full ${meta.color}`}>{meta.label}</span>
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs mb-2">
                    <span className={v.customer_met ? "text-success-600 font-medium" : "text-slate-400"}>
                      {v.customer_met ? "Met" : "Not Met"}
                    </span>
                    {v.person_met && <span className="text-slate-600 font-medium">{v.person_met.replace("_", " ")}</span>}
                    {v.geo_verified && <span className="text-brand-600">GPS Verified</span>}
                    {v.not_met_reason && <span className="text-slate-500">{v.not_met_reason.replace("_", " ")}</span>}
                  </div>
                  {v.ai_visit_note && (
                    <div className="mt-3 pt-3 border-t border-slate-100">
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">AI Visit Note</p>
                      <p className="text-xs text-slate-600 leading-relaxed">{v.ai_visit_note}</p>
                    </div>
                  )}
                  {v.agent_recording_transcript && (
                    <div className="mt-3 pt-3 border-t border-slate-100">
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Agent Speech</p>
                      <p className="text-xs text-slate-600 leading-relaxed italic">"{v.agent_recording_transcript}"</p>
                    </div>
                  )}
                  {v.borrower_recording_transcript && (
                    <div className="mt-3 pt-3 border-t border-slate-100">
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Borrower Speech</p>
                      <p className="text-xs text-slate-600 leading-relaxed italic">"{v.borrower_recording_transcript}"</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {tab === "payments" && (
          <div className="space-y-3">
            {c.payments.length === 0 ? <EmptyState icon={CreditCard} message="No payments recorded" /> : c.payments.map((p) => (
              <div key={p.id} className="card">
                <div className="flex items-start justify-between mb-1">
                  <p className="text-base font-bold text-slate-900">₹{p.amount.toLocaleString("en-IN")}</p>
                  <span className={`text-xs font-medium px-2 py-1 rounded-full ${p.status === "VERIFIED" ? "bg-success-100 text-success-700" : "bg-warning-100 text-warning-700"}`}>{p.status}</span>
                </div>
                <p className="text-xs text-slate-500">{p.mode} · {p.receipt_number}</p>
                <p className="text-xs text-slate-400 mt-0.5">{new Date(p.payment_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</p>
                {p.status !== "VERIFIED" && id && (
                  <PendingPaymentVerify caseId={id} payment={p} onVerified={reloadCase} />
                )}
                <button onClick={() => openWhatsApp("receipt")} className="mt-2 flex items-center gap-1 text-xs text-green-600 font-medium hover:underline">
                  <MessageCircle className="w-3 h-3" /> Share receipt via WhatsApp
                </button>
              </div>
            ))}
          </div>
        )}

        {tab === "photos" && (
          <div className="space-y-3">
            {photos.length === 0 ? (
              <EmptyState icon={Camera} message="No photos captured" />
            ) : (
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                {photos.map((ph) => {
                  const typeLabel: Record<string, string> = {
                    AGENT_SELFIE: "Agent Selfie",
                    BORROWER: "Borrower Photo",
                    VEHICLE_ASSET: "Vehicle / Asset",
                  };
                  return (
                    <div key={ph.storage_key} className="card p-0 overflow-hidden">
                      {ph.view_url ? (
                        <img src={ph.view_url} alt={typeLabel[ph.photo_type] ?? ph.photo_type} className="w-full aspect-square object-cover" />
                      ) : (
                        <div className="w-full aspect-square bg-slate-100 flex items-center justify-center opacity-40"><Camera className="w-8 h-8" /></div>
                      )}
                      <div className="p-2">
                        <p className="text-xs font-semibold text-slate-700">{typeLabel[ph.photo_type] ?? ph.photo_type}</p>
                        {ph.captured_at && (
                          <p className="text-xs text-slate-400 mt-0.5">{new Date(ph.captured_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {tab === "ptps" && (
          <div className="space-y-3">
            {c.ptps.length === 0 ? <EmptyState icon={CalendarClock} message="No PTPs recorded" /> : c.ptps.map((p) => (
              <div key={p.id} className="card">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-base font-bold text-slate-900">₹{p.committed_amount.toLocaleString("en-IN")}</p>
                    <p className="text-xs text-slate-400">Due {new Date(p.committed_date).toLocaleDateString("en-IN")}</p>
                  </div>
                  <span className={`text-xs font-medium px-2 py-1 rounded-full ${p.status === "HONORED" ? "bg-success-100 text-success-700" : p.status === "ACTIVE" ? "bg-brand-100 text-brand-700" : p.status === "BROKEN" ? "bg-danger-100 text-danger-700" : "bg-slate-100 text-slate-600"}`}>{p.status}</span>
                </div>
                {p.customer_reason && <p className="text-xs text-slate-500">"{p.customer_reason}"</p>}
                {p.status === "ACTIVE" && (
                  <button onClick={() => openWhatsApp("ptp")} className="mt-2 flex items-center gap-1 text-xs text-green-600 font-medium hover:underline">
                    <MessageCircle className="w-3 h-3" /> Send PTP reminder via WhatsApp
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Log Call Modal */}
      {showCallModal && (
        // Centred dialog at every size — a bottom sheet pinned the form to the
        // bottom edge and pushed its fields out of comfortable reach. Same
        // treatment as the manager case modal.
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Log call"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 sm:p-4"
          onClick={(e) => { if (e.target === e.currentTarget) closeCallModal(); }}
        >
          <div
            ref={callModalRef}
            tabIndex={-1}
            className="w-full sm:max-w-md bg-white rounded-card max-h-[88svh] overflow-y-auto outline-none border border-slate-200 shadow-pop"
          >
            {/* Modal header */}
            <div className="flex items-center justify-between p-4 border-b border-slate-100 sticky top-0 bg-white z-10">
              <div className="flex items-center gap-2">
                <PhoneCall className="w-4 h-4 text-brand-600" />
                <h2 className="text-sm font-semibold text-slate-800">Log Call</h2>
              </div>
              <button onClick={closeCallModal} aria-label="Close log call" className="tap-target p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 flex items-center justify-center">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-4 space-y-5">
              {/* Outcome */}
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2.5">Call Outcome</p>
                <div className="grid grid-cols-3 gap-2">
                  {(["ANSWERED", "NO_ANSWER", "BUSY", "DECLINED", "SWITCHED_OFF", "WRONG_NUMBER"] as const).map((o) => {
                    const labels: Record<string, string> = { ANSWERED: "Answered", NO_ANSWER: "No Answer", BUSY: "Busy", DECLINED: "Declined", SWITCHED_OFF: "Off", WRONG_NUMBER: "Wrong No." };
                    const selected = callForm.outcome === o;
                    return (
                      <button key={o} onClick={() => setCallForm(f => ({ ...f, outcome: o }))}
                        className={`py-2 px-2 rounded-xl text-xs font-medium border transition-colors ${selected ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-200 text-slate-600 hover:border-slate-300"}`}>
                        {labels[o]}
                      </button>
                    );
                  })}
                </div>
              </div>

              {callForm.outcome === "ANSWERED" && (
                <>
                  {/* Duration + Phone used */}
                  <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs font-semibold text-slate-500 uppercase tracking-wide block mb-1.5">Duration (seconds)</label>
                      <input
                        type="number" min={0} placeholder="e.g. 120"
                        className="w-full rounded-xl border border-slate-200 text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-200"
                        value={callForm.duration_seconds ?? ""}
                        onChange={(e) => setCallForm(f => ({ ...f, duration_seconds: e.target.value ? parseInt(e.target.value) : undefined }))}
                      />
                    </div>
                    <div>
                      <label className="text-xs font-semibold text-slate-500 uppercase tracking-wide block mb-1.5">Phone Used</label>
                      <div className="flex rounded-xl border border-slate-200 overflow-hidden">
                        {(["PRIMARY", "ALTERNATE"] as const).map((p) => (
                          <button key={p} onClick={() => setCallForm(f => ({ ...f, phone_used: p }))}
                            className={`flex-1 py-2.5 text-xs font-medium transition-colors ${callForm.phone_used === p ? "bg-brand-600 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}>
                            {p === "PRIMARY" ? "Primary" : "Alternate"}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Customer response notes */}
                  <div>
                    <label className="text-xs font-semibold text-slate-500 uppercase tracking-wide block mb-1.5">What did the customer say?</label>
                    <textarea
                      rows={3} placeholder="Customer said he'll pay after salary on 5th, wife handles payments, not at home till Sunday..."
                      className="w-full rounded-xl border border-slate-200 text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-200 resize-none placeholder-slate-400"
                      value={callForm.customer_response_notes ?? ""}
                      onChange={(e) => setCallForm(f => ({ ...f, customer_response_notes: e.target.value }))}
                    />
                  </div>

                  {/* Intel section */}
                  <div className="bg-brand-50 rounded-2xl p-4 space-y-4 border border-brand-100">
                    <p className="text-xs font-semibold text-brand-700 uppercase tracking-wide flex items-center gap-1.5">
                      <Zap className="w-3.5 h-3.5" /> Scheduling Intelligence
                    </p>
                    {/* Named the feature it actually feeds. This read "used by AI
                        to re-rank your visit priority list" until 2026-08-28, by
                        which point it pointed at the wrong thing: Visit priority
                        became a specific hand-weighted score (ml/visit_priority.py,
                        is_modelled false) that reads NONE of this intel. What this
                        does feed is Smart Order — CaseService.ranked_cases, which
                        scores call-log availability, last outcome, PTPs and
                        customer flags, and asks a model only for the one-line
                        reason it prints. */}
                    <p className="text-xs text-brand-600 -mt-2">This intel feeds Smart Order, which re-ranks today's beat</p>

                    {/* Visit feasible today */}
                    <div>
                      <label className="text-xs font-semibold text-slate-600 block mb-2">Can you visit today based on this call?</label>
                      <div className="flex gap-2">
                        {([true, false, null] as const).map((v) => {
                          const label = v === true ? "Yes" : v === false ? "No" : "Not sure";
                          const sel = callForm.visit_feasible_today === v;
                          return (
                            <button key={String(v)} onClick={() => setCallForm(f => ({ ...f, visit_feasible_today: v }))}
                              className={`flex-1 py-2 rounded-xl text-xs font-medium border transition-colors ${sel ? "border-brand-500 bg-brand-600 text-white" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"}`}>
                              {label}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Best time */}
                    <div>
                      <label className="text-xs font-semibold text-slate-600 block mb-1.5">Best time to visit (if mentioned)</label>
                      <input
                        type="text" placeholder="e.g. after 6 PM, Saturday morning, before 10 AM"
                        className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-200"
                        value={callForm.best_time_to_visit ?? ""}
                        onChange={(e) => setCallForm(f => ({ ...f, best_time_to_visit: e.target.value }))}
                      />
                    </div>

                    {/* Exact window today — drives beat re-optimization directly */}
                    <div>
                      <label className="text-xs font-semibold text-slate-600 block mb-1.5">Exact window today (optional — used to re-optimize your route)</label>
                      <div className="flex gap-2 mb-2">
                        {URGENT_PRESETS.map((p) => (
                          <button
                            key={p.label}
                            onClick={() => setCallForm(f => ({
                              ...f,
                              available_from: undefined,
                              available_until: new Date(Date.now() + p.minutes * 60_000).toISOString(),
                            }))}
                            className="flex-1 py-2 rounded-xl text-[11px] font-medium border border-slate-200 bg-white text-slate-600 hover:border-brand-200 hover:text-brand-700 transition-colors"
                          >
                            {p.label}
                          </button>
                        ))}
                      </div>
                      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                        <div>
                          <label className="text-[11px] text-slate-400 block mb-1">From</label>
                          <input
                            type="time"
                            className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-200"
                            value={isoToLocalTime(callForm.available_from)}
                            onChange={(e) => setCallForm(f => ({
                              ...f,
                              available_from: e.target.value ? timeTodayToISO(e.target.value) : undefined,
                            }))}
                          />
                        </div>
                        <div>
                          <label className="text-[11px] text-slate-400 block mb-1">Until</label>
                          <input
                            type="time"
                            className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-200"
                            value={isoToLocalTime(callForm.available_until)}
                            onChange={(e) => setCallForm(f => ({
                              ...f,
                              available_until: e.target.value ? timeTodayToISO(e.target.value) : undefined,
                            }))}
                          />
                        </div>
                      </div>
                      {callForm.available_until && (
                        <button
                          onClick={() => setCallForm(f => ({ ...f, available_from: undefined, available_until: undefined }))}
                          className="text-[11px] text-slate-400 hover:text-slate-600 mt-1.5"
                        >
                          Clear window
                        </button>
                      )}
                    </div>

                    {/* Blocked until */}
                    <div>
                      <label className="text-xs font-semibold text-slate-600 block mb-1.5">Customer unavailable until (block visits)</label>
                      <input
                        type="date"
                        className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-200"
                        value={callForm.blocked_until_date ?? ""}
                        onChange={(e) => setCallForm(f => ({ ...f, blocked_until_date: e.target.value }))}
                      />
                    </div>

                    {/* Payment intent */}
                    <div>
                      <label className="text-xs font-semibold text-slate-600 block mb-2">Did customer signal willingness to pay?</label>
                      <div className="flex gap-2">
                        {([true, false, null] as const).map((v) => {
                          const label = v === true ? "Yes" : v === false ? "No" : "Unclear";
                          const sel = callForm.payment_intent_signalled === v;
                          return (
                            <button key={String(v)} onClick={() => setCallForm(f => ({ ...f, payment_intent_signalled: v }))}
                              className={`flex-1 py-2 rounded-xl text-xs font-medium border transition-colors ${sel ? "border-brand-500 bg-brand-600 text-white" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"}`}>
                              {label}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Verbal payment date */}
                    {callForm.payment_intent_signalled === true && (
                      <div>
                        <label className="text-xs font-semibold text-slate-600 block mb-1.5">Date customer mentioned for payment</label>
                        <input
                          type="date"
                          className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-200"
                          value={callForm.verbal_payment_date ?? ""}
                          onChange={(e) => setCallForm(f => ({ ...f, verbal_payment_date: e.target.value }))}
                        />
                      </div>
                    )}

                    {/* Alternate location */}
                    <div>
                      <label className="text-xs font-semibold text-slate-600 block mb-1.5">Alternate location hint (optional)</label>
                      <input
                        type="text" placeholder="e.g. At brother's shop in Lajpat Nagar"
                        className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-200"
                        value={callForm.alternate_location_hint ?? ""}
                        onChange={(e) => setCallForm(f => ({ ...f, alternate_location_hint: e.target.value }))}
                      />
                    </div>
                  </div>
                </>
              )}

              {/* Submit */}
              <button
                onClick={handleLogCall}
                disabled={callSubmitting}
                className="w-full py-3 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700 active:scale-[0.98] transition-all disabled:opacity-50"
              >
                {callSubmitting ? "Saving…" : "Save Call Log"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bottom CTA — constrained to max-w-md to stay inside the mobile frame */}
      <div className="fixed bottom-16 lg:bottom-0 left-0 right-0 z-20 pointer-events-none" style={{ paddingLeft: "var(--rail-w, 0px)" }}>
        <div className="max-w-md md:max-w-none mx-auto md:mx-0 pointer-events-auto tiq-glass-bar border-t border-slate-100/70 safe-bottom">
          {/* Geo-fence status bar */}
          {canRecordVisit && (
            <div className={`flex items-center justify-between gap-2 px-4 py-2 text-xs font-medium border-b ${withinFence ? "bg-success-50 border-success-100 text-success-700" : (geoError && distanceM === null) ? "bg-danger-50 border-danger-100 text-danger-700" : distanceM === null ? "bg-slate-50 border-slate-100 text-slate-500" : "bg-danger-50 border-danger-100 text-danger-700"}`}>
              <div className="flex items-center gap-1.5">
                {withinFence ? <Unlock className="w-3.5 h-3.5 flex-shrink-0" /> : <Lock className="w-3.5 h-3.5 flex-shrink-0" />}
                {geoError && distanceM === null
                  ? geoError
                  : distanceM === null
                    ? "Getting your location…"
                    : withinFence
                      ? `Within range (${distanceM}m) — visit unlocked`
                      : `${distanceM}m away — move within 100m to record visit`}
              </div>
              <MapPin className="w-3.5 h-3.5 opacity-60 flex-shrink-0" />
            </div>
          )}

          <div className="p-4">
            {canRecordVisit ? (
              <div>
                {withinFence ? (
                  // Enabled ONLY once GPS is captured AND within the 100m fence.
                  <Button fullWidth onClick={() => navigate(`/agent/visit/${c.id}`)}>
                    <Unlock className="w-4 h-4" /> Record Visit
                  </Button>
                ) : (geoError && distanceM === null) ? (
                  // Location blocked/denied/unavailable → let the agent re-request.
                  <Button fullWidth variant="secondary" onClick={() => requestLocation()}>
                    <MapPin className="w-4 h-4" /> Retry location
                  </Button>
                ) : (
                  // Location still loading OR out of fence → locked, non-clickable.
                  <button
                    disabled
                    className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-slate-200 text-slate-400 text-sm font-medium cursor-not-allowed border border-slate-200"
                    title={distanceM === null ? "Getting your location…" : `${distanceM}m away — need to be within 100m`}
                  >
                    <Lock className="w-4 h-4" /> {distanceM === null ? "Getting your location…" : `Locked (${distanceM}m away)`}
                  </button>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center gap-2 text-success-600">
                <CheckCircle className="w-5 h-5" />
                <span className="text-sm font-medium">Case resolved</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function AlertBanner({ icon: Icon, color, message }: { icon: LucideIcon; color: "danger" | "warning"; message: string }) {
  const cls = color === "danger" ? "border-danger-200 bg-danger-50 text-danger-700" : "border-warning-200 bg-warning-50 text-warning-700";
  return (
    <div className={`card flex items-center gap-2 ${cls}`}>
      <Icon className="w-4 h-4 flex-shrink-0" />
      <p className="text-sm font-medium">{message}</p>
    </div>
  );
}

/** Collection progress as a radial meter.
 *
 * A meter, not a two-slice pie. The data here is a single ratio against a
 * limit, so it is one arc over a recessive track — "how far to target" — and
 * not two coloured slices competing for area, which would invite the reader to
 * compare collected against remaining as if they were peer categories. One
 * series also means no legend is needed: the card title names it.
 *
 * Same construction as RankingRing on the profile page (circumference +
 * dashoffset, rotated -90° so the arc starts at twelve o'clock), so the two
 * rings on the agent side stay visually consistent.
 */
function CollectionDonut({ pct }: { pct: number }) {
  const SIZE = 104, R = 40, STROKE = 9;
  const circ = 2 * Math.PI * R;
  const safe = Math.max(0, Math.min(100, pct));
  const offset = circ - (safe / 100) * circ;
  return (
    <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} role="img"
           aria-label={`Collection target ${safe}% collected`}>
        <circle cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" stroke="#F1F5F9" strokeWidth={STROKE} />
        <circle
          cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" stroke="#2563EB" strokeWidth={STROKE}
          strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          style={{ transition: "stroke-dashoffset 0.7s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold text-slate-900 leading-none">{safe}%</span>
        <span className="text-[10px] text-slate-400 mt-1">collected</span>
      </div>
    </div>
  );
}

function ActionBtn({ icon, label, color, onClick }: { icon: React.ReactNode; label: string; color: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`flex flex-col items-center gap-1 py-2.5 rounded-xl border text-xs font-medium transition-colors ${color}`}>
      {icon}
      {label}
    </button>
  );
}

function InfoSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">{title}</h3>
      <div className="space-y-2.5">{children}</div>
    </div>
  );
}

function InfoRow({ label, value, action, actionLabel, actionIcon, highlight }: { label: string; value: string; action?: () => void; actionLabel?: string; actionIcon?: React.ReactNode; highlight?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-slate-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className={`text-xs font-medium ${highlight ? "text-danger-600" : "text-slate-900"}`}>{value}</span>
        {action && actionIcon && (
          <button onClick={action} className="p-1.5 rounded-lg bg-brand-50 text-brand-700 hover:bg-brand-100 active:scale-95 transition-all" title="Navigate">
            {actionIcon}
          </button>
        )}
        {action && actionLabel && !actionIcon && (
          <button onClick={action} className="text-xs text-brand-600 font-medium hover:underline">{actionLabel}</button>
        )}
      </div>
    </div>
  );
}

function maskPhone(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  if (digits.length < 4) return "XXXX";
  // Show only last 4 digits, mask everything else
  return "X".repeat(digits.length - 4) + digits.slice(-4);
}

function EmptyState({ icon: Icon, message }: { icon: LucideIcon; message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-slate-400">
      <Icon className="w-9 h-9 mb-3 opacity-50" strokeWidth={1.5} />
      <p className="text-sm">{message}</p>
    </div>
  );
}

// ─── Deferred borrower-OTP verification for a pending payment (2026-07-30) ───
// Shown on offline-recorded payments (status PENDING_VERIFICATION). Once the
// borrower has signal, the agent sends an OTP bound to this exact payment and
// verifies it — promoting the payment to VERIFIED server-side. See
// otp_service.py / prototype_to_product/30.07.md.
function PendingPaymentVerify({ caseId, payment, onVerified }: {
  caseId: string;
  payment: { id: string; amount: number; mode: string };
  onVerified: () => void | Promise<void>;
}) {
  const [otp, setOtp] = useState<{ id: string; maskedPhone: string } | null>(null);
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resendsUsed, setResendsUsed] = useState(0);   // max 3 resends

  async function send() {
    const isResend = !!otp;
    if (isResend && resendsUsed >= 3) return;
    setError(null);
    setSending(true);
    try {
      const res = await sendPaymentOtp(caseId, { amount: payment.amount, mode: payment.mode, payment_id: payment.id });
      setOtp({ id: res.otp_id, maskedPhone: res.masked_phone });
      setCode("");
      if (isResend) setResendsUsed((n) => n + 1);
      toast.success(`OTP sent to borrower (${res.masked_phone})`);
    } catch (err) {
      setError(errorDetail(err, "Could not send OTP"));
    } finally {
      setSending(false);
    }
  }

  async function verify() {
    if (!otp || code.length < 4) return;
    setError(null);
    setVerifying(true);
    try {
      await verifyPaymentOtp(caseId, { otp_id: otp.id, code });
      toast.success("Payment verified");
      await onVerified();
    } catch (err) {
      setError(errorDetail(err, "Incorrect OTP"));
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5">
      <div className="flex items-center gap-1.5 text-amber-800 mb-1.5">
        <ShieldCheck className="w-3.5 h-3.5" />
        <p className="text-xs font-semibold">Awaiting borrower verification</p>
      </div>
      {!otp ? (
        <button onClick={send} disabled={sending} className="flex items-center gap-1 text-xs font-medium text-brand-600 disabled:text-slate-300">
          <Send className="w-3.5 h-3.5" /> {sending ? "Sending…" : "Verify now — send OTP to borrower"}
        </button>
      ) : (
        <div className="space-y-2">
          <p className="text-[11px] text-amber-700 text-center">OTP sent to {otp.maskedPhone}. Enter the code the borrower reads out.</p>
          <OtpInput value={code} onChange={(v) => { setCode(v); setError(null); }} length={4} autoFocus disabled={verifying} />
          <div className="flex items-center gap-2">
            <button onClick={verify} disabled={verifying || code.length < 4} className="flex-1 py-1.5 rounded-lg bg-brand-600 text-white text-xs font-medium disabled:bg-slate-300">
              {verifying ? "Verifying…" : "Verify"}
            </button>
            <button onClick={send} disabled={sending || resendsUsed >= 3} className="px-2 py-1.5 rounded-lg border border-amber-300 text-xs text-amber-700 disabled:text-slate-300">
              {resendsUsed >= 3 ? "No resends" : "Resend"}
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-[11px] text-danger-600 font-medium mt-1 text-center">{error}</p>}
    </div>
  );
}
