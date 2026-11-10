import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.CATMOD_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ["maplibre-gl"] },
  worker: { format: "es" },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes("maplibre-gl")) return "map";
          if (id.includes("@deck.gl") || id.includes("@luma.gl") || id.includes("@loaders.gl")) return "deck";
          if (id.includes("echarts") || id.includes("zrender")) return "charts";
          if (id.includes("apache-arrow")) return "arrow";
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": api,
      "/health": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
