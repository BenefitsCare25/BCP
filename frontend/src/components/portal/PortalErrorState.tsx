/** Shared inline error state for portal pages. Portal queries opt out of the
 * global error toast (`meta.localErrorHandling` + `retry: false`), so a failed
 * fetch must render here — distinct from the confident "no data" empty states,
 * which are reserved for real 404s.
 *
 * Made of the member's material like everything else on the surface: a mount,
 * and a 44px action rather than the shared 32px Button. */
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Mount } from "./leaf/Mount";
import { Action } from "./leaf/Action";

export function PortalErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <Mount>
      <div className="text-center">
        <AlertTriangle className="mx-auto size-6 text-label" aria-hidden />
        <p className="mt-2 text-md font-semibold text-record">
          We couldn&rsquo;t load this just now
        </p>
        <p className="mt-1 text-row text-label">
          It&rsquo;s us, not you — nothing you&rsquo;ve sent has been lost. Try
          again in a moment, and tell your HR team if it keeps happening.
        </p>
        {onRetry && (
          <Action type="button" className="mt-3" onClick={onRetry}>
            <RefreshCw className="size-4" aria-hidden />
            Try again
          </Action>
        )}
      </div>
    </Mount>
  );
}
