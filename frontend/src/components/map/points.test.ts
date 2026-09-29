// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import L from "leaflet";
import { AdaptivePointGroup, CLUSTER_ABOVE, drawPoints, shouldCluster, type MapPoint } from "./points";

const HOSTILE = `<img src=x onerror="alert(1)"><script>alert(2)</script>"Gurgaon' Zone`;

function makeMap(): L.Map {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return L.map(el, { maxZoom: 19 }).setView([28.4595, 77.0266], 10);
}

/** n points about 1 km apart on a line, close enough to cluster at zoom 10. */
function points(n: number, label = (i: number) => `Region ${i}`): MapPoint[] {
  return Array.from({ length: n }, (_, i) => ({ lat: 28.4 + i * 0.01, lon: 77.0, label: label(i) }));
}

function markers(map: L.Map): L.Marker[] {
  const out: L.Marker[] = [];
  map.eachLayer((l) => {
    if (l instanceof L.Marker && !(l as unknown as { _childCount?: number })._childCount) out.push(l);
  });
  return out;
}

function circles(map: L.Map): L.CircleMarker[] {
  const out: L.CircleMarker[] = [];
  map.eachLayer((l) => {
    if (l instanceof L.CircleMarker) out.push(l);
  });
  return out;
}

function clusterBubbles(map: L.Map): number {
  return map.getContainer().querySelectorAll(".marker-cluster").length;
}

beforeEach(() => {
  vi.spyOn(L.Map.prototype, "getSize").mockImplementation(() => L.point(800, 600));
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("shouldCluster", () => {
  it("clusters above 40 points only", () => {
    expect(CLUSTER_ABOVE).toBe(40);
    expect(shouldCluster(40)).toBe(false);
    expect(shouldCluster(41)).toBe(true);
  });
});

describe("drawPoints", () => {
  it("draws every point at or below the threshold, with the label as text", () => {
    const map = makeMap();
    const remove = drawPoints(map, points(3, (i) => (i === 0 ? HOSTILE : `Region ${i}`)));
    expect(circles(map)).toHaveLength(3);
    expect(clusterBubbles(map)).toBe(0);

    const first = circles(map).find((c) => c.getLatLng().lat === 28.4) as L.CircleMarker;
    first.openTooltip();
    const tooltip = map.getContainer().querySelector(".leaflet-tooltip") as HTMLElement;
    expect(tooltip.querySelector("script, img, [onerror]")).toBeNull();
    expect(tooltip.textContent).toBe(HOSTILE);

    remove();
    expect(circles(map)).toHaveLength(0);
    map.remove();
  });

  it("draws cluster bubbles, not 41 separate points, above the threshold", () => {
    const map = makeMap();
    drawPoints(map, points(CLUSTER_ABOVE + 1));
    expect(clusterBubbles(map)).toBeGreaterThan(0);
    expect(circles(map).length).toBeLessThan(CLUSTER_ABOVE + 1);
    map.remove();
  });
});

describe("AdaptivePointGroup", () => {
  it("switches to clustering when it crosses 40 members and back, keeping every member", () => {
    const map = makeMap();
    const group = new AdaptivePointGroup(map);
    const all = Array.from({ length: CLUSTER_ABOVE + 1 }, (_, i) => L.marker([28.4 + i * 0.01, 77.0]));

    all.slice(0, CLUSTER_ABOVE).forEach((m) => group.add(m));
    expect(group.clustered).toBe(false);
    expect(markers(map)).toHaveLength(CLUSTER_ABOVE);

    group.add(all[CLUSTER_ABOVE]);
    expect(group.clustered).toBe(true);
    expect(clusterBubbles(map)).toBeGreaterThan(0);
    expect(markers(map).length).toBeLessThan(CLUSTER_ABOVE + 1);

    group.delete(all[0]);
    expect(group.clustered).toBe(false);
    expect(clusterBubbles(map)).toBe(0);
    expect(markers(map)).toHaveLength(CLUSTER_ABOVE);
    expect(group.has(all[0])).toBe(false);
    expect(map.hasLayer(all[0])).toBe(false);
    map.remove();
  });

  it("adding a member twice or deleting a stranger changes nothing", () => {
    const map = makeMap();
    const group = new AdaptivePointGroup(map);
    const m = L.marker([28.4, 77.0]);
    group.add(m);
    group.add(m);
    group.delete(L.marker([28.5, 77.0]));
    expect(markers(map)).toHaveLength(1);
    map.remove();
  });
});
