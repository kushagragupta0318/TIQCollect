/**
 * Map constants, kept out of MapCanvas.tsx deliberately.
 *
 * A file that exports both a component and a constant breaks Vite's fast
 * refresh (react-refresh/only-export-components): editing the constant forces a
 * full reload instead of a hot swap. One extra file is cheaper than losing
 * component state on every save.
 */
import type L from "leaflet";

/** Gurugram — the agency's base, and a sane view before any data loads. */
export const DEFAULT_CENTRE: L.LatLngExpression = [28.4595, 77.0266];
