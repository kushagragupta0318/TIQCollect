/**
 * Shared Leaflet canvas.
 *
 * WHY THIS EXISTS. Three pages already touched Leaflet directly and each
 * repeated the same bootstrap — create the map, add an OpenStreetMap tile layer,
 * set the attribution, then invalidateSize() on a timeout because Leaflet
 * measures its container on creation and inside a flex/grid shell that
 * measurement can land before layout settles, leaving a panel of grey tiles.
 * A fourth copy was about to be written for the agent beat map. One definition,
 * one place — the same rule that ended seven copies of the DPD bucket rule.
 *
 * TILES. OpenStreetMap's public tile server, which is free and needs no key.
 * Its usage policy explicitly rules out heavy or commercial use, so a real
 * deployment must either self-host tiles (the OSRM extract in docker-compose
 * serves the same region) or take a provider. VITE_TILE_URL overrides the host
 * without touching this file.
 */
import { useEffect, useRef } from "react";
import L from "leaflet";
import { DEFAULT_CENTRE } from "./constants";

const TILE_URL =
  (import.meta.env.VITE_TILE_URL as string | undefined) ??
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

interface MapCanvasProps {
  /** Called once the map exists. Return a cleanup for anything you added. */
  onReady: (map: L.Map) => void | (() => void);
  /** Re-runs onReady when any of these change. */
  deps?: readonly unknown[];
  className?: string;
  centre?: L.LatLngExpression;
  zoom?: number;
}

export function MapCanvas({
  onReady,
  deps = [],
  className = "h-64 w-full rounded-xl overflow-hidden",
  centre = DEFAULT_CENTRE,
  zoom = 12,
}: MapCanvasProps) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  // Held in a ref so a changing callback identity does not tear the map down
  // and rebuild it on every render — that flickers and loses the viewport.
  //
  // WRITTEN IN AN EFFECT, NOT DURING RENDER. Assigning `onReadyRef.current`
  // in the render body is the exact defect this repo already fixed once, in
  // useAnimatedValue on 2026-09-07: React may render without committing, so a
  // ref written during render can hold a value from a render that never
  // happened. Declared BEFORE the draw effect so it is updated first — effects
  // run in declaration order.
  const onReadyRef = useRef(onReady);
  useEffect(() => {
    onReadyRef.current = onReady;
  });

  useEffect(() => {
    if (!el.current || mapRef.current) return;
    const map = L.map(el.current, {
      zoomControl: true,
      attributionControl: true,
    }).setView(centre, zoom);
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(map);
    mapRef.current = map;
    // See the header: without this the panel can render as grey tiles.
    const t = window.setTimeout(() => map.invalidateSize(), 0);
    return () => {
      window.clearTimeout(t);
      map.remove();
      mapRef.current = null;
    };
    // centre/zoom are the INITIAL view only; changing them later must not
    // rebuild the map, or a re-render would yank the viewport from under the
    // user mid-pan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const cleanup = onReadyRef.current(map);
    return typeof cleanup === "function" ? cleanup : undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return <div ref={el} className={className} />;
}
