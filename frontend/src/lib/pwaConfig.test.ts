/**
 * I01 — the service worker's safety properties. The one that matters most:
 * it must never stand between the app and /api, and above all not the SSE
 * stream, which stalls when a worker proxies it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { SW_NAVIGATION_DENYLIST, pwaOptions } from "./pwaConfig";

const workbox = pwaOptions.workbox!;
const manifest = pwaOptions.manifest as {
  theme_color: string;
  background_color: string;
  icons: { src: string; sizes: string; purpose?: string }[];
};
const FRONTEND = join(__dirname, "..", "..");

/**
 * Whether the generated SW answers a NAVIGATION to `path` with the precached
 * index.html. The same test workbox-routing's NavigationRoute._match runs:
 * pathname + search against the denylist, then the allowlist (default
 * /./, which the config does not override).
 */
function fallbackServes(path: string): boolean {
  expect(workbox.navigateFallbackAllowlist).toBeUndefined();
  const url = new URL(path, "https://tiqcollect.example");
  const pathnameAndSearch = url.pathname + url.search;
  const denylist = workbox.navigateFallbackDenylist ?? [];
  return !denylist.some((re) => re.test(pathnameAndSearch));
}

describe("navigation fallback", () => {
  it("is configured with the exported denylist, to index.html", () => {
    expect(workbox.navigateFallbackDenylist).toBe(SW_NAVIGATION_DENYLIST);
    expect(workbox.navigateFallback).toBe("index.html");
  });

  it.each([
    "/api",
    "/api/",
    "/api?x=1",
    "/api/v1/auth/login",
    "/api/v1/events/stream",
    "/api/v1/events/stream?token=abc",
    "/api/field-ops/cases",
    "/ws",
    "/ws/agent-location",
    "/docs",
    "/docs/",
    "/docs?x=1",
    "/redoc",
    "/openapi.json",
    "/collection_dashboard/",
    "/collection_dashboard/index.html",
  ])("is denied for %s — the network answers it, never the SW", (path) => {
    expect(fallbackServes(path)).toBe(false);
  });

  it.each([
    "/",
    "/login",
    "/quick-login?token=x",
    "/simulator",
    "/agent/home",
    "/agent/cases/42",
    "/agent/visit/42",
    "/manager/overview",
    "/manager/analytics?month=2026-09",
    // Look-alikes: a prefix only counts on a segment boundary.
    "/apiary",
    "/wsx",
    "/documents",
  ])("serves the app shell for the app route %s", (path) => {
    expect(fallbackServes(path)).toBe(true);
  });
});

describe("runtime caching", () => {
  it("has no runtime routes at all — no API response is ever cached", () => {
    expect(workbox.runtimeCaching ?? []).toEqual([]);
  });

  it("has no rule that would match an API or stream URL, should one be added", () => {
    const urls = ["/api/v1/manager/dashboard", "/api/v1/events/stream"].map(
      (p) => new URL(p, "https://tiqcollect.example"),
    );
    for (const rule of workbox.runtimeCaching ?? []) {
      const p = rule.urlPattern;
      for (const url of urls) {
        const hit =
          p instanceof RegExp ? p.test(url.href) :
          typeof p === "string" ? url.href.startsWith(new URL(p, url).href) :
          true; // a function pattern cannot be proven safe here — fail it
        expect(hit, `runtime rule ${String(p)} matches ${url.pathname}`).toBe(false);
      }
    }
  });

  it("precaches the app shell and not the static dashboard beside it", () => {
    expect(workbox.globPatterns).toEqual(["**/*.{js,css,html}"]);
    expect(workbox.globIgnores).toContain("collection_dashboard/**");
  });
});

describe("registration", () => {
  it("is off in the dev server", () => {
    expect(pwaOptions.devOptions?.enabled).toBe(false);
  });

  it("prompts for an update rather than reloading under a half-filled form", () => {
    expect(pwaOptions.registerType).toBe("prompt");
    // autoUpdate's skipWaiting/clientsClaim would take over mid-visit.
    expect(workbox.skipWaiting).toBeUndefined();
    expect(workbox.clientsClaim).toBeUndefined();
  });
});

describe("manifest", () => {
  it("installs as TIQCollect, standalone, from /", () => {
    expect(pwaOptions.manifest).toMatchObject({ name: "TIQCollect", start_url: "/", display: "standalone" });
  });

  it("ships a 192, a 512 and a maskable 512 that exist in public/ at those sizes", () => {
    const pngSize = (file: string) => {
      const b = readFileSync(join(FRONTEND, "public", file));
      expect(b.subarray(1, 4).toString("ascii"), `${file} is a PNG`).toBe("PNG");
      return `${b.readUInt32BE(16)}x${b.readUInt32BE(20)}`; // IHDR width x height
    };
    for (const icon of manifest.icons) expect(pngSize(icon.src), icon.src).toBe(icon.sizes);
    const sizes = manifest.icons.map((i) => `${i.sizes}${i.purpose ? `:${i.purpose}` : ""}`);
    expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512", "512x512:maskable"]));
  });

  it("takes theme_color from index.html's theme-color meta", () => {
    const html = readFileSync(join(FRONTEND, "index.html"), "utf8");
    const meta = html.match(/<meta name="theme-color" content="(#[0-9A-Fa-f]{6})"/);
    expect(meta?.[1]).toBe(manifest.theme_color);
  });

  it("takes background_color from what hsl(var(--background)) renders to", () => {
    const css = readFileSync(join(FRONTEND, "src", "index.css"), "utf8");
    const m = css.match(/--background:\s*(\d+)\s+(\d+)%\s+(\d+)%/);
    expect(m).not.toBeNull();
    const [h, s, l] = [Number(m![1]), Number(m![2]) / 100, Number(m![3]) / 100];
    // CSS Color 4 hsl() → sRGB.
    const k = (n: number) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    const hex = "#" + [f(0), f(8), f(4)]
      .map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("").toUpperCase();
    expect(manifest.background_color).toBe(hex);
  });
});
