import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.CATMOD_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": api,
      "/health": api,
    },
  },
});
