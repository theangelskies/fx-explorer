import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser only ever talks to Vite (port 5173). Requests to /v1/* are forwarded to the API
// on port 8000, so the app and the API look like one site: no CORS setup needed.
const apiProxy = { "/v1": { target: "http://localhost:8000" } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: apiProxy },
  preview: { port: 4173, proxy: apiProxy },
});
