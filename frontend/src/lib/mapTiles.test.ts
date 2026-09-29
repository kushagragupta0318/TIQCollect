import { describe, expect, it } from "vitest";
import {
  MAPBOX_ATTRIBUTION,
  OSM_TILE_URL,
  checkMapboxToken,
  hasNightVariant,
  isAllowedMapHost,
  parseAllowedHosts,
  resolveTileSource,
} from "./mapTiles";

const TOKEN = "pk.eyJ1IjoidGlxIiwiYSI6ImFiYyJ9.test-signature";

describe("resolveTileSource", () => {
  it("uses Mapbox streets with 512 px @2x-capable tiles when a public token is set on an allowed host", () => {
    const s = resolveTileSource("day", { VITE_MAPBOX_TOKEN: TOKEN }, "localhost");
    expect(s.provider).toBe("mapbox");
    expect(s.url).toBe(
      `https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/512/{z}/{x}/{y}{r}?access_token=${encodeURIComponent(TOKEN)}`,
    );
    expect(s.options).toMatchObject({ tileSize: 512, zoomOffset: -1, minZoom: 1, updateWhenIdle: true, keepBuffer: 4 });
    expect(s.options.detectRetina).toBeUndefined();
    expect(s.fallbackReason).toBeUndefined();
  });

  it("uses the dark style for the night variant", () => {
    const s = resolveTileSource("night", { VITE_MAPBOX_TOKEN: TOKEN }, "127.0.0.1");
    expect(s.url).toContain("/styles/v1/mapbox/dark-v11/tiles/512/");
  });

  it("carries Mapbox's three required attribution links", () => {
    const s = resolveTileSource("day", { VITE_MAPBOX_TOKEN: TOKEN }, "localhost");
    expect(s.options.attribution).toBe(MAPBOX_ATTRIBUTION);
    expect(MAPBOX_ATTRIBUTION).toContain('href="https://www.mapbox.com/about/maps/"');
    expect(MAPBOX_ATTRIBUTION).toContain("&copy; Mapbox");
    expect(MAPBOX_ATTRIBUTION).toContain('href="https://www.openstreetmap.org/copyright"');
    expect(MAPBOX_ATTRIBUTION).toContain('href="https://apps.mapbox.com/feedback/"');
    expect(MAPBOX_ATTRIBUTION).toContain("Improve this map");
  });

  it("falls back to OSM silently when no token is set", () => {
    for (const token of [undefined, "", "   "]) {
      const s = resolveTileSource("day", { VITE_MAPBOX_TOKEN: token }, "localhost");
      expect(s.provider).toBe("osm");
      expect(s.url).toBe(OSM_TILE_URL);
      expect(s.options.attribution).toContain("OpenStreetMap");
      expect(s.options.attribution).not.toContain("Mapbox");
      expect(s.fallbackReason).toBeUndefined();
    }
  });

  it("falls back to OSM, with a reason, on a host outside the allowlist", () => {
    const s = resolveTileSource("day", { VITE_MAPBOX_TOKEN: TOKEN }, "fieldops.example.net");
    expect(s.provider).toBe("osm");
    expect(s.url).not.toContain(TOKEN);
    expect(s.fallbackReason).toContain('"fieldops.example.net"');
  });

  it("uses Mapbox on a host listed in VITE_ALLOWED_MAP_HOSTS", () => {
    const env = { VITE_MAPBOX_TOKEN: TOKEN, VITE_ALLOWED_MAP_HOSTS: "https://fieldops.example.net, 192.168.1.20" };
    expect(resolveTileSource("day", env, "fieldops.example.net").provider).toBe("mapbox");
    expect(resolveTileSource("day", env, "192.168.1.20").provider).toBe("mapbox");
  });

  it("refuses a secret or malformed token, or an uninterpolated ${VAR}, and never puts it in a URL", () => {
    for (const token of ["sk.eyJsecret.value", "not-a-token", "${VITE_MAPBOX_TOKEN}"]) {
      const s = resolveTileSource("day", { VITE_MAPBOX_TOKEN: token }, "localhost");
      expect(s.provider).toBe("osm");
      expect(s.url).not.toContain(token);
      expect(s.fallbackReason).toBeTruthy();
    }
  });

  it("lets a self-hosted VITE_TILE_URL win over Mapbox", () => {
    const s = resolveTileSource(
      "day",
      { VITE_TILE_URL: "https://tiles.internal/{z}/{x}/{y}.png", VITE_MAPBOX_TOKEN: TOKEN },
      "localhost",
    );
    expect(s.provider).toBe("custom");
    expect(s.url).toBe("https://tiles.internal/{z}/{x}/{y}.png");
    expect(s.url).not.toContain(TOKEN);
  });
});

describe("isAllowedMapHost", () => {
  it("always allows a developer's own machine", () => {
    for (const host of ["localhost", "127.0.0.1", "[::1]", "LOCALHOST"]) {
      expect(isAllowedMapHost(host, undefined)).toBe(true);
    }
  });

  it("matches listed hosts exactly, never by suffix or prefix", () => {
    const allowed = "fieldops.example.net";
    expect(isAllowedMapHost("fieldops.example.net", allowed)).toBe(true);
    expect(isAllowedMapHost("evil-fieldops.example.net", allowed)).toBe(false);
    expect(isAllowedMapHost("fieldops.example.net.evil.com", allowed)).toBe(false);
    expect(isAllowedMapHost("example.net", allowed)).toBe(false);
    expect(isAllowedMapHost("", allowed)).toBe(false);
    expect(isAllowedMapHost("fieldops.example.net", "${VITE_ALLOWED_MAP_HOSTS}")).toBe(false);
  });
});

describe("parseAllowedHosts", () => {
  it("accepts bare hosts, hosts with a port or path, and full URLs", () => {
    expect(
      parseAllowedHosts(" a.example.com ,https://B.example.com:8443/app, c.example.com:5473, d.example.com/x, [::2]:80,,"),
    ).toEqual(["a.example.com", "b.example.com", "c.example.com", "d.example.com", "::2"]);
  });

  it("returns nothing for an empty or missing list", () => {
    expect(parseAllowedHosts(undefined)).toEqual([]);
    expect(parseAllowedHosts("")).toEqual([]);
  });
});

describe("checkMapboxToken", () => {
  it("grades public, missing, secret and malformed tokens", () => {
    expect(checkMapboxToken(TOKEN).level).toBe("ok");
    expect(checkMapboxToken(undefined).level).toBe("missing");
    expect(checkMapboxToken("").level).toBe("missing");
    expect(checkMapboxToken("sk.abc").level).toBe("secret");
    expect(checkMapboxToken("abc").level).toBe("malformed");
    // backend/.env was written for the platform; an uninterpolated reference counts as unset.
    expect(checkMapboxToken("${VITE_MAPBOX_TOKEN}").level).toBe("malformed");
  });
});

describe("hasNightVariant", () => {
  it("is offered only when Mapbox tiles are in use", () => {
    expect(hasNightVariant({ VITE_MAPBOX_TOKEN: TOKEN }, "localhost")).toBe(true);
    expect(hasNightVariant({}, "localhost")).toBe(false);
    expect(hasNightVariant({ VITE_MAPBOX_TOKEN: TOKEN }, "other.example.net")).toBe(false);
    expect(hasNightVariant({ VITE_MAPBOX_TOKEN: TOKEN, VITE_TILE_URL: "https://t/{z}/{x}/{y}.png" }, "localhost")).toBe(false);
  });
});
