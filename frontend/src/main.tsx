import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { MsalProvider } from "@azure/msal-react";
import { toast, Toaster } from "sonner";
import { router } from "./router";
import {
  ENTRA_ENABLED,
  clearLocalSession,
  getMsal,
  initializeMsal,
} from "./auth/msal";
import { DENIED_SEARCH, SIGN_IN_PATH, isDeniedSignInUrl } from "./api/client";
import { queryClient, setNoAccessHandler } from "./lib/queryClient";
import { captureTenantSlugFromUrl } from "./lib/tenant";
import "./styles.css";

// A query failing with NoAccessError means the signed-in account isn't
// provisioned (or was just disabled mid-session). The query client can't
// import the router, so the navigation is injected here. Every in-flight query
// fails at once, so `bouncing` keeps that one event to one bounce.
let bouncing = false;
setNoAccessHandler(() => {
  if (bouncing || isDeniedSignInUrl()) return;
  bouncing = true;
  void (async () => {
    // Drop the local Microsoft session first, or the sign-in page's
    // "already signed in" guard sends them straight back into the app.
    await clearLocalSession();
    await router.navigate({
      to: SIGN_IN_PATH,
      search: DENIED_SEARCH,
      replace: true,
    });
    bouncing = false;
  })();
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
        <Toaster
          richColors
          position="top-right"
          offset={{ top: 64, right: 16 }}
          mobileOffset={{ top: 64, right: 16, left: 16 }}
        />
      </QueryClientProvider>
    </React.StrictMode>
  );

  ReactDOM.createRoot(document.getElementById("root")!).render(
    msal ? <MsalProvider instance={msal}>{root}</MsalProvider> : root,
  );
}

bootstrap();
