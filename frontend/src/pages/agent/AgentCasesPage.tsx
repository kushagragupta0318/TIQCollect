import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { MapPin, Navigation, Phone, MessageCircle, ChevronRight, Search, Briefcase, Sparkles, RefreshCw, Loader2 } from "lucide-react";
import { DPDBadge, VisitPriorityBadge, CaseStatusBadge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { useBeat } from "@/contexts/BeatContext";
import { getRankedCases, notifyVisit, type RankedCase } from "@/api/agent";
import { toast } from "react-hot-toast";
import { useVoiceCall } from "@/hooks/useVoiceCall";
import { useLiveLocation } from "@/hooks/useLiveLocation";
import { haversineM, formatDistance, GEO_FENCE_METRES } from "@/lib/geo";
import CallModal from "@/components/ui/CallModal";
import type { Case, CaseStatus } from "@/types";

const STATUS_FILTERS: { label: string; value: CaseStatus | "ALL" | "PTP_TODAY" }[] = [
  { label: "All", value: "ALL" },
  { label: "Assigned", value: "ASSIGNED" },
  { label: "In Progress", value: "IN_PROGRESS" },
  { label: "PTP Today", value: "PTP_TODAY" },
  { label: "Part Paid", value: "PARTIALLY_PAID" },
  { label: "Escalated", value: "ESCALATED" },
  { label: "Paid", value: "PAID" },
];

export default function AgentCasesPage() {
  const { beat, loading, refresh } = useBeat();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<CaseStatus | "ALL" | "PTP_TODAY">("ALL");
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [smartOrder, setSmartOrder] = useState(false);
  const [rankedCases, setRankedCases] = useState<RankedCase[] | null>(null);
  const [rankLoading, setRankLoading] = useState(false);
  const { activeCall, startCall, hangUp } = useVoiceCall();
  const liveLoc = useLiveLocation();

  async function fetchRanked() {
    setRankLoading(true);
    try {
      const data = await getRankedCases();
      setRankedCases(data);
      setSmartOrder(true);
    } catch {
      toast.error("Could not fetch ranked cases");
    } finally {
      setRankLoading(false);
    }
  }

  function toggleSmartOrder() {
    if (smartOrder) {
      setSmartOrder(false);
    } else if (rankedCases) {
      setSmartOrder(true);
    } else {
      fetchRanked();
    }
  }

  const here = liveLoc.coords;

  const cases = useMemo<Case[]>(() => {
    if (!beat) return [];
    const visitedSet = new Set(beat.visited_today_ids ?? []);
    const isBlocked = (c: Case) =>
      Boolean(
        c.customer?.do_not_contact ||
        c.is_blocked ||
        (c.rank_badge && (c.rank_badge.startsWith("BLOCKED") || c.rank_badge === "DO NOT VISIT"))
      );

    const all = (beat.cases ?? []).map((c) => ({
      ...c,
      is_visited_today: visitedSet.has(c.id),
      is_blocked: isBlocked(c),
    }));

    const active = all.filter((c) => !c.is_visited_today && !c.is_blocked);
    const done = all.filter((c) => c.is_visited_today && !c.is_blocked);
    const blocked = all.filter((c) => c.is_blocked);

    const score = (c: Case) => c.visit_priority?.score ?? -1;

    if (here) {
      const distOf = new Map<string, number>();
      for (const c of active) {
        distOf.set(c.id, haversineM(here.lat, here.lon, c.customer.latitude, c.customer.longitude));
      }
      const bucket = (c: Case) => Math.round((distOf.get(c.id) ?? Infinity) / SORT_BUCKET_M);
      active.sort(
        (a, b) =>
          bucket(a) - bucket(b) ||
          score(b) - score(a) ||
          a.id.localeCompare(b.id),
      );
    } else {
      active.sort((a, b) => score(b) - score(a) || a.id.localeCompare(b.id));
    }

    return [...active, ...done, ...blocked];
  }, [beat, here]);

  const activeList = useMemo<(Case | RankedCase)[]>(() => {
    if (smartOrder && rankedCases) {
      const visitedSet = new Set(beat?.visited_today_ids ?? []);
      const isBlocked = (c: RankedCase) =>
        Boolean(
          c.is_blocked ||
          c.customer?.do_not_contact ||
          (c.rank_badge && (c.rank_badge.startsWith("BLOCKED") || c.rank_badge === "DO NOT VISIT")) ||
          (c.rank_score !== undefined && c.rank_score <= -900)
        );

      const active = rankedCases.filter((c) => !c.is_visited_today && !visitedSet.has(c.id) && !isBlocked(c));
      const done = rankedCases.filter((c) => (c.is_visited_today || visitedSet.has(c.id)) && !isBlocked(c));
      const blocked = rankedCases.filter((c) => isBlocked(c));

      return [...active, ...done, ...blocked];
    }
    return cases;
  }, [smartOrder, rankedCases, cases, beat]);

  const filtered = useMemo(() => {
    let list = activeList;
    const urlFilter = searchParams.get("filter");
    if (urlFilter === "ptp_due")            list = list.filter((c) => !!c.ptp_due_today);
    else if (urlFilter === "visited_today") list = list.filter((c) => !!c.is_visited_today);
    else if (urlFilter === "collected")     list = list.filter((c) => c.status === "PAID" || c.status === "PARTIALLY_PAID");
    else if (statusFilter === "PTP_TODAY")  list = list.filter((c) => !!c.ptp_due_today);
    else if (statusFilter !== "ALL")        list = list.filter((c) => c.status === statusFilter);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (c) =>
          c.customer.full_name.toLowerCase().includes(q) ||
          c.case_number.toLowerCase().includes(q) ||
          c.customer.city.toLowerCase().includes(q)
      );
    }
    return list;
  }, [activeList, search, statusFilter, searchParams]);

  function callCustomer(phone: string, name: string, e: React.MouseEvent) {
    e.stopPropagation();
    startCall(phone, name);
  }

  function openGoogleMaps(lat: number, lon: number, name: string, e: React.MouseEvent) {
    e.stopPropagation();
    window.open(
      `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&destination_place_id=${encodeURIComponent(name)}&travelmode=driving`,
      "_blank"
    );
  }

  return (
    <div className="flex flex-col h-full">
      {activeCall && <CallModal call={activeCall} onHangUp={hangUp} />}
      <div className="p-4 lg:px-6 pb-2 space-y-3 bg-white border-b border-slate-100 sticky top-0 z-10">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-900">
              {searchParams.get("filter") === "visited_today" ? "Visited Today"
                : searchParams.get("filter") === "collected"   ? "Collected Today"
                : searchParams.get("filter") === "ptp_due"     ? "PTPs Due Today"
                : "My Cases"}
            </h1>
            {searchParams.get("filter") && (
              <p className="text-xs text-slate-400">{filtered.length} of {cases.length} cases</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {smartOrder && rankedCases && (
              <button
                onClick={fetchRanked}
                disabled={rankLoading}
                className="p-1.5 rounded-lg text-purple-500 hover:bg-purple-50 transition-colors"
                title="Refresh ranking"
              >
                {rankLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              </button>
            )}
            <button
              onClick={toggleSmartOrder}
              disabled={rankLoading}
              className={`tap-target flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border transition-all ${
                smartOrder
                  ? "bg-purple-600 text-white border-purple-600"
                  : "bg-white text-purple-600 border-purple-200 hover:border-purple-400"
              }`}
            >
              {rankLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
              {smartOrder ? "Smart Order" : "Smart Order"}
            </button>
          </div>
        </div>
        <Input
          placeholder="Search name, case no, loan..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          leftIcon={<Search className="w-4 h-4" />}
        />
        <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatusFilter(f.value)}
              className={`tap-target-h flex-shrink-0 inline-flex items-center px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                statusFilter === f.value
                  ? "bg-brand-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto divide-y divide-slate-100 lg:divide-y-0 lg:grid lg:grid-cols-2 xl:grid-cols-3 lg:gap-4 lg:p-6 lg:content-start">
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400 lg:col-span-full text-center px-4">
            <div className="w-14 h-14 rounded-2xl bg-slate-100 flex items-center justify-center text-slate-400 mb-3">
              <Briefcase className="w-7 h-7 opacity-60" />
            </div>
            <p className="text-sm font-semibold text-slate-700 mb-1">
              {!beat ? "No Active Beat Assigned Today" : "No Cases Found"}
            </p>
            <p className="text-xs text-slate-500 max-w-xs mb-4">
              {!beat
                ? "Your manager hasn't published a beat route for you today. When cases are assigned, they will appear here."
                : "No cases match your active search or status filter."}
            </p>
            {!beat && (
              <button
                onClick={() => refresh()}
                className="btn btn-secondary text-xs flex items-center gap-1.5 px-3 py-1.5"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Refresh Queue</span>
              </button>
            )}
          </div>
        )}
        {filtered.map((c) => {
          const ranked = smartOrder ? (c as RankedCase) : null;
          return (
            <CaseCard
              key={c.id}
              case_={c}
              rank={ranked?.rank}
              rankBadge={ranked?.rank_badge}
              rankBadgeColor={ranked?.rank_badge_color}
              rankReason={ranked?.rank_reason}
              onNavigate={(e) => openGoogleMaps(c.customer.latitude, c.customer.longitude, c.customer.full_name, e)}
              onCall={(e) => callCustomer(c.customer.phone_primary, c.customer.full_name, e)}
              onWhatsapp={async (e) => { e.stopPropagation(); try { await notifyVisit(c.id); toast.success("Visit notification sent"); } catch { toast.error("Could not send notification"); } }}
              onOpen={() => navigate(`/agent/cases/${c.id}`)}
              distanceM={here ? haversineM(here.lat, here.lon, c.customer.latitude, c.customer.longitude) : null}
            />
          );
        })}
      </div>
    </div>
  );
}

const SORT_BUCKET_M = 1000;
const SHOW_SLA_BADGE = false;
const SHOW_IN_RANGE_PILL = false;

function getSLAInfo(allocationDate: string | null | undefined) {
  if (!allocationDate) return null;
  const deadline = new Date(allocationDate + "T00:00:00");
  deadline.setDate(deadline.getDate() + 3);
  const hoursLeft = Math.round((deadline.getTime() - Date.now()) / 3_600_000);
  if (hoursLeft < 0) return { label: "OVERDUE", cls: "text-danger-600 bg-danger-50 border-danger-100" };
  if (hoursLeft < 24) return { label: `${hoursLeft}h left`, cls: "text-warning-600 bg-warning-50 border-warning-100" };
  return { label: `${Math.ceil(hoursLeft / 24)}d SLA`, cls: "text-slate-500 bg-slate-50 border-slate-100" };
}

const RANK_BADGE_STYLES: Record<string, string> = {
  red:    "bg-danger-50 text-danger-700 border-danger-200",
  green:  "bg-success-50 text-success-700 border-success-200",
  blue:   "bg-brand-50 text-brand-700 border-brand-200",
  orange: "bg-warning-50 text-warning-700 border-warning-200",
  grey:   "bg-slate-50 text-slate-500 border-slate-200",
};

function CaseCard({ case_: c, rank, rankBadge, rankBadgeColor, rankReason, onNavigate, onCall, onWhatsapp, onOpen, distanceM }: {
  case_: Case;
  distanceM?: number | null;
  rank?: number;
  rankBadge?: string;
  rankBadgeColor?: string;
  rankReason?: string;
  onNavigate: (e: React.MouseEvent) => void;
  onCall: (e: React.MouseEvent) => void;
  onWhatsapp: (e: React.MouseEvent) => void;
  onOpen: () => void;
}) {
  const isDone = !!c.is_visited_today;
  const sla = getSLAInfo(c.allocation_date);
  const isBlocked = rankBadge?.startsWith("BLOCKED") || rankBadge === "DO NOT VISIT";
  return (
    <div onClick={onOpen} className={`tap-card p-4 active:bg-slate-50 cursor-pointer lg:rounded-2xl lg:border lg:border-slate-100 lg:h-full lg:flex lg:flex-col ${isDone || isBlocked ? "opacity-60" : ""}`}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-start gap-2.5 flex-1 min-w-0">
          {rank !== undefined ? (
            <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 text-xs font-bold ${isDone ? "bg-success-100 text-success-600 border-2 border-success-400" : "bg-purple-100 text-purple-700"}`}>
              {isDone ? "✓" : rank}
            </div>
          ) : isDone ? (
            <div className="w-5 h-5 rounded-full bg-success-100 border-2 border-success-500 flex items-center justify-center flex-shrink-0 mt-0.5">
              <svg className="w-3 h-3 text-success-600" viewBox="0 0 12 12" fill="none">
                <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
          ) : (
            <div className="w-5 h-5 rounded-full border-2 border-slate-200 flex-shrink-0 mt-0.5" />
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-slate-900">{c.customer.full_name}</span>
              {c.customer.is_hostile && <span className="badge badge-red text-xs">⚠ Hostile</span>}
              {c.is_escalated && <span className="badge badge-red text-xs">🔴 Escalated</span>}
              {SHOW_SLA_BADGE && sla && <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${sla.cls}`}>{sla.label}</span>}
            </div>
            <p className="text-xs text-slate-400 mt-0.5">{c.case_number} · {c.loan.bank_name}</p>
          </div>
        </div>
        <ChevronRight className="w-5 h-5 text-slate-300 flex-shrink-0 mt-0.5" />
      </div>

      <div className="flex flex-wrap gap-1.5 mb-2">
        <DPDBadge bucket={c.loan.dpd_bucket} />
        {(c.status !== "PTP_SET" || c.ptp_due_today) && (
          <CaseStatusBadge status={c.status} ptpDueToday={c.ptp_due_today} />
        )}
        {rankBadge && (
          <span className={`text-xs px-1.5 py-0.5 rounded-full border font-semibold ${RANK_BADGE_STYLES[rankBadgeColor ?? "grey"]}`}>
            {rankBadge}
          </span>
        )}
        {rank === undefined && c.visit_priority && (
          <VisitPriorityBadge priority={c.visit_priority} />
        )}
      </div>

      {rank === undefined && c.visit_priority && !isDone && (
        <div className="flex items-start gap-1.5 mb-2 bg-brand-50 rounded-lg px-2.5 py-1.5">
          <Briefcase className="w-3.5 h-3.5 text-brand-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-brand-700 leading-snug">{c.visit_priority.reason}</p>
        </div>
      )}

      {rankReason && (
        <div className="flex items-start gap-1.5 mb-2 bg-purple-50 rounded-lg px-2.5 py-1.5">
          <Sparkles className="w-3.5 h-3.5 text-purple-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-purple-700 leading-snug">{rankReason}</p>
        </div>
      )}

      <div className="flex items-center gap-2 mb-3 text-xs text-slate-500">
        <span>{c.loan.bank_name.split(" ")[0]}</span>
        <span className="text-slate-300">·</span>
        <span>{c.loan.loan_type?.replace("_", " ")}</span>
        {c.collection_stage && (
          <>
            <span className="text-slate-300">·</span>
            <span className="font-medium text-slate-600">{c.collection_stage.replace("_", " ")}</span>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 lg:mt-auto">
        <div className="flex items-center gap-1 text-xs text-slate-400 flex-wrap">
          <MapPin className="w-3 h-3 flex-shrink-0" />
          <span>{c.customer.city}</span>
          {distanceM != null && (
            <span
              className={
                SHOW_IN_RANGE_PILL && distanceM <= GEO_FENCE_METRES
                  ? "ml-1 px-1.5 py-0.5 rounded-full font-semibold bg-success-50 text-success-700 border border-success-200"
                  : "ml-1 text-slate-400"
              }
            >
              {SHOW_IN_RANGE_PILL && distanceM <= GEO_FENCE_METRES
                ? `In range · ${formatDistance(distanceM)}`
                : formatDistance(distanceM)}
            </span>
          )}
          {c.visit_count > 0 && <span className="ml-1 text-slate-300">· {c.visit_count} visit{c.visit_count > 1 ? "s" : ""}</span>}
        </div>
        <div className="flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
          <button onClick={onCall} className="tap-target flex items-center justify-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg bg-success-50 text-success-700 hover:bg-success-100 transition-colors">
            <Phone className="w-3 h-3" /> Call
          </button>
          <button onClick={onWhatsapp} className="tap-target flex items-center justify-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg bg-green-50 text-green-700 hover:bg-green-100 transition-colors">
            <MessageCircle className="w-3 h-3" /> WhatsApp
          </button>
          <button onClick={onNavigate} className="tap-target flex items-center justify-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors">
            <Navigation className="w-3 h-3" /> Navigate
          </button>
        </div>
      </div>
    </div>
  );
}
