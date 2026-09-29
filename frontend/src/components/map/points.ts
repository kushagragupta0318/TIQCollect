/**
 * Point layers that cluster only when crowded: above CLUSTER_ABOVE points a map draws cluster
 * bubbles, at or below it every point. Used by the live map, the agency directory and coverage.
 */
import L from "leaflet";
import "./leafletGlobal";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import { escapeHtml } from "@/lib/html";

export const CLUSTER_ABOVE = 40;

const CLUSTER_OPTIONS: L.MarkerClusterGroupOptions = {
  showCoverageOnHover: false,
  // Adds a large set in slices so the page stays responsive while it loads.
  chunkedLoading: true,
  // Street level shows every point: the live map focuses an agent or an SOS at zoom 15.
  disableClusteringAtZoom: 15,
  maxClusterRadius: 50,
};

export function shouldCluster(count: number): boolean {
  return count > CLUSTER_ABOVE;
}

function groupFor(count: number): L.FeatureGroup {
  return shouldCluster(count) ? L.markerClusterGroup(CLUSTER_OPTIONS) : L.featureGroup();
}

export function isClusterGroup(group: L.Layer): group is L.MarkerClusterGroup {
  return group instanceof L.MarkerClusterGroup;
}

export interface MapPoint {
  lat: number;
  lon: number;
  /** Plain text; escaped here before it reaches the tooltip's innerHTML. */
  label: string;
}

const POINT_STYLE: L.CircleMarkerOptions = { radius: 7, color: "#1677FF", weight: 2, fillColor: "#1677FF", fillOpacity: 0.5 };

/** Plots labelled points, fits the view to them, and returns a remover (MapCanvas onReady). */
export function drawPoints(map: L.Map, points: readonly MapPoint[], maxZoom = 10): () => void {
  const group = groupFor(points.length);
  const layers = points.map((p) =>
    L.circleMarker([p.lat, p.lon], POINT_STYLE).bindTooltip(escapeHtml(p.label), { direction: "top" }),
  );
  if (isClusterGroup(group)) group.addLayers(layers);
  else layers.forEach((l) => group.addLayer(l));
  group.addTo(map);
  if (points.length) {
    map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon] as [number, number])).pad(0.25), { maxZoom });
  }
  return () => {
    group.remove();
  };
}

/**
 * A marker set that changes membership over time (the live map's agents). It clusters only
 * while it holds more than CLUSTER_ABOVE markers, and switches group type when it crosses.
 */
export class AdaptivePointGroup {
  private group: L.FeatureGroup;
  private readonly members = new Set<L.Layer>();

  constructor(private readonly map: L.Map) {
    this.group = groupFor(0).addTo(map);
  }

  get clustered(): boolean {
    return isClusterGroup(this.group);
  }

  has(layer: L.Layer): boolean {
    return this.members.has(layer);
  }

  add(layer: L.Layer): void {
    if (this.members.has(layer)) return;
    this.members.add(layer);
    if (!this.rebuildIfCrossed()) this.group.addLayer(layer);
  }

  delete(layer: L.Layer): void {
    if (!this.members.delete(layer)) return;
    if (!this.rebuildIfCrossed()) this.group.removeLayer(layer);
  }

  remove(): void {
    this.group.clearLayers();
    this.group.remove();
    this.members.clear();
  }

  private rebuildIfCrossed(): boolean {
    if (shouldCluster(this.members.size) === this.clustered) return false;
    this.group.clearLayers();
    this.group.remove();
    this.group = groupFor(this.members.size);
    const layers = [...this.members];
    if (isClusterGroup(this.group)) this.group.addLayers(layers);
    else layers.forEach((l) => this.group.addLayer(l));
    this.group.addTo(this.map);
    return true;
  }
}
