import { Link } from "@tanstack/react-router";
import { AlertTriangle, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";

function BackHome() {
  return (
    <Link to="/">
      <Button variant="default">
        <Home className="size-4" />
        Back to home
      </Button>
    </Link>
  );
}

export function GlobalErrorComponent({
  error,
  reset,
}: {
  error: unknown;
  reset?: () => void;
}) {
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background p-6">
      <div className="flex max-w-md flex-col items-start gap-4 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2 text-error">
          <AlertTriangle className="size-5" />
          <span className="text-base font-semibold">Something went wrong</span>
        </div>
        <p className="text-sm text-muted-foreground">{formatError(error)}</p>
        <div className="flex gap-2">
          {reset && (
            <Button variant="outline" onClick={reset}>
              Try again
            </Button>
          )}
          <BackHome />
        </div>
      </div>
    </div>
  );
}

export function NotFoundComponent() {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-start gap-4 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="text-base font-semibold text-foreground">Page not found</div>
        <p className="text-sm text-muted-foreground">
          The URL you requested doesn't match any route in this workspace.
        </p>
        <BackHome />
      </div>
    </div>
  );
}
