import { useEffect, useRef } from "react";
import L from "leaflet";
import { escapeHtml } from "@/lib/html";
import "leaflet/dist/leaflet.css";
import { MapCanvas } from "@/components/map/MapCanvas";
import type { LatLng, Track } from "./routePlayback";
import { sim } from "./simTheme";

export interface Stop {
  id: string;
  label: string;
  lat: number;
  lon: number;
}

/**
 * The simulated GPS: the phone's position on a map. Click anywhere to put the
 * agent there; the beat's road route and its stops are drawn for reference.
 * Circle markers rather than L.marker so no icon images need bundling.
 */
export function GpsPanel({
  fix,
  track,
  stops,
  gpsLost,
  onPick,
}: {
  fix: LatLng | null;
  track: Track | null;
  stops: Stop[];
  gpsLost: boolean;
  onPick: (p: LatLng) => void;
}) {
  const mapRef = useRef<L.Map | null>(null);
  const agentRef = useRef<L.CircleMarker | null>(null);
  const followRef = useRef(true);
  const onPickRef = useRef(onPick);
  useEffect(() => {
    onPickRef.current = onPick;
  });

  const trackKey = track ? `${track.points.length}:${track.total.toFixed(0)}` : "none";
  const stopsKey = stops.map((s) => s.id).join(",");

  // Agent marker follows the fix; the view follows until the user pans.
  useEffect(() => {
    const map = mapRef.current;
    const m = agentRef.current;
    if (!map || !m || !fix) return;
    m.setLatLng(fix);
    m.setStyle({ fillColor: gpsLost ? sim.tone.muted : sim.tone.brand });
    if (followRef.current && !map.getBounds().pad(-0.2).contains(fix)) map.panTo(fix, { animate: true });
  }, [fix, gpsLost]);

  return (
    <MapCanvas
      className="h-56 w-full overflow-hidden rounded-xl"
      zoom={14}
      centre={fix ?? undefined}
      deps={[trackKey, stopsKey]}
      onReady={(map) => {
        mapRef.current = map;
        const layers: L.Layer[] = [];
        if (track && track.points.length > 1) {
          const line = L.polyline(track.points, { color: sim.tone.brand, weight: 3, opacity: 0.55 });
          layers.push(line);
          map.fitBounds(line.getBounds(), { padding: [16, 16] });
        }
        stops.forEach((s, i) => {
          layers.push(
            L.circleMarker([s.lat, s.lon], {
              radius: 6, color: "#FFFFFF", weight: 2, fillColor: sim.tone.warn, fillOpacity: 1,
            }).bindTooltip(`${i + 1}. ${escapeHtml(s.label)}`, { direction: "top" }),
          );
        });
        const agent = L.circleMarker(fix ?? map.getCenter(), {
          radius: 8, color: "#FFFFFF", weight: 3, fillColor: sim.tone.brand, fillOpacity: 1,
        }).bindTooltip("Simulated phone", { direction: "right" });
        agentRef.current = agent;
        layers.push(agent);
        layers.forEach((l) => l.addTo(map));

        const click = (e: L.LeafletMouseEvent) => {
          followRef.current = true;
          onPickRef.current([e.latlng.lat, e.latlng.lng]);
        };
        const drag = () => { followRef.current = false; };
        map.on("click", click);
        map.on("dragstart", drag);
        return () => {
          map.off("click", click);
          map.off("dragstart", drag);
          layers.forEach((l) => l.remove());
          agentRef.current = null;
        };
      }}
    />
  );
}
