import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { toast, Toaster } from "sonner";
import { router } from "./router";
import { clearLocalSession, initializeBrokerSignIn } from "./auth/msal";
import { captureBrokerInviteFromUrl } from "./auth/brokerInvite";
import { rememberSignInNotice } from "./auth/signInNotice";
import { brokerAuthEnabled, loadStaffSignIn } from "./auth/staffSignIn";
import { DENIED_SEARCH, SIGN_IN_PATH, isDeniedSignInUrl } from "./api/client";
import { errorCode, formatError } from "./lib/errors";
import { queryClient, setNoAccessHandler } from "./lib/queryClient";
import { captureTenantSlugFromUrl } from "./lib/tenant";
import { primeBrand } from "./lib/brand";
import { installMediaInteractionGuards } from "./lib/mediaInteractions";
import "./styles.css";

installMediaInteractionGuards();

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

/** Refusals of a Microsoft sign-in that the sign-in page explains itself. */
const EXPLAINED_REFUSALS = new Set(["invitation_invalid", "sign_in_method_disabled"]);

/** The broker app's sign-in: read how this host's staff sign in, then finish a
 *  Microsoft redirect or restore the session, all before the router decides
 *  what to render. The employee and HR portals have their own sign-in. */
async function startBrokerSignIn() {
  const path = window.location.pathname;
  if (path.startsWith("/portal/") || path.startsWith("/hr/")) return;
  // Before MSAL reads the address: the invitation token leaves the URL first.
  captureBrokerInviteFromUrl();
  await loadStaffSignIn();
  if (!brokerAuthEnabled()) return;
  try {
    await initializeBrokerSignIn();
  } catch (err) {
    // Non-fatal: render the app and let the user retry.
    console.error("Broker sign-in initialization failed");
    const code = errorCode(err);
    if (code === "no_access" || code === "invitation_expired") {
      window.history.replaceState(null, "", "/sign-in?denied=1");
    } else if (code && EXPLAINED_REFUSALS.has(code)) {
      rememberSignInNotice({ code, message: formatError(err) });
      window.history.replaceState(null, "", SIGN_IN_PATH);
    } else {
      toast.error("Sign-in is currently unavailable. Please try again.");
    }
  }
}

async function bootstrap() {
  // Single-host deployments carry the tenant as `?company=<slug>` on the entry
  // link. Consume it before the router renders, so the very first API call
  // already knows which tenant it is for.
  captureTenantSlugFromUrl();
  // After the tenant is known, so a portal reads its company's brand.
  primeBrand();
  await startBrokerSignIn();

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

  ReactDOM.createRoot(document.getElementById("root")!).render(root);
}

bootstrap();
