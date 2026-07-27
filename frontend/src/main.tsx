import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { MsalProvider } from "@azure/msal-react";
import { toast, Toaster } from "sonner";
import { router } from "./router";
import { ENTRA_ENABLED, getMsal, initializeMsal } from "./auth/msal";
import { NO_ACCESS_PATH } from "./api/client";
import { queryClient, setNoAccessHandler } from "./lib/queryClient";
import { captureTenantSlugFromUrl } from "./lib/tenant";
import "./styles.css";

// A query failing with NoAccessError means the signed-in account isn't
// provisioned (or was just disabled mid-session). The query client can't
// import the router, so the navigation is injected here.
setNoAccessHandler(() => {
  if (window.location.pathname === NO_ACCESS_PATH) return;
  void router.navigate({ to: NO_ACCESS_PATH, replace: true });
});

async function bootstrap() {
  // Single-host deployments carry the tenant as `?company=<slug>` on the entry
  // link. Consume it before the router renders, so the very first API call
  // already knows which tenant it is for.
  captureTenantSlugFromUrl();

  // MSAL v3 requires `initialize()` BEFORE any other call. Doing it here
  // (rather than inside a useEffect) ensures the redirect-response from
  // /auth/callback is consumed before the router decides what to render.
  if (ENTRA_ENABLED) {
    try {
      await initializeMsal();
    } catch (err) {
      // Non-fatal: render the app and let the user retry. A failed init
      // typically means a bad VITE_ENTRA_* config; surface it loudly.
      console.error("MSAL initialise failed", err);
      toast.error(
        "Sign-in is currently unavailable — check VITE_ENTRA_* config.",
      );
    }
  }

  const msal = getMsal();

  const root = (
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <Toaster richColors position="top-right" />
      </QueryClientProvider>
    </React.StrictMode>
  );

  ReactDOM.createRoot(document.getElementById("root")!).render(
    msal ? <MsalProvider instance={msal}>{root}</MsalProvider> : root,
  );
}

bootstrap();
