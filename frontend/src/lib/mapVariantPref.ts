import type { MapVariant } from "./mapTiles";

const KEY = "tiq.liveMap.variant";

// A per-viewer convenience. Storage can be missing or throw (private mode, blocked site
// data), so both sides fail soft to the day map.
export function readMapVariant(): MapVariant {
  try {
    return localStorage.getItem(KEY) === "night" ? "night" : "day";
  } catch {
    return "day";
  }
}

export function writeMapVariant(variant: MapVariant): void {
  try {
    localStorage.setItem(KEY, variant);
  } catch {
    // Not remembered; the toggle still works for this page view.
  }
}
