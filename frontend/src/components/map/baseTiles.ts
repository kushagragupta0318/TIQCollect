/**
 * Leaflet side of lib/mapTiles.ts: every map's base layer goes through addBaseTiles, so the
 * tile source, its attribution and Mapbox's required wordmark are decided in one place.
 */
import L from "leaflet";
import { resolveTileSource, hasNightVariant, type MapVariant, type TileEnv, type TileSource } from "@/lib/mapTiles";
import mapboxLogoUrl from "./mapbox-logo.svg";

// The three keys by name: passing import.meta.env whole makes Vite inline every VITE_* var.
const ENV: TileEnv = {
  VITE_TILE_URL: import.meta.env.VITE_TILE_URL,
  VITE_MAPBOX_TOKEN: import.meta.env.VITE_MAPBOX_TOKEN,
  VITE_ALLOWED_MAP_HOSTS: import.meta.env.VITE_ALLOWED_MAP_HOSTS,
};

const warned = new Set<string>();

export function tileSourceFor(variant: MapVariant, env: TileEnv = ENV): TileSource {
  const source = resolveTileSource(variant, env, window.location.hostname);
  if (source.fallbackReason && !warned.has(source.fallbackReason)) {
    warned.add(source.fallbackReason);
    console.warn(`[maps] ${source.fallbackReason}`);
  }
  return source;
}

export function nightVariantAvailable(env: TileEnv = ENV): boolean {
  return hasNightVariant(env, window.location.hostname);
}

const MapboxWordmark = L.Control.extend({
  onAdd() {
    const a = L.DomUtil.create("a", "tiq-mapbox-wordmark");
    a.href = "https://www.mapbox.com/about/maps/";
    a.target = "_blank";
    a.rel = "noopener";
    a.setAttribute("aria-label", "Mapbox");
    a.style.display = "block";
    const img = L.DomUtil.create("img", "", a);
    img.src = mapboxLogoUrl;
    img.alt = "Mapbox";
    img.width = 88;
    img.height = 23;
    return a;
  },
});

/** Adds the base tiles for `variant`; the returned function removes them (for a variant switch). */
export function addBaseTiles(map: L.Map, variant: MapVariant = "day", env: TileEnv = ENV): () => void {
  const source = tileSourceFor(variant, env);
  const layer = L.tileLayer(source.url, source.options).addTo(map);
  const wordmark = source.provider === "mapbox" ? new MapboxWordmark({ position: "bottomleft" }).addTo(map) : null;
  return () => {
    layer.remove();
    wordmark?.remove();
  };
}
