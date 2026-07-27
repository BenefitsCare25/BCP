import { QueryCache, QueryClient, MutationCache } from "@tanstack/react-query";
import { NoAccessError, UnauthorizedError } from "@/api/client";
import { PortalUnauthorizedError } from "@/api/portalClient";
import { errorStatus, formatError } from "@/lib/errors";
import { notify } from "@/stores/notifications";

/** Set by main.tsx once the router exists — the router imports this module, so
 * it can't be imported back here without a cycle. */
let onNoAccess: (() => void) | null = null;

export function setNoAccessHandler(handler: () => void): void {
  onNoAccess = handler;
}

function reportError(scope: "query" | "mutation", error: unknown) {
  // UnauthorizedError is already handled by the API client (a sign-in
  // redirect is in flight). Don't double-surface it as a toast.
  if (error instanceof UnauthorizedError) return;
  if (error instanceof PortalUnauthorizedError) return;
  // The account isn't provisioned on this platform — the sign-in page's own
  // banner IS the message, so don't also log a notification they can't act on.
  if (error instanceof NoAccessError) {
    onNoAccess?.();
    return;
  }
  console.error(`[${scope}]`, error);
  // Goes to the top-bar notification centre, NOT a toast: one backend fault
  // fails every in-flight query with the same message, and a stack of floating
  // duplicates covered the navigation. The store dedupes by message, so this
  // alerts once however many queries failed.
  notify({ message: formatError(error), tone: "error" });
}

/** Retry only what a retry can fix. A 4xx is the server's considered answer;
 * repeating it just doubles the latency and the error noise. */
function retryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof NoAccessError || error instanceof UnauthorizedError) {
    return false;
  }
  const status = errorStatus(error);
  if (status !== null && status >= 400 && status < 500) return false;
  return failureCount < 1;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: retryQuery },
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
