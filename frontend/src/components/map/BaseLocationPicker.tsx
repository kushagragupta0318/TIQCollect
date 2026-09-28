// ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
// 2026-09-28 — NEW (P2 G02). Click-to-place base location picker for the
//   create/edit agent drawers. Pattern lifted from
//   pages/simulator/GpsPanel.tsx: MapCanvas + a single L.circleMarker moved on
//   click, so no marker icon image needs bundling. Deliberately its own file
//   under components/map/ rather than living inside the drawer — MapCanvas's
//   own sibling files (BeatRouteMap, GpsPanel) are each one map behaviour per
//   file, and a base-location picker is a third, reusable one (create AND
//   edit both need it).
// ────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapCanvas } from "@/components/map/MapCanvas";
import { DEFAULT_CENTRE } from "@/components/map/constants";

interface BaseLocationPickerProps {
  latitude: number | null;
  longitude: number | null;
  onChange: (lat: number, lon: number) => void;
}

const MARKER_STYLE: L.CircleMarkerOptions = {
  radius: 8, color: "#FFFFFF", weight: 3, fillColor: "#1677FF", fillOpacity: 1,
};

/**
 * Click anywhere on the map to place (or move) the agent's base-location
 * marker. Centred on the given point when one exists (the edit drawer's
 * pre-fill); otherwise centred on the app's default view with a hint below
 * the map until a point is picked.
 */
export function BaseLocationPicker({ latitude, longitude, onChange }: BaseLocationPickerProps) {
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.CircleMarker | null>(null);
  // Held in a ref, same reasoning as MapCanvas's own onReadyRef: onReady only
  // runs once (no deps below), so the click handler it registers must read a
  // never-stale callback rather than close over the first render's onChange.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });

  const hasPoint = latitude !== null && longitude !== null;

  // Moves the marker (or creates it, on the first click after mounting with
  // no point yet) whenever the picked coordinates change. Separate from
  // onReady because onReady runs once on mount and must not re-fire on every
  // keystroke-free click — the map itself never needs rebuilding here.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !hasPoint) return;
    const pos: L.LatLngExpression = [latitude as number, longitude as number];
    if (markerRef.current) {
      markerRef.current.setLatLng(pos);
    } else {
      markerRef.current = L.circleMarker(pos, MARKER_STYLE).addTo(map);
    }
  }, [latitude, longitude, hasPoint]);

  return (
    <div>
      <MapCanvas
        className="h-64 w-full overflow-hidden rounded-xl"
        zoom={hasPoint ? 14 : 11}
        centre={hasPoint ? ([latitude as number, longitude as number] as L.LatLngExpression) : DEFAULT_CENTRE}
        onReady={(map) => {
          mapRef.current = map;
          if (hasPoint) {
            markerRef.current = L.circleMarker([latitude as number, longitude as number], MARKER_STYLE).addTo(map);
          }
          const click = (e: L.LeafletMouseEvent) => {
            onChangeRef.current(e.latlng.lat, e.latlng.lng);
          };
          map.on("click", click);
          return () => {
            map.off("click", click);
            markerRef.current?.remove();
            markerRef.current = null;
          };
        }}
      />
      {!hasPoint && (
        <p className="text-xs mt-1.5" style={{ color: "#6B6D76" }}>
          Click the map to set the agent's base location
        </p>
      )}
    </div>
  );
}
