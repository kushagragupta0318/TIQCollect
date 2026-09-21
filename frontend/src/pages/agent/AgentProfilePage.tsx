import { useEffect, useState } from "react";
import { Calendar, LogOut, Shield, TrendingUp, HandCoins, Target, Briefcase, ChevronLeft, ChevronRight } from "lucide-react";
import { useNavigate } from "react-router";
import { toast } from "react-hot-toast";
import { getProfile, getAvailabilityCalendar } from "@/api/agent";
import type { AvailabilityCalendar } from "@/api/agent";
import { useAuthStore } from "@/store/authStore";
import { TierBadge } from "@/components/ui/Badge";
import { checkOut as apiCheckOut } from "@/api/agent";
import AgentIDCard from "@/components/ui/AgentIDCard";
import { LeaveRequestsSection } from "./LeaveRequestsSection";
import type { Agent } from "@/types";
import { useAnimatedValue } from "@/hooks/useAnimatedValue";

// Indian-style compact currency so every ₹ value is formatted identically.
function inrCompact(n: number): string {
  if (n >= 1_00_00_000) return `₹${(n / 1_00_00_000).toFixed(1)}Cr`;
  if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(1)}L`;
  if (n >= 1_000) return `₹${(n / 1_000).toFixed(0)}K`;
  return `₹${n}`;
}

export default function AgentProfilePage() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [checkInStatus, setCheckInStatus] = useState<string>("OFF_DUTY");
  const [calendar, setCalendar] = useState<AvailabilityCalendar | null>(null);
  const [checkingOut, setCheckingOut] = useState(false);

  // Check-in has existed since the beginning with no counterpart: an agent went
  // ON_DUTY and stayed there until a manager changed it. Location tracking is
  // gated on duty status, so the person being tracked had no way to stop it.
  async function handleCheckOut() {
    if (checkingOut) return;
    setCheckingOut(true);
    try {
      const res = await apiCheckOut();
      setCheckInStatus(res.status);
      toast.success(res.message);
    } catch {
      toast.error("Could not check out — try again");
    } finally {
      setCheckingOut(false);
    }
  }

  useEffect(() => {
    getProfile().then((data) => {
      setAgent(data as Agent);
      setCheckInStatus(data.status);
    }).catch(() => {});
    getAvailabilityCalendar().then(setCalendar).catch(() => {});
  }, []);

  function handleLogout() {
    logout();
    toast.success("Signed out successfully");
    navigate("/login", { replace: true });
  }

  const checkedIn = checkInStatus === "ON_DUTY";
  const animatedRanking = useAnimatedValue(agent?.ranking_score ?? 0);

  // The profile API returns honored/set (not a pre-computed rate), so derive it here.
  const ptpRate = agent
    ? Math.round((agent.current_month_ptps_honored / Math.max(agent.current_month_ptps_set, 1)) * 100)
    : 0;

  return (
    <div className="p-4 lg:p-6 pb-6 space-y-4 lg:space-y-0 lg:grid lg:grid-cols-2 lg:gap-4 lg:items-start">
      {/* Left column on desktop — identity + performance. The split point is
          the midpoint of upstream's block order, so mobile stacking is exactly
          what upstream ships. */}
      <div className="space-y-4">
      {/* ── Identity: the single source for name, role, territory, status ── */}
      <div className="card">
        <div className="flex items-center gap-4">
          <div className="flex size-16 shrink-0 items-center justify-center rounded-card bg-brand-100 text-2xl font-bold text-primary">
            {user?.full_name.charAt(0)}
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-bold text-slate-900 truncate">{user?.full_name}</h2>
            <p className="text-sm text-slate-500 truncate">{agent?.employee_code} · {agent?.territory}</p>
            <div className="flex items-center gap-2 mt-1.5">
              {agent && <TierBadge tier={agent.tier} />}
              <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${checkedIn ? "bg-success-100 text-success-700" : "bg-slate-100 text-slate-500"}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${checkedIn ? "bg-success-500" : "bg-slate-400"}`} />
                {checkedIn ? "On Duty" : "Off Duty"}
              </span>
            </div>
          </div>
          {checkedIn && (
            <button
              onClick={handleCheckOut}
              disabled={checkingOut}
              className="tap-target shrink-0 self-start text-xs font-semibold px-3 py-2 rounded-xl transition-colors"
              style={{
                background: "#FFFFFF", color: "#B3261E",
                border: "1px solid rgba(179,38,30,0.30)",
                opacity: checkingOut ? 0.6 : 1,
              }}
            >
              {checkingOut ? "Checking out…" : "Check Out"}
            </button>
          )}
        </div>

        {/* Specialization + languages — shown once, as quiet meta chips */}
        {agent && (
          <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap gap-1.5">
            <span className="text-xs px-2 py-0.5 rounded-md bg-brand-50 text-brand-700 font-medium">{agent.specialization}</span>
            {agent.languages_spoken.slice(0, 3).map((lang) => (
              <span key={lang} className="text-xs px-2 py-0.5 rounded-md bg-slate-100 text-slate-600">{lang}</span>
            ))}
          </div>
        )}
      </div>

      {/* ── Ranking hero: the lively focal metric ── */}
      <div className="card flex items-center gap-4">
        <RankingRing score={animatedRanking} />
        <div className="flex-1">
          <p className="text-sm font-semibold text-slate-800">Performance Ranking</p>
          <p className="text-xs text-slate-400 mt-0.5 leading-relaxed">
            Blends your collection rate, PTP conversions and visit efficiency into one score out of 100.
          </p>
        </div>
      </div>

      {/* ── This Month: each metric exactly once, all tiles aligned ── */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-2">This Month</h3>
        <div className="grid grid-cols-2 gap-3">
          <MetricTile icon={<HandCoins className="w-4 h-4" />} color="text-success-600"
            value={inrCompact(agent?.current_month_collections ?? 0)} label="Collected" />
          <MetricTile icon={<TrendingUp className="w-4 h-4" />} color="text-brand-600"
            value={agent?.current_month_visits ?? 0} label="Visits" />
          {/* "PTPs Due", not "PTPs Set" — the backend counts promises whose
              committed_date falls in this month and has passed, which is what
              came due rather than what was raised. An agent who took twelve
              promises this month, four of them dated next week, sees eight
              here; calling that "Set" would read as lost work. */}
          <MetricTile icon={<Calendar className="w-4 h-4" />} color="text-warning-600"
            value={agent?.current_month_ptps_set ?? 0} label="PTPs Due" />
          <MetricTile icon={<Target className="w-4 h-4" />} color="text-brand-600"
            value={`${ptpRate}%`} label="PTP Rate"
            sub={`${agent?.current_month_ptps_honored ?? 0} of ${agent?.current_month_ptps_set ?? 0} due kept`} />
        </div>
      </div>

      {/* ── Lifetime: only the two stats not shown elsewhere ── */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-2">Lifetime</h3>
        <div className="grid grid-cols-2 gap-3">
          <MetricTile icon={<Shield className="w-4 h-4" />} color="text-slate-800"
            value={`${((agent?.lifetime_collection_rate ?? 0) * 100).toFixed(1)}%`} label="Collection Rate" />
          <MetricTile icon={<Briefcase className="w-4 h-4" />} color="text-slate-800"
            value={agent?.max_cases_per_day ?? 0} label="Max Cases / Day" />
        </div>
      </div>

      </div>

      {/* Right column on desktop — availability, credentials, compliance */}
      <div className="space-y-4">
      {/* ── Duty calendar (unique, kept) ── */}
      {calendar && <AvailabilitySection calendar={calendar} />}

      {/* ── Leave: request + own history (2026-09-21) ── */}
      <LeaveRequestsSection />

      {/* ── Digital ID card: the single source for the ID number ── */}
      {agent && (
        <AgentIDCard
          agentId={agent.id}
          name={user?.full_name ?? agent.full_name ?? ""}
          idCardNumber={agent.id_card_number}
          territory={agent.territory}
          tier={agent.tier}
          employeeCode={agent.employee_code}
          validUntil="31 Mar 2026"
        />
      )}

      {/* ── RBI compliance (no longer repeats the ID number) ── */}
      <div className="card border-success-200 bg-success-50 space-y-2">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-success-600" />
          <span className="text-sm font-semibold text-success-700">RBI Compliance</span>
        </div>
        <ComplianceRow label="Contact Hours" value="8 AM – 7 PM" ok />
        <ComplianceRow label="Agent ID" value="Verified" ok />
        <ComplianceRow label="Geo-stamp" value="GPS tracking active" ok />
        <ComplianceRow label="Sunday Collections" value="Disabled" ok />
      </div>

      {/* ── Sign out ── */}
      <button onClick={handleLogout} className="w-full card flex items-center justify-center gap-2 text-danger-600 hover:bg-danger-50 cursor-pointer">
        <LogOut className="w-5 h-5" />
        <span className="text-sm font-medium">Sign Out</span>
      </button>
      </div>
    </div>
  );
}

// ── Circular ranking gauge (the lively element) ──────────────────────────────
function RankingRing({ score }: { score: number }) {
  const r = 32;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score));
  const offset = circ - (pct / 100) * circ;
  return (
    <div className="relative shrink-0" style={{ width: 84, height: 84 }}>
      <svg width={84} height={84} viewBox="0 0 84 84">
        <defs>
          <linearGradient id="rankGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#f59e0b" />
            <stop offset="100%" stopColor="#0C66E4" />
          </linearGradient>
        </defs>
        <circle cx={42} cy={42} r={r} fill="none" stroke="#e2e8f0" strokeWidth={7} />
        <circle
          cx={42} cy={42} r={r} fill="none" stroke="url(#rankGrad)" strokeWidth={7}
          strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
          transform="rotate(-90 42 42)" style={{ transition: "stroke-dashoffset 0.7s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-bold text-slate-900 leading-none">{score.toFixed(1)}</span>
        <span className="text-[9px] text-slate-400 mt-0.5">/ 100</span>
      </div>
    </div>
  );
}

// ── Aligned KPI tile — one consistent shape for every number on the page ─────
function MetricTile({ value, label, sub, color, icon }: {
  value: string | number; label: string; sub?: string; color: string; icon?: React.ReactNode;
}) {
  return (
    <div className="card py-3.5">
      <div className="flex items-center justify-between">
        <p className={`text-2xl font-bold ${color}`}>{value}</p>
        {icon && <span className={`${color} opacity-70`}>{icon}</span>}
      </div>
      <p className="text-xs font-medium text-slate-500 mt-1">{label}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function ComplianceRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-600">{label}</span>
      <span className={`font-medium ${ok ? "text-success-700" : "text-danger-600"}`}>{ok ? "✓ " : "✗ "}{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Availability Calendar Section
// ---------------------------------------------------------------------------

function AvailabilitySection({ calendar }: { calendar: AvailabilityCalendar }) {
  const { monthly_summary, calendar: days } = calendar;

  // Index every recorded day by its date, and by month, so we can render a real
  // month grid and jump between months.
  const byDate = new Map(days.map((d) => [d.date, d]));
  const monthKeys = Array.from(new Set(days.map((d) => d.date.slice(0, 7)))).sort();
  const attByMonth = new Map(monthly_summary.map((m) => [m.month, m.attendance_pct]));

  // Start on the most recent month that has data.
  const [monthIdx, setMonthIdx] = useState(Math.max(monthKeys.length - 1, 0));
  if (monthKeys.length === 0) return null;

  const viewKey = monthKeys[Math.min(monthIdx, monthKeys.length - 1)];   // "YYYY-MM"
  const [year, month] = viewKey.split("-").map(Number);                   // month is 1-based
  const daysInMonth = new Date(year, month, 0).getDate();
  const firstDow = new Date(year, month - 1, 1).getDay();                 // 0 = Sun
  const monthLabel = new Date(year, month - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  const todayStr = new Date().toISOString().slice(0, 10);

  // Leading blanks + the month's days.
  const cells: (number | null)[] = [
    ...Array(firstDow).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];

  const canPrev = monthIdx > 0;
  const canNext = monthIdx < monthKeys.length - 1;
  const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

  return (
    <div className="card space-y-2">
      {/* Month switcher */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => canPrev && setMonthIdx((i) => i - 1)}
          disabled={!canPrev}
          className="p-1 rounded-md text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:hover:bg-transparent"
          aria-label="Previous month"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-800">{monthLabel}</span>
          {attByMonth.has(viewKey) && (
            <span className="text-[11px] text-brand-600 font-medium">· {attByMonth.get(viewKey)}%</span>
          )}
        </div>
        <button
          onClick={() => canNext && setMonthIdx((i) => i + 1)}
          disabled={!canNext}
          className="p-1 rounded-md text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:hover:bg-transparent"
          aria-label="Next month"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-0.5">
        {WEEKDAYS.map((d, i) => (
          <div key={i} className="text-center text-[9px] font-semibold text-slate-400 uppercase">{d}</div>
        ))}
      </div>

      {/* Day grid — present = green, absent = red, no record = muted */}
      <div className="grid grid-cols-7 gap-0.5">
        {cells.map((day, i) => {
          if (day === null) return <div key={i} className="h-7" />;
          const dateStr = `${viewKey}-${String(day).padStart(2, "0")}`;
          const rec = byDate.get(dateStr);
          const isToday = dateStr === todayStr;
          let cls = "text-slate-300";                       // no record (Sunday / future)
          if (rec?.status === "ON_DUTY") cls = "bg-success-100 text-success-700 font-semibold";
          else if (rec?.status === "ON_LEAVE") cls = "bg-amber-100 text-amber-700 font-medium";   // 2026-09-21: approved leave
          else if (rec?.status === "OFF_DUTY") cls = "bg-danger-100 text-danger-600 font-medium";
          const title = rec
            ? `${dateStr} — ${rec.status === "ON_DUTY" ? `Present (${rec.cases} cases)` : rec.status === "ON_LEAVE" ? `On leave (${(rec.leave_type ?? "").replace(/_/g, " ").toLowerCase()})` : "Absent"}`
            : dateStr;
          return (
            <div
              key={i}
              title={title}
              className={`h-7 flex items-center justify-center rounded-md text-[11px] ${cls} ${isToday ? "ring-2 ring-brand-500 ring-inset" : ""}`}
            >
              {day}
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 pt-1.5 border-t border-slate-100">
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded bg-success-100 border border-success-300" /><span className="text-[11px] text-slate-500">Present</span></div>
        <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded bg-danger-100 border border-danger-300" /><span className="text-[11px] text-slate-500">Absent</span></div>
      </div>
    </div>
  );
}
