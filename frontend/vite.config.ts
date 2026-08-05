import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";

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

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, "");
  const apiTarget = env.VITE_API_TARGET || "http://localhost:8400";

  return {
    plugins: [react()],
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
