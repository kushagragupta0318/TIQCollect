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
 * TILES come from components/map/baseTiles.ts (lib/mapTiles.ts decides the
 * source: Mapbox with a token, else OpenStreetMap).
 */
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { DEFAULT_CENTRE } from "./constants";
import { addBaseTiles } from "./baseTiles";

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
      // A map inside a scrolling page must not eat the page's scroll: rolling
      // the wheel over it used to zoom instead of scrolling past, trapping the
      // reader on the panel. Leaflet's own answer — the wheel scrolls the page
      // until the map is CLICKED (or ctrl/⌘ is held), and from then on it
      // zooms; clicking off the map hands scrolling back. Zoom buttons and
      // pinch-zoom are unaffected, so nothing is lost on touch.
      scrollWheelZoom: "center",
    }).setView(centre, zoom);
    map.scrollWheelZoom.disable();
    map.on("click focus", () => map.scrollWheelZoom.enable());
    map.on("blur", () => map.scrollWheelZoom.disable());
    // Clicking anywhere outside the map releases the wheel back to the page.
    const releaseWheel = (e: MouseEvent) => {
      if (el.current && e.target instanceof Node && !el.current.contains(e.target)) {
        map.scrollWheelZoom.disable();
      }
    };
    document.addEventListener("click", releaseWheel, true);
    addBaseTiles(map);
    mapRef.current = map;
    // See the header: without this the panel can render as grey tiles.
    const t = window.setTimeout(() => map.invalidateSize(), 0);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("click", releaseWheel, true);
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
