import { useEffect, useState } from "react";
import { Star, TrendingUp, CheckCircle, Calendar, LogOut, Shield } from "lucide-react";
import { useNavigate } from "react-router";
import { toast } from "react-hot-toast";
import { getProfile, getAvailabilityCalendar } from "@/api/agent";
import type { AvailabilityCalendar } from "@/api/agent";
import { useAuthStore } from "@/store/authStore";
import { TierBadge } from "@/components/ui/Badge";
import { StatCard } from "@/components/ui/Card";
import AgentIDCard from "@/components/ui/AgentIDCard";
import type { Agent } from "@/types";
import { useAnimatedValue } from "@/hooks/useAnimatedValue";

export default function AgentProfilePage() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [checkInStatus, setCheckInStatus] = useState<string>("OFF_DUTY");
  const [calendar, setCalendar] = useState<AvailabilityCalendar | null>(null);

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

  const ptpConversion = agent
    ? Math.round((agent.current_month_ptps_honored / Math.max(agent.current_month_ptps_set, 1)) * 100)
    : 0;

  const animatedRankingScore = useAnimatedValue(agent?.ranking_score ?? 0);

  return (
    <div className="p-4 space-y-4 pb-6">
      {/* Agent card */}
      <div className="card">
        <div className="flex items-center gap-4">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center text-white text-2xl font-bold shadow-lg">
            {user?.full_name.charAt(0)}
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-bold text-slate-900 truncate">{user?.full_name}</h2>
            <p className="text-sm text-slate-500">{agent?.employee_code} · {agent?.territory}</p>
            <div className="flex items-center gap-2 mt-1">
              {agent && <TierBadge tier={agent.tier} />}
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${checkedIn ? "bg-success-100 text-success-700" : "bg-slate-100 text-slate-500"}`}>
                {checkedIn ? "● On Duty" : "Off Duty"}
              </span>
            </div>
          </div>
        </div>

        {/* ID card info */}
        <div className="mt-4 pt-4 border-t border-slate-100 grid grid-cols-2 gap-3 text-sm">
          <div>
            <p className="text-xs text-slate-400">Agent ID</p>
            <p className="font-medium text-slate-700">{agent?.id_card_number}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400">Status</p>
            <p className="font-medium text-slate-700">{checkedIn ? "On Duty" : "Off Duty"}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400">Languages</p>
            <p className="font-medium text-slate-700">{agent?.languages_spoken.slice(0, 2).join(", ")}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400">Specialization</p>
            <p className="font-medium text-slate-700">{agent?.specialization}</p>
          </div>
        </div>

        {/* Ranking score */}
        <div className="mt-4 pt-4 border-t border-slate-100">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5 text-sm font-medium text-slate-700">
              <Star className="w-4 h-4 text-warning-500" />
              Ranking Score
            </div>
            <span className="text-xl font-bold text-brand-600">{animatedRankingScore.toFixed(1)}</span>
          </div>
          <div className="w-full bg-slate-100 rounded-full h-2.5">
            <div className="h-2.5 rounded-full bg-gradient-to-r from-warning-400 to-warning-500 transition-all duration-700" style={{ width: `${animatedRankingScore}%` }} />
          </div>
          <p className="text-xs text-slate-400 mt-1.5">Based on collection rate, PTP conversions & visit efficiency</p>
        </div>
      </div>

      {/* Today's performance */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-2">Today's Performance</h3>
        <div className="grid grid-cols-3 gap-2">
          <div className="card text-center py-3">
            <p className="text-xl font-bold text-brand-600">{(agent as any)?.cases_today ?? "—"}</p>
            <p className="text-xs text-slate-400">Cases Today</p>
          </div>
          <div className="card text-center py-3">
            <p className="text-xl font-bold text-success-600">₹{((agent?.current_month_collections ?? 0) / 1000).toFixed(0)}K</p>
            <p className="text-xs text-slate-400">Month Total</p>
          </div>
          <div className="card text-center py-3">
            <p className="text-xl font-bold text-slate-900">{agent?.current_month_visits ?? "—"}</p>
            <p className="text-xs text-slate-400">Month Visits</p>
          </div>
        </div>
      </div>

      {/* This month stats */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-2">This Month</h3>
        <div className="grid grid-cols-2 gap-3">
          <StatCard label="Visits" value={agent?.current_month_visits ?? 0} icon={<TrendingUp className="w-5 h-5" />} colorClass="text-brand-600" />
          <StatCard label="Collected" value={`₹${((agent?.current_month_collections ?? 0) / 1000).toFixed(0)}K`} icon={<CheckCircle className="w-5 h-5" />} colorClass="text-success-600" />
          <StatCard label="PTPs Set" value={agent?.current_month_ptps_set ?? 0} icon={<Calendar className="w-5 h-5" />} colorClass="text-warning-600" />
          <StatCard label="PTP Rate" value={`${ptpConversion}%`} subtext={`${agent?.current_month_ptps_honored}/${agent?.current_month_ptps_set} honored`} colorClass="text-brand-600" />
        </div>
      </div>

      {/* Lifetime */}
      <div className="card space-y-2">
        <p className="text-sm font-semibold text-slate-700">Lifetime Performance</p>
        <Row label="Collection Rate" value={`${((agent?.lifetime_collection_rate ?? 0) * 100).toFixed(1)}%`} />
        <Row label="Max Cases/Day" value={String(agent?.max_cases_per_day ?? 0)} />
        <Row label="Territory" value={agent?.territory ?? ""} />
      </div>

      {/* Digital ID Card */}
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

      {/* Availability Calendar */}
      {calendar && <AvailabilitySection calendar={calendar} />}

      {/* RBI Compliance */}
      <div className="card border-success-200 bg-success-50 space-y-2">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-success-600" />
          <span className="text-sm font-semibold text-success-700">RBI Compliance Status</span>
        </div>
        <ComplianceRow label="Contact Hours" value="8 AM – 7 PM" ok />
        <ComplianceRow label="Agent ID Verified" value={agent?.id_card_number ?? ""} ok />
        <ComplianceRow label="Geo-stamp Active" value="GPS tracking enabled" ok />
        <ComplianceRow label="No Sunday Collections" value="Enforced" ok />
      </div>

      {/* Logout */}
      <button onClick={handleLogout} className="w-full card flex items-center gap-3 text-danger-600 hover:bg-danger-50 cursor-pointer">
        <LogOut className="w-5 h-5" />
        <span className="text-sm font-medium">Sign Out</span>
      </button>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-slate-500">{label}</span>
      <span className="text-sm font-medium text-slate-900">{value}</span>
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
  const { summary, monthly_summary, calendar: days } = calendar;

  // Build a 26-week grid (rows = weeks, cols = days Mon–Sat)
  // We'll show the last 26 weeks of non-Sunday days as a compact dot grid
  const DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  // Group days into weeks (ISO week, Mon–Sat)
  type Week = { weekKey: string; slots: (typeof days[0] | null)[] };
  const weekMap: Map<string, (typeof days[0] | null)[]> = new Map();

  for (const day of days) {
    const d = new Date(day.date);
    // week key: year + ISO week number
    const startOfYear = new Date(d.getFullYear(), 0, 1);
    const weekNum = Math.ceil(((d.getTime() - startOfYear.getTime()) / 86400000 + startOfYear.getDay() + 1) / 7);
    const key = `${d.getFullYear()}-W${String(weekNum).padStart(2, "0")}`;
    if (!weekMap.has(key)) weekMap.set(key, [null, null, null, null, null, null]);
    const dowIndex = DOW_ORDER.indexOf(day.day_of_week);
    if (dowIndex >= 0) weekMap.get(key)![dowIndex] = day;
  }

  const weeks: Week[] = Array.from(weekMap.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .slice(-26)
    .map(([weekKey, slots]) => ({ weekKey, slots }));

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-brand-500" />
          <span className="text-sm font-semibold text-slate-700">Duty Calendar — Last 6 Months</span>
        </div>
        <span className="text-xs font-medium text-brand-600">{summary.attendance_rate_pct}% attendance</span>
      </div>

      {/* Summary pills */}
      <div className="flex gap-2">
        <span className="text-xs px-2 py-1 rounded-full bg-success-100 text-success-700 font-medium">{summary.on_duty_days} days on duty</span>
        <span className="text-xs px-2 py-1 rounded-full bg-slate-100 text-slate-500 font-medium">{summary.off_duty_days} days off</span>
      </div>

      {/* Dot grid — compact heatmap */}
      <div className="overflow-x-auto pb-1">
        <div className="flex gap-0.5" style={{ minWidth: `${weeks.length * 12}px` }}>
          {weeks.map(({ weekKey, slots }) => (
            <div key={weekKey} className="flex flex-col gap-0.5">
              {slots.map((day, i) => {
                if (!day) return <div key={i} className="w-2.5 h-2.5" />;
                const isOn = day.status === "ON_DUTY";
                const title = `${day.date} — ${isOn ? `On Duty (${day.cases} cases)` : "Off Duty"}`;
                return (
                  <div
                    key={i}
                    title={title}
                    className={`w-2.5 h-2.5 rounded-sm ${isOn ? "bg-success-400" : "bg-slate-200"}`}
                  />
                );
              })}
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2 mt-2">
          <div className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded-sm bg-success-400" /><span className="text-xs text-slate-400">On Duty</span></div>
          <div className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded-sm bg-slate-200" /><span className="text-xs text-slate-400">Off</span></div>
        </div>
      </div>

      {/* Monthly breakdown */}
      <div className="space-y-1 pt-1 border-t border-slate-100">
        <p className="text-xs font-medium text-slate-500 mb-1">Monthly Breakdown</p>
        {monthly_summary.slice(-4).map((m) => (
          <div key={m.month} className="flex items-center gap-2">
            <span className="text-xs text-slate-500 w-16 shrink-0">
              {new Date(m.month + "-01").toLocaleDateString("en-IN", { month: "short", year: "2-digit" })}
            </span>
            <div className="flex-1 bg-slate-100 rounded-full h-2 overflow-hidden">
              <div
                className={`h-2 rounded-full ${m.attendance_pct >= 80 ? "bg-success-400" : m.attendance_pct >= 60 ? "bg-warning-400" : "bg-danger-400"}`}
                style={{ width: `${m.attendance_pct}%` }}
              />
            </div>
            <span className="text-xs font-medium text-slate-700 w-8 text-right">{m.attendance_pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
