import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Ports follow the collections platform's numbering (command-center 5173/8000,
// digi-tele 5273/8100, tech-ops 5373/8200). Standalone TIQCollect used
// 5173/8000, which collide with Command Center now that this app lives inside
// the platform.
//
// The backend target defaults to :8400 and can be overridden per machine with
// VITE_API_TARGET in a .env.local — carried over from standalone TIQCollect,
// where the same knob pointed at :8001.
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
