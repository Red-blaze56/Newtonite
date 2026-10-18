import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser calls /api/...; Vite forwards it to the FastAPI server. Same origin, so no CORS
// in development. In Docker, nginx does the same forwarding (see nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_URL ?? "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
