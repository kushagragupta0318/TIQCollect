import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Ports follow the collections platform's numbering (command-center 5173/8000,
  // digi-tele 5273/8100, tech-ops 5373/8200). Standalone TIQCollect used
  // 5173/8000, which collide with Command Center now that this app lives inside
  // the platform.
  server: {
    port: 5473,
    proxy: {
      "/api": {
        target: "http://localhost:8400",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8400",
        ws: true,
      },
    },
  },
});
