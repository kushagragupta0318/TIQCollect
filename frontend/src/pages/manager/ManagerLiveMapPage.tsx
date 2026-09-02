// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-18 — New page. The manager API has always returned
//   last_known_latitude/longitude, but nothing in the product could draw them:
//   no map library was installed at all, and the agent's own "Beat Map" page
//   just opened Google Maps in a new tab. The only way anyone saw an agent's
//   position was the Google link inside an SOS SMS.
//
//   Leaflet + OpenStreetMap rather than a keyed provider: free, no API key to
//   leak, and consistent with the OSRM routing this platform already runs on
//   OSM data.
// ────────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { AlertTriangle, BatteryLow, Crosshair, MapPin, RefreshCw, Route } from "lucide-react";
import { getAgentsLive, getAgentTrail, type AgentTrail, type LiveAgentPosition } from "@/api/manager";

const EASE = "cubic-bezier(0.2,0,0,1)";

// Poll cadence. Faster than the 30s used elsewhere because this page exists to
// answer "where is my team right now", and during an SOS that gap is the whole
// point of the screen.
const POLL_MS = 15_000;

// A fix older than this is drawn hollow. The agent has not necessarily stopped
// moving — more often their phone is in a pocket or out of signal — so it is
// shown as low confidence rather than hidden.
const STALE_AFTER_S = 600;

// Fallback view when nobody is being tracked yet: all of Delhi NCR, so an empty
// map still reads as a map rather than an ocean. Deliberately not used as a
// position for any agent.
const DEFAULT_CENTRE: [number, number] = [28.6139, 77.209];

const AGENT_COLORS = [
  "#2563EB", // Royal Blue
  "#059669", // Emerald Green
  "#D97706", // Amber Gold
  "#7C3AED", // Violet Purple
  "#DB2777", // Pink Rose
  "#0891B2", // Cyan Teal
  "#EA580C", // Vibrant Orange
  "#4F46E5", // Indigo
  "#16A34A", // Forest Green
  "#9333EA", // Purple
  "#0284C7", // Sky Blue
  "#E11D48", // Crimson Red
  "#D946EF", // Fuchsia
  "#0D9488", // Teal
  "#CA8A04", // Gold
];

export function getAgentColor(agentId: string, employeeCode?: string): string {
  const seed = employeeCode || agentId || "";
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash << 5) - hash + seed.charCodeAt(i);
    hash |= 0;
  }
  const idx = Math.abs(hash) % AGENT_COLORS.length;
  return AGENT_COLORS[idx];
}

export function getAgentInitials(name: string): string {
  if (!name) return "AG";
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function ageLabel(seconds: number | null): string {
  if (seconds == null) return "no fix yet";
  if (seconds < 60) return "just now";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} hr ago` : `${Math.round(h / 24)} d ago`;
}

/** Distinct vibrant colored marker with initials & pin pointer for each agent */
function agentIcon(a: LiveAgentPosition, isSelected: boolean = false): L.DivIcon {
  const sos = a.sos_active;
  const color = sos ? "#DC2626" : getAgentColor(a.agent_id, a.employee_code);
  const initials = getAgentInitials(a.full_name);
  const size = sos ? 36 : isSelected ? 34 : 28;

  return L.divIcon({
    className: "custom-agent-marker",
    iconSize: [size, size + 8],
    iconAnchor: [size / 2, size + 4],
    popupAnchor: [0, -size],
    html: `
      <div style="position:relative;width:${size}px;height:${size + 8}px;display:flex;flex-direction:column;align-items:center;cursor:pointer;">
        ${(sos || isSelected) ? `
          <div style="
            position:absolute;top:-4px;left:-4px;width:${size + 8}px;height:${size + 8}px;border-radius:50%;
            background:${color};opacity:0.35;animation:sospulse 1.4s ease-in-out infinite;pointer-events:none;
          "></div>
        ` : ""}
        <div style="
          width:${size}px;height:${size}px;border-radius:50%;
          background:${color};
          color:#ffffff;
          display:flex;align-items:center;justify-content:center;
          font-weight:700;font-size:${size > 30 ? "11px" : "10px"};
          font-family:system-ui,-apple-system,sans-serif;
          border:2.5px solid #ffffff;
          box-shadow:0 3px 10px rgba(0,0,0,0.35), 0 0 0 1px ${color}40;
          transition:transform 0.15s ease;
          ${isSelected ? "transform:scale(1.15);" : ""}
        ">
          ${sos ? "⚠️" : initials}
        </div>
        <div style="
          width:0;height:0;
          border-left:5px solid transparent;
          border-right:5px solid transparent;
          border-top:6px solid ${color};
          margin-top:-1px;
        "></div>
      </div>`,
  });
}

export default function ManagerLiveMapPage() {
  const mapEl = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<Map<string, L.Marker>>(new Map());
  const trailRef = useRef<L.LayerGroup | null>(null);
  const fittedRef = useRef(false);
  // Which SOS incident the map has already snapped to, so it snaps once per
  // incident rather than on every poll.
  const focusedSosRef = useRef<string | null>(null);

  const [agents, setAgents] = useState<LiveAgentPosition[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [trail, setTrail] = useState<AgentTrail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<Date | null>(null);

  // ── map bootstrap ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapEl.current || mapRef.current) return;
    const map = L.map(mapEl.current, { zoomControl: true, attributionControl: true })
      .setView(DEFAULT_CENTRE, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    trailRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    // Leaflet measures its container on creation; inside a flex/grid shell that
    // measurement can land before layout settles, leaving grey tiles.
    setTimeout(() => map.invalidateSize(), 0);
    // Captured now, not read from the ref at teardown: by cleanup time the ref
    // may already point at a different Map instance.
    const markers = markersRef.current;
    return () => {
      map.remove();
      mapRef.current = null;
      markers.clear();
    };
  }, []);

  // ── polling ───────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      const data = await getAgentsLive();
      setAgents(data.agents);
      setLastSync(new Date());
      setError(null);

      // An SOS is the one thing allowed to move the map without being asked.
      // Handled here, as a direct consequence of new data, rather than in an
      // effect watching `agents` — an effect would be a cascading render, and
      // would also re-snap the view every poll while the SOS stayed active,
      // fighting a manager trying to pan away.
      const sos = data.agents.find((a) => a.sos_active && a.latitude != null);
      if (!sos) {
        focusedSosRef.current = null;
      } else if (focusedSosRef.current !== sos.agent_id) {
        focusedSosRef.current = sos.agent_id;
        setSelected(sos.agent_id);
        mapRef.current?.setView([sos.latitude as number, sos.longitude as number], 15, { animate: true });
      }
    } catch {
      setError("Could not refresh agent positions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Polling an API is the "subscribe for updates from an external system"
    // case this rule explicitly sanctions. `load` is async and sets state from
    // its promise callback rather than synchronously, but the rule cannot see
    // through the await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // ── markers ───────────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const seen = new Set<string>();

    for (const a of agents) {
      if (a.latitude == null || a.longitude == null) continue;
      seen.add(a.agent_id);
      const pos: [number, number] = [a.latitude, a.longitude];
      const isSel = a.agent_id === selected;
      const existing = markersRef.current.get(a.agent_id);
      if (existing) {
        existing.setLatLng(pos);
        existing.setIcon(agentIcon(a, isSel));
        if (isSel) existing.setZIndexOffset(1000);
        else existing.setZIndexOffset(0);
      } else {
        const m = L.marker(pos, { icon: agentIcon(a, isSel), title: a.full_name, zIndexOffset: isSel ? 1000 : 0 })
          .addTo(map)
          .on("click", () => setSelected(a.agent_id));
        markersRef.current.set(a.agent_id, m);
      }
      markersRef.current.get(a.agent_id)!.bindTooltip(
        `<b>${a.full_name}</b><br/>${a.employee_code} · ${ageLabel(a.age_seconds)}` +
        (a.sos_active ? "<br/><b style='color:#DC2626'>SOS ACTIVE</b>" : ""),
        { direction: "top", offset: [0, -16] },
      );
    }

    // Drop markers for agents that no longer report a position at all.
    for (const [id, m] of markersRef.current) {
      if (!seen.has(id)) { m.remove(); markersRef.current.delete(id); }
    }

    // Fit once, on the first data that has anything in it. Re-fitting on every
    // poll would yank the view out from under a manager who has panned.
    if (!fittedRef.current && seen.size > 0) {
      const bounds = L.latLngBounds(
        agents.filter((a) => a.latitude != null)
              .map((a) => [a.latitude as number, a.longitude as number] as [number, number]),
      );
      map.fitBounds(bounds, { padding: [48, 48], maxZoom: 15 });
      fittedRef.current = true;
    }
  }, [agents, selected]);

  const sosAgents = useMemo(() => agents.filter((a) => a.sos_active), [agents]);

  // ── trail for the selected agent ──────────────────────────────────────────
  useEffect(() => {
    const layer = trailRef.current;
    if (!layer) return;
    layer.clearLayers();
    // Deliberately no setTrail(null) here — clearing state from an effect body
    // is a cascading render. Staleness is handled at render time instead, by
    // only showing a trail whose agent_id still matches the selection.
    if (!selected) return;

    let cancelled = false;
    getAgentTrail(selected).then((t) => {
      if (cancelled || !trailRef.current) return;
      setTrail(t);
      const selAgent = agents.find((ag) => ag.agent_id === selected);
      const selColor = selAgent ? getAgentColor(selAgent.agent_id, selAgent.employee_code) : "#2563EB";
      const pts = t.points.map((p) => [p.latitude, p.longitude] as [number, number]);
      if (pts.length > 1) {
        L.polyline(pts, { color: selColor, weight: 3.5, opacity: 0.8 }).addTo(trailRef.current);
      }
      for (const p of t.points) {
        if (p.source === "HEARTBEAT") continue;
        // Only the punctuation of the day gets its own marker — check-in,
        // visits, SOS. Drawing every heartbeat would bury them.
        L.circleMarker([p.latitude, p.longitude], {
          radius: p.is_sos ? 8 : 5,
          color: p.is_sos ? "#DC2626" : selColor,
          fillColor: p.is_sos ? "#DC2626" : selColor,
          fillOpacity: 0.9, weight: 2,
        })
          .bindTooltip(`${p.source} · ${new Date(p.recorded_at).toLocaleTimeString()}`,
                       { direction: "top" })
          .addTo(trailRef.current);
      }
    }).catch(() => { if (!cancelled) setTrail(null); });

    return () => { cancelled = true; };
  }, [selected, agents]);

  const tracked = agents.filter((a) => a.latitude != null).length;
  const selectedAgent = agents.find((a) => a.agent_id === selected) || null;
  // Guard against showing the previous agent's trail for a frame after the
  // selection changes but before the new fetch resolves.
  const shownTrail = trail && trail.agent_id === selected ? trail : null;

  return (
    <div className="space-y-4">
      <style>{`@keyframes sospulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.35);opacity:.65}}`}</style>

      {/* Header */}
      <div className="flex items-center justify-between gap-3"
           style={{ animation: `enter 420ms ${EASE} 0ms both` }}>
        <div className="min-w-0">
          <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>
            Live Map
          </h1>
          <p className="text-[13px] sm:text-sm" style={{ color: "#6B6D76" }}>
            {tracked} of {agents.length} agents reporting
            {lastSync && <> · updated {lastSync.toLocaleTimeString()}</>}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {sosAgents.length > 0 && (
            <div className="flex items-center gap-2 px-3 sm:px-4 py-2 rounded-xl font-semibold text-xs sm:text-sm animate-pulse"
                 style={{ background: "rgba(220,38,38,0.10)", border: "1px solid rgba(220,38,38,0.25)", color: "#991B1B" }}>
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              <span className="whitespace-nowrap">{sosAgents.length} SOS<span className="hidden sm:inline"> Active</span></span>
            </div>
          )}
          <button onClick={() => void load()} title="Refresh now"
                  className="tap-target rounded-xl px-3 py-2 text-xs font-semibold flex items-center gap-1.5"
                  style={{ background: "rgba(0,0,0,0.04)", color: "#3A3C44" }}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-card p-3 text-sm"
             style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.18)", color: "#991B1B" }}>
          {error}
        </div>
      )}

      <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(0,1fr)" }}>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          {/* Map */}
          <div className="rounded-card overflow-hidden"
               style={{ border: "1px solid rgba(0,0,0,0.07)", animation: `enter 420ms ${EASE} 60ms both` }}>
            <div ref={mapEl} style={{ height: "clamp(380px, 62vh, 720px)", width: "100%", background: "#E8EAEE" }} />
          </div>

          {/* Roster */}
          <div className="rounded-card p-3 space-y-2"
               style={{ border: "1px solid rgba(0,0,0,0.07)", maxHeight: "clamp(380px, 62vh, 720px)", overflowY: "auto",
                        animation: `enter 420ms ${EASE} 120ms both` }}>
            {agents.length === 0 && !loading && (
              <p className="text-sm p-3" style={{ color: "#8A8C94" }}>No agents assigned to you.</p>
            )}
            {[...agents]
              .sort((a, b) =>
                Number(b.sos_active) - Number(a.sos_active) ||
                (a.age_seconds ?? Infinity) - (b.age_seconds ?? Infinity))
              .map((a) => {
                const stale = (a.age_seconds ?? Infinity) > STALE_AFTER_S;
                const isSel = a.agent_id === selected;
                const color = a.sos_active ? "#DC2626" : getAgentColor(a.agent_id, a.employee_code);
                const initials = getAgentInitials(a.full_name);
                return (
                  <button
                    key={a.agent_id}
                    onClick={() => {
                      setSelected(isSel ? null : a.agent_id);
                      if (!isSel && a.latitude != null && mapRef.current) {
                        mapRef.current.setView([a.latitude, a.longitude as number], 15, { animate: true });
                      }
                    }}
                    className="w-full text-left rounded-xl p-2.5 transition-all flex items-center justify-between gap-2.5 group"
                    style={{
                      background: a.sos_active ? "rgba(220,38,38,0.08)" : isSel ? `${color}15` : "rgba(0,0,0,0.02)",
                      border: `1.5px solid ${a.sos_active ? "rgba(220,38,38,0.35)" : isSel ? color : "rgba(0,0,0,0.06)"}`,
                      boxShadow: isSel ? `0 2px 8px ${color}25` : "none",
                    }}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div
                        className="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-xs flex-shrink-0 text-white shadow-sm transition-transform group-hover:scale-105"
                        style={{ background: color }}
                      >
                        {a.sos_active ? "⚠️" : initials}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="font-bold text-xs text-slate-900 truncate">{a.full_name}</span>
                          {a.battery_pct != null && a.battery_pct <= 20 && (
                            <BatteryLow className="w-3.5 h-3.5 flex-shrink-0 text-amber-600" />
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
                          <span className="font-mono text-slate-600 font-semibold">{a.employee_code}</span>
                          <span>•</span>
                          <span className={stale ? "text-amber-600 font-medium" : "text-slate-500"}>
                            {ageLabel(a.age_seconds)}
                          </span>
                          {a.battery_pct != null && (
                            <>
                              <span>•</span>
                              <span>{a.battery_pct}%</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 flex-shrink-0">
                      <div
                        className="w-2.5 h-2.5 rounded-full"
                        style={{
                          background: color,
                          boxShadow: `0 0 0 2px ${color}30`,
                        }}
                      />
                    </div>
                  </button>
                );
              })}
          </div>
        </div>

        {/* Selected agent's day */}
        {selectedAgent && shownTrail && (
          <div className="rounded-card p-4 flex flex-wrap items-center gap-x-6 gap-y-2"
               style={{ border: "1px solid rgba(0,0,0,0.07)", animation: `enter 420ms ${EASE} 0ms both` }}>
            <div className="flex items-center gap-2">
              <Route className="w-4 h-4 flex-shrink-0" style={{ color: "#2563EB" }} />
              <span className="font-semibold text-sm" style={{ color: "#1C1C1F" }}>
                {shownTrail.full_name} — today
              </span>
            </div>
            <Stat label="Distance" value={`${(shownTrail.distance_metres / 1000).toFixed(2)} km`} />
            <Stat label="Fixes" value={String(shownTrail.point_count)} />
            <Stat label="Last seen" value={ageLabel(selectedAgent.age_seconds)} />
            {selectedAgent.accuracy_metres != null && (
              <Stat label="Accuracy" value={`±${Math.round(selectedAgent.accuracy_metres)} m`} />
            )}
            {shownTrail.point_count === 0 && (
              <span className="text-[12.5px] flex items-center gap-1.5" style={{ color: "#B45309" }}>
                <Crosshair className="w-3.5 h-3.5" /> No fixes recorded today
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10.5px] font-semibold uppercase" style={{ color: "#8A8C94", letterSpacing: "0.04em" }}>{label}</span>
      <span className="text-sm font-bold" style={{ color: "#1C1C1F", fontVariantNumeric: "tabular-nums" }}>{value}</span>
    </div>
  );
}
