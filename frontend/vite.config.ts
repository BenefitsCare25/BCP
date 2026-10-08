import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// A local API serves every firm, picking one from the Host header the browser
// used (`<firm-slug>.localhost` → that firm; backend `core/tenant_resolution.py`).
const LOCAL_API = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?\/?$/i;

export default defineConfig(({ mode }) => {
  const localEnv = loadEnv(mode, process.cwd(), "INSPRO_");
  const apiTarget =
    process.env.INSPRO_DEV_API_TARGET ?? localEnv.INSPRO_DEV_API_TARGET ?? "http://localhost:8000";
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      // Allow per-firm hosts in dev: `brokera.localhost:5173` (browsers resolve
      // *.localhost → loopback), plus the legacy `{slug}.hr.localhost` /
      // `{slug}.portal.localhost` subdomain shapes.
      allowedHosts: [".localhost"],
      proxy: {
        "/api": {
          target: apiTarget,
          // Keep the browser's Host for a local API so the backend resolves the
          // firm from it (and the cookie same-origin check sees the page's own
          // host). A remote target needs its own Host for virtual hosting.
          changeOrigin: !LOCAL_API.test(apiTarget),
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          // Heavy infra goes into its own long-lived chunks so the app shell
          // stays small and unrelated deploys don't bust the user's cache.
          manualChunks: {
            react: ["react", "react-dom"],
            msal: ["@azure/msal-browser", "@azure/msal-react"],
            tanstack: [
              "@tanstack/react-router",
              "@tanstack/react-query",
            ],
          },
        },
      },
    },
  };
});
