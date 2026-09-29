/**
 * The one tile-source config for every map. Pure (no Leaflet runtime, no import.meta.env) so
 * vite.config.ts can import checkMapboxToken. Precedence: VITE_TILE_URL > Mapbox (token + allowed
 * host) > OSM's public server, so a missing or refused token degrades the map, never breaks it.
 */
import type { TileLayerOptions } from "leaflet";

export type MapVariant = "day" | "night";

export interface TileEnv {
  VITE_TILE_URL?: string;
  VITE_MAPBOX_TOKEN?: string;
  VITE_ALLOWED_MAP_HOSTS?: string;
}

export type TileProvider = "custom" | "mapbox" | "osm";

export interface TileSource {
  provider: TileProvider;
  url: string;
  options: TileLayerOptions;
  /** Set when Mapbox was configured but not used; logged once by baseTiles. */
  fallbackReason?: string;
}

// Mapbox's own styles: streets for the day map (the OSM look family, light),
// dark for the manager live map's night preference. No custom style build.
const MAPBOX_STYLE: Record<MapVariant, string> = {
  day: "mapbox/streets-v12",
  night: "mapbox/dark-v11",
};

export const OSM_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';

// Mapbox's attribution terms for a third-party library: all three links.
// The wordmark they also require is added by baseTiles.
export const MAPBOX_ATTRIBUTION =
  '<a href="https://www.mapbox.com/about/maps/" target="_blank" rel="noopener">&copy; Mapbox</a> ' +
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">&copy; OpenStreetMap</a> ' +
  '<strong><a href="https://apps.mapbox.com/feedback/" target="_blank" rel="noopener">Improve this map</a></strong>';

// Hosts where the token may always be used: a developer's own machine.
const ALWAYS_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "::1"];

// Performance options every layer shares. keepBuffer 4 keeps a ring of tiles
// around the view so a short pan re-uses them; updateWhenIdle waits for the
// pan to end instead of fetching tiles the user is only passing over.
const SHARED_OPTIONS: TileLayerOptions = {
  maxZoom: 19,
  updateWhenIdle: true,
  keepBuffer: 4,
};

/** Hostnames from VITE_ALLOWED_MAP_HOSTS: comma-separated hosts or full URLs. */
export function parseAllowedHosts(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean)
    .map((entry) => {
      if (!entry.includes("://")) {
        const host = entry.replace(/\/.*$/, "");
        if (host.startsWith("[")) return host.slice(1, host.indexOf("]"));
        return host.split(":").length === 2 ? host.split(":")[0] : host;
      }
      try {
        return new URL(entry).hostname.replace(/^\[|\]$/g, "");
      } catch {
        return "";
      }
    })
    .filter(Boolean);
}

/** Exact hostname match. A suffix match would let evil-example.com borrow the token. */
export function isAllowedMapHost(hostname: string, allowedRaw: string | undefined): boolean {
  const host = hostname.trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (!host) return false;
  return ALWAYS_ALLOWED_HOSTS.includes(host) || parseAllowedHosts(allowedRaw).includes(host);
}

export type TokenCheck =
  | { level: "ok" }
  | { level: "missing"; message: string }
  | { level: "secret"; message: string }
  | { level: "malformed"; message: string };

/**
 * A secret (sk.) token must never reach the bundle, where anyone can read it;
 * the build refuses one. Missing only warns: the maps fall back to OSM.
 */
export function checkMapboxToken(token: string | undefined): TokenCheck {
  const t = (token ?? "").trim();
  if (!t) {
    return {
      level: "missing",
      message: "VITE_MAPBOX_TOKEN is not set; every map will use OpenStreetMap's public tile server.",
    };
  }
  if (t.startsWith("sk.")) {
    return {
      level: "secret",
      message: "VITE_MAPBOX_TOKEN is a SECRET token (sk.*). It would ship in the public bundle; use the public pk.* token.",
    };
  }
  if (!t.startsWith("pk.")) {
    return { level: "malformed", message: "VITE_MAPBOX_TOKEN does not look like a Mapbox public token (pk.*); ignoring it." };
  }
  return { level: "ok" };
}

function osmSource(fallbackReason?: string): TileSource {
  return {
    provider: "osm",
    url: OSM_TILE_URL,
    options: { ...SHARED_OPTIONS, attribution: OSM_ATTRIBUTION },
    fallbackReason,
  };
}

export function resolveTileSource(variant: MapVariant, env: TileEnv, hostname: string): TileSource {
  const custom = env.VITE_TILE_URL?.trim();
  if (custom) {
    return { provider: "custom", url: custom, options: { ...SHARED_OPTIONS, attribution: OSM_ATTRIBUTION } };
  }

  const token = env.VITE_MAPBOX_TOKEN?.trim();
  const check = checkMapboxToken(token);
  if (check.level === "missing") return osmSource();
  if (check.level !== "ok") return osmSource(check.message);
  if (!isAllowedMapHost(hostname, env.VITE_ALLOWED_MAP_HOSTS)) {
    return osmSource(
      `Mapbox tiles are not allowed on host "${hostname}" (add it to VITE_ALLOWED_MAP_HOSTS); using OpenStreetMap.`,
    );
  }

  return {
    provider: "mapbox",
    // 512 px tiles + zoomOffset -1: a quarter of the requests of 256 px. {r} fetches @2x tiles
    // on a retina screen; detectRetina would fetch the next zoom level instead (4x the calls).
    url:
      `https://api.mapbox.com/styles/v1/${MAPBOX_STYLE[variant]}/tiles/512/{z}/{x}/{y}{r}` +
      `?access_token=${encodeURIComponent(token as string)}`,
    options: {
      ...SHARED_OPTIONS,
      tileSize: 512,
      zoomOffset: -1,
      // Map zoom 0 would ask for tile zoom -1, which does not exist.
      minZoom: 1,
      attribution: MAPBOX_ATTRIBUTION,
    },
  };
}

/** The night style exists only on Mapbox; OSM and a custom server have one look. */
export function hasNightVariant(env: TileEnv, hostname: string): boolean {
  return resolveTileSource("night", env, hostname).provider === "mapbox";
}
