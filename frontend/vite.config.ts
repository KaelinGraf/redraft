import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// s6-ui.md §7.1: dev server proxies /api to the FastAPI backend on 127.0.0.1:8420 (redraft
// ui's own default host/port, src/redraft/ui/app.py:main) so the browser only ever talks to
// one origin -- zero CORS configuration needed on either side.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8420",
    },
  },
});
