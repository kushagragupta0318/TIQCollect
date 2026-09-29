import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import fs from "fs";
import path from "path";
import { pwaOptions } from "./src/lib/pwaConfig";
import { checkMapboxToken } from "./src/lib/mapTiles";

// 2026-09-24 (I01) — PWA: manifest, icons and an app-shell service worker,
// options in src/lib/pwaConfig.ts (where the test that pins them lives). The
// SW exists only in `vite build` output; the dev server below is untouched.
// Installing on a phone: frontend/README.md.

// Ports follow the collections platform's numbering (command-center 5173/8000,
// digi-tele 5273/8100, tech-ops 5373/8200). Standalone TIQCollect used
// 5173/8000, which collide with Command Center now that this app lives inside
// the platform.
//
// The backend target defaults to :8400 and can be overridden per machine with
// VITE_API_TARGET in a .env.local — carried over from standalone TIQCollect,
// where the same knob pointed at :8001.
//
// HTTPS is opt-in: `HTTPS=1 npm run dev`.
//
// The agent flow needs a secure context — navigator.geolocation, camera and
// microphone are all gated behind one, and plain HTTP on a LAN IP is not a
// secure context (only https:// and localhost are). Testing Record Visit or
// check-in on a real phone therefore requires this.
//
// We use our own certificate rather than @vitejs/plugin-basic-ssl because that
// plugin issues a cert for CN=example.org whose SAN covers only localhost and
// 127.0.0.1. Reaching the dev server from a phone means hitting the LAN IP, and
// a cert that does not name that IP is a hostname mismatch — which mobile
// browsers often will not let you click past on a bare IP address.
//
// Regenerate for a new LAN IP (see docs/development-guide.md):
//   LAN=$(hostname -I | awk '{print $1}')
//   openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
//     -keyout certs/dev-key.pem -out certs/dev-cert.pem -subj "/CN=$LAN" \
//     -addext "subjectAltName=IP:$LAN,IP:127.0.0.1,DNS:localhost"
const useHttps = process.env.HTTPS === "1" || process.env.HTTPS === "true";
const certDir = path.resolve(__dirname, "certs");
const keyFile = path.join(certDir, "dev-key.pem");
const certFile = path.join(certDir, "dev-cert.pem");
const haveCert = fs.existsSync(keyFile) && fs.existsSync(certFile);

if (useHttps && !haveCert) {
  // Fail loudly rather than silently serving HTTP when HTTPS was asked for —
  // a silent downgrade is how you end up debugging "why is geolocation null".
  throw new Error(
    "HTTPS=1 but frontend/certs/dev-{key,cert}.pem are missing. " +
      "Generate them with the openssl command in this file's comment.",
  );
}

// The map token is inlined into the bundle (lib/mapTiles.ts). A secret sk.* token must never
// be, so it stops dev and build alike; a production build without one only warns, since every
// map then falls back to OpenStreetMap's public server.
function mapboxTokenCheck(token: string | undefined): Plugin {
  return {
    name: "tiq-mapbox-token-check",
    configResolved(config) {
      const check = checkMapboxToken(token);
      if (check.level === "secret") throw new Error(check.message);
      if (check.level !== "ok" && config.command === "build" && config.mode === "production") {
        config.logger.warn(`WARNING: ${check.message}`);
      }
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, "");
  const apiTarget = env.VITE_API_TARGET || "http://localhost:8400";

  return {
    plugins: [react(), VitePWA(pwaOptions), mapboxTokenCheck(env.VITE_MAPBOX_TOKEN)],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5473,
      // Fail rather than silently sliding to 5474 when the port is taken — a
      // moved port is indistinguishable from "the server didn't start" once you
      // are typing a URL into a phone.
      strictPort: true,
      https: useHttps ? { key: fs.readFileSync(keyFile), cert: fs.readFileSync(certFile) } : undefined,
      // Listen on all interfaces so a phone on the same Wi-Fi can load the app
      // for real-device testing (http://<your-lan-ip>:5473). The /api proxy below
      // still runs server-side, so requests stay same-origin from the phone's
      // point of view — no CORS change needed on the backend, with or without
      // HTTPS.
      host: true,
      // 2026-09-24 — file-change events do not cross a Windows → Docker bind
      // mount for Vite's watcher (measured: an edit to a served module was
      // still absent 3 s later, and a new route stayed missing until the
      // container restarted, so the dev app silently ran stale code).
      // Uvicorn's reloader in the api container is unaffected because
      // watchfiles falls back to polling on its own; Vite's does not.
      // docker-compose.yml sets VITE_WATCH_POLLING=1 for the web container; a
      // native `npm run dev` keeps the cheaper event-based watcher.
      watch:
        process.env.VITE_WATCH_POLLING === "1"
          ? { usePolling: true, interval: 300 }
          : undefined,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
        "/ws": {
          target: apiTarget.replace(/^http/, "ws"),
          ws: true,
        },
      },
    },
  };
});
