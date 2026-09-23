import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { shortMoney } from "@/lib/money";
import { ArrowLeft, Navigation, CheckCircle, MapPin, ChevronRight, Zap, Route, RefreshCw, Phone, ClipboardList, AlertTriangle } from "lucide-react";
import { toast } from "react-hot-toast";
import { reoptimizeBeat } from "@/api/agent";
import { DPDBadge, VisitPriorityBadge, CaseStatusBadge } from "@/components/ui/Badge";
import { useBeat } from "@/contexts/useBeat";
import type { Case } from "@/types";
import { useAnimatedValue } from "@/hooks/useAnimatedValue";
import { BeatRouteMap, type BeatStop } from "@/components/map/BeatRouteMap";
import "leaflet/dist/leaflet.css";

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

  // 2026-09-07 — useAnimatedValue used to be called further down, after the `loading` and `no beat` early returns. That is a conditional
  // hook: on a render that took either early path the hook was skipped, so
  // React's hook order changed between renders and state could be read back
  // against the wrong slot. It is the kind of fault that shows up as a stray
  // animation or a stale number rather than as a crash.
  //
  // Hoisted above every return, with the value computed defensively from a beat
  // that may not be loaded yet. Hooks must run in the same order on every
  // render; the guards below only decide what is DRAWN.
  const progressPct = Math.round(
    ((new Set(beat?.visited_today_ids ?? []).size) /
      Math.max((beat?.cases ?? []).length, 1)) * 100,
  );
  const animatedProgressPct = useAnimatedValue(progressPct);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!beat) {
    return (
      <div className="min-h-svh bg-slate-50 flex flex-col">
        <div className="bg-white border-b border-slate-100 sticky top-0 z-20">
          <div className="flex items-center gap-3 p-4">
            <button onClick={() => navigate(-1)} className="tap-target -ml-1 text-slate-400 flex items-center justify-center">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="flex-1">
              <h1 className="font-bold text-slate-900">Beat Map</h1>
              <p className="text-xs text-slate-400">Today's Field Route</p>
            </div>
          </div>
        </div>

        <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
          <div className="w-16 h-16 rounded-2xl bg-indigo-50 flex items-center justify-center text-brand-600 mb-4 shadow-sm border border-indigo-100">
            <MapPin className="w-8 h-8 opacity-80" />
          </div>
          <h2 className="text-base font-bold text-slate-800 mb-1">No Field Route Scheduled</h2>
          <p className="text-xs text-slate-500 max-w-xs mb-6">
            You do not have an active beat route assigned for today. When your manager sequences your queue, your map stops will appear here.
          </p>
          <button
            onClick={() => refresh()}
            className="btn btn-primary text-xs flex items-center gap-2 px-4 py-2"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Check For Updates</span>
          </button>
        </div>
      </div>
    );
  }

  const cases = beat.cases ?? [];
  const visitedTodaySet = new Set(beat.visited_today_ids ?? []);
  const completedCount = visitedTodaySet.size;

  // Done = visited today only. Pending = everything else.
  const _pending = cases.filter((c) => !visitedTodaySet.has(c.id));
  // Stops in BEAT ORDER, not pending-first: the map shows the planned day, and
  // renumbering it as visits complete would make the sequence disagree with the
  // route the optimiser actually produced.
  const mapStops: BeatStop[] = cases
    .filter((c) => c.customer?.latitude != null && c.customer?.longitude != null)
    .map((c) => ({
      id: c.id,
      lat: c.customer!.latitude as number,
      lon: c.customer!.longitude as number,
      label: c.customer?.full_name ?? c.case_number,
      sublabel: c.customer?.city ?? undefined,
      done: visitedTodaySet.has(c.id),
    }));

  // All stats from real-time server values — identical source as home-summary
  const collectedAmt = beat.amount_collected_today ?? 0;
  const ptpCount = beat.ptps_due_today ?? 0;
  const nextUnvisited = _pending[0] ?? null;

  return (
    <div className="min-h-svh bg-slate-50 pb-6">
      {/* Header */}
      <div className="bg-white border-b border-slate-100 sticky top-0 z-20">
        <div className="flex items-center gap-3 p-4">
          <button onClick={() => navigate(-1)} className="tap-target -ml-1 text-slate-400 flex items-center justify-center">
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

      <div className="p-4 lg:p-6 space-y-4">
        {/* Summary card */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-slate-700">Route Progress</span>
            <span className="text-sm font-bold text-brand-600">{animatedProgressPct}%</span>
          </div>
          <div className="w-full bg-slate-100 rounded-full h-2.5 mb-3">
            <div className="h-2.5 rounded-full bg-primary transition-all duration-700" style={{ width: `${animatedProgressPct}%` }} />
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
              <p className="font-bold text-slate-700">{shortMoney(beat.total_target_amount)}</p>
            </div>
          </div>
        </div>

        {/* Action buttons row */}
        <div className="flex gap-3">
          {/* Open in Google Maps — uses current server-optimized order */}
          <button
            onClick={() => openOptimizedRoute(cases)}
            className="flex-1 card flex items-center gap-3 bg-brand-600 border-brand-600 text-white hover:bg-brand-700 transition-[background-color,border-color,box-shadow,transform]"
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
            className="flex-1 card flex items-center gap-3 bg-white border-slate-200 text-slate-700 hover:bg-slate-50 transition-[background-color,border-color,box-shadow,transform] disabled:opacity-60"
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
              <div className="min-w-0 pr-3">
                <p className="font-semibold text-slate-900">{nextUnvisited.customer.full_name}</p>
                {nextUnvisited.customer.address_line1 && (
                  <p className="text-xs text-slate-600 mt-0.5 break-words">
                    {nextUnvisited.customer.address_line1}
                    {nextUnvisited.customer.address_line2 && `, ${nextUnvisited.customer.address_line2}`}
                  </p>
                )}
                <p className="text-xs text-slate-500 mt-0.5">
                  {nextUnvisited.customer.city}
                  {nextUnvisited.customer.pincode && ` ${nextUnvisited.customer.pincode}`}
                  {" · "}DPD {nextUnvisited.loan.dpd}d
                </p>
              </div>
              <button
                onClick={() => navigateToStop(nextUnvisited)}
                className="tap-target flex items-center justify-center gap-1.5 bg-brand-600 text-white text-xs font-medium px-3 py-2 rounded-lg hover:bg-brand-700 transition-colors"
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
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 text-xs font-bold ${
                      isPTP ? "bg-warning-100 text-warning-700" : "bg-brand-100 text-brand-700"
                    }`}>
                      {idx + 1}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-sm font-medium truncate text-slate-900">{c.customer.full_name}</p>
                        {c.customer.is_hostile && <AlertTriangle className="w-3.5 h-3.5 text-danger-500" aria-label="Hostile" />}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-xs text-slate-400">{c.customer.city}</span>
                        <DPDBadge bucket={c.loan.dpd_bucket} />
                        <VisitPriorityBadge priority={c.visit_priority} />
                      </div>
                    </div>

                    <div className="flex-shrink-0 text-right">
                      <p className="text-xs font-semibold text-danger-600">{shortMoney(c.target_amount)}</p>
                      <CaseStatusBadge status={c.status} />
                    </div>
                  </div>
                </button>

                {isActive && (
                  <div className="px-3 pb-3 flex gap-2">
                    <button
                      onClick={() => window.open(`tel:${c.customer.phone_primary}`, "_self")}
                      className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-2 rounded-lg bg-success-50 text-success-700 hover:bg-success-100 transition-colors"
                    >
                      <Phone className="w-3.5 h-3.5" /> Call
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
                      <ClipboardList className="w-3.5 h-3.5" /> Visit
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* ── The day, on a real map ────────────────────────────────────────
            2026-09-08. This was an `<svg viewBox="0 0 300 120">` that laid the
            next eight stops on a fixed 4x2 grid and joined them with dashed
            lines. The positions were invented, so two stops 200 m apart and two
            30 km apart drew identically — the one thing a route map exists to
            show was the one thing it could not. Navigation still hands off to
            the phone's map app (that deep-link is a free URL scheme, no key,
            and it is the right tool for turn-by-turn); what changed is that the
            agent can now see the shape of their day before they set off. */}
        {cases.length > 0 && (
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <p className="text-sm font-semibold text-slate-700">Route</p>
              <p className="text-xs text-slate-400">
                {_pending.length} pending · {beat.estimated_distance_km.toFixed(1)} km
                {beat.estimated_duration_minutes ? ` · ~${Math.round(beat.estimated_duration_minutes / 60)}h` : ""}
              </p>
            </div>
            <BeatRouteMap
              stops={mapStops}
              start={
                beat.start_latitude != null && beat.start_longitude != null
                  ? { lat: beat.start_latitude, lon: beat.start_longitude }
                  : null
              }
              geometry={beat.route_geometry}
              source={beat.route_source}
              onSelect={(id) => navigate(`/agent/cases/${id}`)}
              className="h-56 w-full rounded-xl overflow-hidden"
            />
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
