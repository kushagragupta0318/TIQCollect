import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { ArrowLeft, Navigation, CheckCircle, MapPin, ChevronRight, Zap, Route, RefreshCw } from "lucide-react";
import { toast } from "react-hot-toast";
import { reoptimizeBeat } from "@/api/agent";
import { DPDBadge, PriorityBadge, CaseStatusBadge } from "@/components/ui/Badge";
import { useBeat } from "@/contexts/BeatContext";
import type { Case } from "@/types";
import { useAnimatedValue } from "@/hooks/useAnimatedValue";

// Client-side Haversine for the Google Maps URL builder only
function distKm(lat1: number, lon1: number, lat2: number, lon2: number) {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Nearest-neighbour sort for the Google Maps URL (pending stops, front-end only)
function sortByProximity(cases: Case[], startLat: number, startLon: number): Case[] {
  if (cases.length <= 1) return cases;
  const rem = [...cases];
  const out: Case[] = [];
  let lat = startLat, lon = startLon;
  while (rem.length) {
    let ni = 0, nd = Infinity;
    rem.forEach((c, i) => {
      const d = distKm(lat, lon, c.customer.latitude, c.customer.longitude);
      if (d < nd) { nd = d; ni = i; }
    });
    const nxt = rem.splice(ni, 1)[0];
    out.push(nxt);
    lat = nxt.customer.latitude; lon = nxt.customer.longitude;
  }
  return out;
}

export default function BeatMapPage() {
  const navigate = useNavigate();
  const { beat, loading, refresh } = useBeat();
  const [reoptimizing, setReoptimizing] = useState(false);
  const [activeStop, setActiveStop] = useState<string | null>(null);
  const [userLoc, setUserLoc] = useState<{ lat: number; lon: number } | null>(null);

  // Agent002 home as fallback start (Sector 44, Gurugram)
  const START_LAT = 28.455151, START_LON = 77.071623;

  useEffect(() => {
    navigator.geolocation?.getCurrentPosition(
      (p) => setUserLoc({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => {},
    );
  }, []);

  function navigateToStop(c: Case) {
    window.open(`https://www.google.com/maps/dir/?api=1&destination=${c.customer.latitude},${c.customer.longitude}&travelmode=driving`, "_blank");
  }

  // Open Google Maps with the beat's current server-optimized order
  function openOptimizedRoute(allCases: Case[]) {
    if (allCases.length === 0) return;
    const startLat = userLoc?.lat ?? START_LAT;
    const startLon = userLoc?.lon ?? START_LON;
    // allCases is already in server-optimized order; pending stops first
    const visitedSet = new Set(beat?.visited_today_ids ?? []);
    const pending = allCases.filter((c) => !visitedSet.has(c.id) && !["PAID", "CLOSED", "WRITTEN_OFF"].includes(c.status));
    // Google Maps URL supports max 9 waypoints + 1 destination = 10 stops
    const stops = sortByProximity(pending, startLat, startLon).slice(0, 10);
    if (stops.length === 0) return;
    const dest = stops[stops.length - 1];
    const wps = stops.slice(0, -1).map((c) => `${c.customer.latitude},${c.customer.longitude}`).join("|");
    const url = `https://www.google.com/maps/dir/?api=1&origin=${startLat},${startLon}&destination=${dest.customer.latitude},${dest.customer.longitude}${wps ? `&waypoints=${wps}` : ""}&travelmode=driving`;
    window.open(url, "_blank");
  }

  // Call backend to re-run OSRM + OR-Tools from current GPS, refresh beat display
  async function handleReoptimize() {
    const lat = userLoc?.lat ?? START_LAT;
    const lon = userLoc?.lon ?? START_LON;
    setReoptimizing(true);
    try {
      const result = await reoptimizeBeat(lat, lon);
      if (result.optimized) {
        await refresh();
        toast.success(`Route re-optimized — ${result.pending_count} pending stops reordered`);
      } else {
        toast(result.message ?? "Nothing to reorder");
      }
    } catch {
      toast.error("Re-optimization failed — check network");
    } finally {
      setReoptimizing(false);
    }
  }

  const cases = beat?.cases ?? [];
  const visitedTodaySet = new Set(beat?.visited_today_ids ?? []);
  const completedCount = visitedTodaySet.size;
  const progressPct = Math.round((completedCount / Math.max(cases.length, 1)) * 100);
  const animatedProgressPct = useAnimatedValue(progressPct);

  if (loading) return (
    <div className="flex items-center justify-center h-screen">
      <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );

  if (!beat) return (
    <div className="flex flex-col items-center justify-center h-screen gap-4 text-slate-500">
      <MapPin className="w-12 h-12 opacity-40" />
      <p>No beat plan for today</p>
    </div>
  );

  const startLat = userLoc?.lat ?? START_LAT;
  const startLon = userLoc?.lon ?? START_LON;

  // Done = visited today only. Pending = everything else.
  // This guarantees: pending + done = total (no overlap, no gaps).
  const _pending = cases.filter((c) => !visitedTodaySet.has(c.id));

  // All stats from real-time server values — identical source as home-summary
  const collectedAmt = beat.amount_collected_today ?? 0;
  // Use server-computed ptps_due_today — same source as Home page stat.
  const ptpCount = beat.ptps_due_today ?? 0;
  const nextUnvisited = _pending[0] ?? null;

  return (
    <div className="min-h-screen bg-slate-50 pb-6">
      {/* Header */}
      <div className="bg-white border-b border-slate-100 sticky top-0 z-20">
        <div className="flex items-center gap-3 p-4">
          <button onClick={() => navigate(-1)} className="p-1 -ml-1 text-slate-400">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="flex-1">
            <h1 className="font-bold text-slate-900">Beat Map</h1>
            <p className="text-xs text-slate-400">{beat.beat_number} · {new Date(beat.beat_date).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}</p>
          </div>
          <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${beat.status === "IN_PROGRESS" ? "bg-brand-100 text-brand-700" : beat.status === "COMPLETED" ? "bg-success-100 text-success-700" : "bg-slate-100 text-slate-600"}`}>
            {beat.status.replace("_", " ")}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* Summary card */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-slate-700">Route Progress</span>
            <span className="text-sm font-bold text-brand-600">{animatedProgressPct}%</span>
          </div>
          <div className="w-full bg-slate-100 rounded-full h-2.5 mb-3">
            <div className="h-2.5 rounded-full bg-gradient-to-r from-brand-500 to-brand-600 transition-all duration-700" style={{ width: `${animatedProgressPct}%` }} />
          </div>
          <div className="grid grid-cols-4 gap-2 text-center">
            <Tile label="Pending" value={String(_pending.length)} />
            <Tile label="Done" value={String(completedCount)} color="text-success-600" />
            <Tile label="PTPs" value={String(ptpCount)} color="text-warning-600" />
            <Tile label="km" value={beat.estimated_distance_km.toFixed(1)} />
          </div>
          <div className="mt-3 pt-3 border-t border-slate-100 flex items-center justify-between text-sm">
            <div>
              <p className="text-xs text-slate-400">Collected today</p>
              <p className="font-bold text-success-600">₹{(collectedAmt / 1000).toFixed(1)}K</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-slate-400">Total target</p>
              <p className="font-bold text-slate-700">₹{(beat.total_target_amount / 1000).toFixed(0)}K</p>
            </div>
          </div>
        </div>

        {/* Action buttons row */}
        <div className="flex gap-3">
          {/* Open in Google Maps — uses current server-optimized order */}
          <button
            onClick={() => openOptimizedRoute(cases)}
            className="flex-1 card flex items-center gap-3 bg-brand-600 border-brand-600 text-white hover:bg-brand-700 transition-colors"
          >
            <div className="w-9 h-9 rounded-xl bg-brand-500 flex items-center justify-center flex-shrink-0">
              <Route className="w-4 h-4" />
            </div>
            <div className="flex-1 text-left">
              <p className="text-sm font-semibold">Open Route</p>
              <p className="text-xs opacity-80">{_pending.length} stops · Maps</p>
            </div>
          </button>

          {/* Re-optimize from current GPS: calls OSRM + OR-Tools on the backend */}
          <button
            onClick={handleReoptimize}
            disabled={reoptimizing}
            className="flex-1 card flex items-center gap-3 bg-white border-slate-200 text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-60"
          >
            <div className="w-9 h-9 rounded-xl bg-slate-100 flex items-center justify-center flex-shrink-0">
              <RefreshCw className={`w-4 h-4 text-brand-600 ${reoptimizing ? "animate-spin" : ""}`} />
            </div>
            <div className="flex-1 text-left">
              <p className="text-sm font-semibold text-slate-800">
                {reoptimizing ? "Optimizing…" : "Re-optimize"}
              </p>
              <p className="text-xs text-slate-400">OSRM + OR-Tools</p>
            </div>
          </button>
        </div>

        {/* Next stop highlight */}
        {nextUnvisited && (
          <div className="card border-warning-200 bg-warning-50">
            <div className="flex items-center gap-2 mb-2">
              <Zap className="w-4 h-4 text-warning-600" />
              <span className="text-xs font-semibold text-warning-700">NEXT STOP</span>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold text-slate-900">{nextUnvisited.customer.full_name}</p>
                <p className="text-xs text-slate-500">{nextUnvisited.customer.city} · DPD {nextUnvisited.loan.dpd}d</p>
              </div>
              <button
                onClick={() => navigateToStop(nextUnvisited)}
                className="flex items-center gap-1.5 bg-brand-600 text-white text-xs font-medium px-3 py-2 rounded-lg hover:bg-brand-700 transition-colors"
              >
                <Navigation className="w-3.5 h-3.5" />
                Go
              </button>
            </div>
          </div>
        )}

        {/* Stop list — pending stops only, in optimized visiting order */}
        <div className="space-y-0 bg-white rounded-2xl border border-slate-100 overflow-hidden">
          <div className="flex items-center justify-between p-3 pb-2">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Pending Stops</p>
            <span className="text-xs text-brand-600 font-medium flex items-center gap-1"><Route className="w-3 h-3" /> Optimised Order</span>
          </div>
          {_pending.length === 0 && (
            <div className="flex flex-col items-center justify-center py-10 text-slate-400">
              <CheckCircle className="w-8 h-8 mb-2 text-success-400" />
              <p className="text-sm font-medium text-success-600">All stops completed!</p>
            </div>
          )}
          {_pending.map((c, idx) => {
            const isPTP = c.status === "PTP_SET";
            const isActive = activeStop === c.id;
            return (
              <div key={c.id} className="border-t border-slate-50 first:border-t-0">
                <button
                  className="w-full text-left p-3 hover:bg-slate-50 transition-colors"
                  onClick={() => setActiveStop(isActive ? null : c.id)}
                >
                  <div className="flex items-center gap-3">
                    {/* Stop number */}
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 text-xs font-bold ${
                      isPTP ? "bg-warning-100 text-warning-700" : "bg-brand-100 text-brand-700"
                    }`}>
                      {idx + 1}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-sm font-medium truncate text-slate-900">{c.customer.full_name}</p>
                        {c.customer.is_hostile && <span className="text-danger-500 text-xs">⚠️</span>}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-xs text-slate-400">{c.customer.city}</span>
                        <DPDBadge bucket={c.loan.dpd_bucket} />
                        <PriorityBadge priority={c.priority} />
                      </div>
                    </div>

                    <div className="flex-shrink-0 text-right">
                      <p className="text-xs font-semibold text-danger-600">₹{(c.target_amount / 1000).toFixed(0)}K</p>
                      <CaseStatusBadge status={c.status} />
                    </div>
                  </div>
                </button>

                {/* Expanded actions */}
                {isActive && (
                  <div className="px-3 pb-3 flex gap-2">
                    <button
                      onClick={() => window.open(`tel:${c.customer.phone_primary}`, "_self")}
                      className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-2 rounded-lg bg-success-50 text-success-700 hover:bg-success-100 transition-colors"
                    >
                      📞 Call
                    </button>
                    <button
                      onClick={() => navigateToStop(c)}
                      className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-2 rounded-lg bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors"
                    >
                      <Navigation className="w-3.5 h-3.5" /> Navigate
                    </button>
                    <button
                      onClick={() => navigate(`/agent/cases/${c.id}`)}
                      className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-2 rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors"
                    >
                      <ChevronRight className="w-3.5 h-3.5" /> Detail
                    </button>
                    <button
                      onClick={() => navigate(`/agent/visit/${c.id}`)}
                      className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-2 rounded-lg bg-warning-50 text-warning-700 hover:bg-warning-100 transition-colors"
                    >
                      📋 Visit
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Route visualization (simple) — pending stops only */}
        {_pending.length > 0 && (
          <div className="card">
            <p className="text-sm font-semibold text-slate-700 mb-3">Remaining Route</p>
            <div className="relative overflow-hidden rounded-xl bg-slate-100 h-40 flex items-center justify-center">
              <div className="absolute inset-0 flex items-center justify-center">
                <svg viewBox="0 0 300 120" className="w-full h-full p-4">
                  {_pending.slice(0, 8).map((c, i) => {
                    const x = 20 + (i % 4) * 70;
                    const y = i < 4 ? 25 : 85;
                    return (
                      <g key={c.id}>
                        {i > 0 && (
                          <line
                            x1={20 + ((i - 1) % 4) * 70 + 10}
                            y1={i - 1 < 4 ? 25 : 85}
                            x2={x}
                            y2={y}
                            stroke="#3b82f6"
                            strokeWidth="1.5"
                            strokeDasharray="4 2"
                            opacity={0.5}
                          />
                        )}
                        <circle cx={x} cy={y} r="10" fill="#dbeafe" stroke="#3b82f6" strokeWidth="1.5" />
                        <text x={x} y={y + 4} textAnchor="middle" fontSize="8" fill="#1d4ed8" fontWeight="bold">{i + 1}</text>
                      </g>
                    );
                  })}
                </svg>
              </div>
              <div className="absolute bottom-2 right-2 text-xs text-slate-400 bg-white px-2 py-1 rounded-lg">
                {_pending.length} pending · {beat.estimated_distance_km}km
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Tile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <p className={`text-base font-bold ${color ?? "text-slate-900"}`}>{value}</p>
      <p className="text-xs text-slate-400">{label}</p>
    </div>
  );
}
