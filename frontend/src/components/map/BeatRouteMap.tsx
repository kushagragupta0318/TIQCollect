/**
 * The agent's day, on a real map.
 *
 * WHAT THIS REPLACES. BeatMapPage drew a decorative `<svg viewBox="0 0 300 120">`
 * that laid the next eight stops out on a fixed 4x2 grid and joined them with
 * dashed lines. The positions were invented — two stops 200 metres apart and two
 * 30 km apart rendered identically — so the one thing a route map exists to show,
 * where the work actually is, was the one thing it could not.
 *
 * HONEST ABOUT THE LINE IT DRAWS. `route_geometry` is a real road polyline from
 * OSRM. When OSRM was unreachable at planning time the backend records
 * route_source="haversine" and no geometry, and this component then draws a
 * dashed straight-line connector and says so, rather than presenting four
 * straight lines as a driving route.
 */
import { useMemo } from "react";
import L from "leaflet";
import { escapeHtml } from "@/lib/html";
import { simplifyPath } from "@/lib/simplify";
import { MapCanvas } from "./MapCanvas";
import { decodePolyline } from "./polyline";

export interface BeatStop {
  id: string;
  lat: number;
  lon: number;
  label: string;
  sublabel?: string;
  done?: boolean;
}

interface BeatRouteMapProps {
  stops: BeatStop[];
  start?: { lat: number; lon: number } | null;
  geometry?: string | null;
  source?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
}

const DONE = "#94a3b8"; // slate-400
const NEXT = "#2563eb"; // blue-600
const LATER = "#64748b"; // slate-500
const BASE = "#0f766e"; // teal-700

function pin(colour: string, text: string, dim: boolean) {
  return L.divIcon({
    className: "",
    html:
      `<div style="background:${colour};opacity:${dim ? 0.55 : 1};` +
      `width:26px;height:26px;border-radius:50%;display:flex;align-items:center;` +
      `justify-content:center;color:#fff;font:600 12px/1 system-ui,sans-serif;` +
      `box-shadow:0 1px 4px rgba(0,0,0,.35);border:2px solid #fff">${text}</div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

export function BeatRouteMap({
  stops,
  start,
  geometry,
  source,
  onSelect,
  className,
}: BeatRouteMapProps) {
  const road = useMemo(
    () => (geometry ? simplifyPath(decodePolyline(geometry)) : []),
    [geometry],
  );

  const draw = (map: L.Map) => {
    const layer = L.layerGroup().addTo(map);
    const bounds: L.LatLngExpression[] = [];

    if (start) {
      L.marker([start.lat, start.lon], { icon: pin(BASE, "⌂", false) })
        .bindTooltip("Your base", { direction: "top" })
        .addTo(layer);
      bounds.push([start.lat, start.lon]);
    }

    if (road.length > 1) {
      L.polyline(road, { color: NEXT, weight: 4, opacity: 0.75 }).addTo(layer);
      road.forEach((p) => bounds.push(p));
    } else if (stops.length) {
      // No road geometry: connect the stops so the ORDER is still readable, but
      // dashed, because this is not the path anyone will drive.
      const line: L.LatLngExpression[] = [
        ...(start ? [[start.lat, start.lon] as L.LatLngExpression] : []),
        ...stops.map((s) => [s.lat, s.lon] as L.LatLngExpression),
      ];
      L.polyline(line, {
        color: LATER,
        weight: 2.5,
        opacity: 0.6,
        dashArray: "6 6",
      }).addTo(layer);
    }

    const firstPending = stops.findIndex((s) => !s.done);
    stops.forEach((s, i) => {
      const colour = s.done ? DONE : i === firstPending ? NEXT : LATER;
      const marker = L.marker([s.lat, s.lon], {
        icon: pin(colour, String(i + 1), !!s.done),
      })
        .bindTooltip(
          `<strong>${escapeHtml(s.label)}</strong>${s.sublabel ? `<br>${escapeHtml(s.sublabel)}` : ""}`,
          { direction: "top" },
        )
        .addTo(layer);
      if (onSelect) marker.on("click", () => onSelect(s.id));
      bounds.push([s.lat, s.lon]);
    });

    if (bounds.length) {
      map.fitBounds(L.latLngBounds(bounds).pad(0.18), { maxZoom: 15 });
    }
    return () => layer.remove();
  };

  const approximate = !geometry && stops.length > 0;

  return (
    <div className="relative">
      <MapCanvas
        onReady={draw}
        deps={[stops, road, start?.lat, start?.lon]}
        className={className ?? "h-64 w-full rounded-xl overflow-hidden"}
      />
      {approximate && (
        <div className="absolute bottom-10 left-2 z-[400] text-xs text-slate-600 bg-white/90 px-2 py-1 rounded-lg shadow-sm">
          Straight-line order{source ? ` · ${source}` : ""} — road route
          unavailable
        </div>
      )}
    </div>
  );
}
