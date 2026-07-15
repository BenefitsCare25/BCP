import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryCache, QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { MsalProvider } from "@azure/msal-react";
import { toast, Toaster } from "sonner";
import { router } from "./router";
import { ENTRA_ENABLED, getMsal, initializeMsal } from "./auth/msal";
import { UnauthorizedError } from "./api/client";
import { PortalUnauthorizedError } from "./api/portalClient";
import { formatError } from "./lib/errors";
import "./styles.css";

function reportError(scope: "query" | "mutation", error: unknown) {
  // UnauthorizedError is already handled by the API client (a sign-in
  // redirect is in flight). Don't double-surface it as a toast.
  if (error instanceof UnauthorizedError) return;
  if (error instanceof PortalUnauthorizedError) return;
  console.error(`[${scope}]`, error);
  toast.error(formatError(error));
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
    mutations: { retry: 0 },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      // Queries that render their own error state (e.g. the portal statement's
      // "no active coverage" empty state) opt out of the global toast via meta.
      if (query.meta?.localErrorHandling) return;
      reportError("query", error);
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      // Mutations that own their error UX (e.g. a structured-409 dialog on
      // enrollment submit) opt out of the global toast via meta.
      if (mutation.meta?.localErrorHandling) return;
      reportError("mutation", error);
    },
  }),
});

async function bootstrap() {
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
