// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import L from "leaflet";
import { addBaseTiles, tileSourceFor } from "./baseTiles";

const TOKEN = "pk.eyJ1IjoidGlxIiwiYSI6ImFiYyJ9.test-signature";

function makeMap(): L.Map {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return L.map(el).setView([28.4595, 77.0266], 12);
}

function tileLayers(map: L.Map): L.TileLayer[] {
  const out: L.TileLayer[] = [];
  map.eachLayer((l) => {
    if (l instanceof L.TileLayer) out.push(l);
  });
  return out;
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("addBaseTiles", () => {
  it("adds one Mapbox layer plus the Mapbox wordmark, and removes both", () => {
    const map = makeMap();
    const remove = addBaseTiles(map, "day", { VITE_MAPBOX_TOKEN: TOKEN });
    const layers = tileLayers(map);
    expect(layers).toHaveLength(1);
    expect((layers[0] as unknown as { _url: string })._url).toContain("api.mapbox.com/styles/v1/mapbox/streets-v12");
    expect(map.getContainer().querySelector(".tiq-mapbox-wordmark img")).not.toBeNull();
    expect(map.getContainer().querySelector(".leaflet-control-attribution")?.textContent).toContain("Improve this map");

    remove();
    expect(tileLayers(map)).toHaveLength(0);
    expect(map.getContainer().querySelector(".tiq-mapbox-wordmark")).toBeNull();
    map.remove();
  });

  it("adds OSM without a wordmark when there is no token", () => {
    const map = makeMap();
    addBaseTiles(map, "day", {});
    const layers = tileLayers(map);
    expect(layers).toHaveLength(1);
    expect((layers[0] as unknown as { _url: string })._url).toContain("tile.openstreetmap.org");
    expect(map.getContainer().querySelector(".tiq-mapbox-wordmark")).toBeNull();
    map.remove();
  });
});

describe("tileSourceFor", () => {
  it("warns once per fallback reason, not on every map", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // jsdom's page host is localhost; a secret token is the fallback we can trigger here.
    const env = { VITE_MAPBOX_TOKEN: "sk.secret-value" };
    tileSourceFor("day", env);
    tileSourceFor("day", env);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).not.toContain("sk.secret-value");
  });
});
