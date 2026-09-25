import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig(({ mode }) => {
  const localEnv = loadEnv(mode, process.cwd(), "INSPRO_");
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      // Allow per-tenant subdomains in dev: `{slug}.hr.localhost:5173` /
      // `{slug}.portal.localhost:5173` (Chrome resolves *.localhost → 127.0.0.1),
      // so the multi-tenant subdomain is visible locally, not just in prod.
      allowedHosts: [".localhost"],
      proxy: {
        "/api": {
          target: process.env.INSPRO_DEV_API_TARGET ?? localEnv.INSPRO_DEV_API_TARGET ?? "http://localhost:8000",
          changeOrigin: true,
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
