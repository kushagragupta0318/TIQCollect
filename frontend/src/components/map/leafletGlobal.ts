import L from "leaflet";

// leaflet.markercluster is a UMD build that extends a global `L`; the Leaflet build Vite
// imports never sets one. Imported before "leaflet.markercluster" so the plugin extends this L.
(globalThis as unknown as { L: typeof L }).L = L;
