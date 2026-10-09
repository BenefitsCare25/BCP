import { Link } from "@tanstack/react-router";
import { AlertTriangle, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { usePortalTranslation } from "@/i18n/portal";

function BackHome() {
  const pt = usePortalTranslation();
  return (
    <Link to="/">
      <Button variant="default">
        <Home className="size-4" />
        {pt("Back to home")}
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
  const pt = usePortalTranslation();
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background p-6">
      <div className="flex max-w-md flex-col items-start gap-4 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2 text-error">
          <AlertTriangle className="size-5" />
          <span className="text-base font-semibold">{pt("Something went wrong")}</span>
        </div>
        <p className="text-sm text-muted-foreground">{pt(formatError(error))}</p>
        <div className="flex gap-2">
          {reset && (
            <Button variant="outline" onClick={reset}>
              {pt("Try again")}
            </Button>
          )}
          <BackHome />
        </div>
      </div>
    </div>
  );
}

export function NotFoundComponent() {
  const pt = usePortalTranslation();
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-start gap-4 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="text-base font-semibold text-foreground">{pt("Page not found")}</div>
        <p className="text-sm text-muted-foreground">
          {pt("The URL you requested doesn't match any route in this workspace.")}
        </p>
        <BackHome />
      </div>
    </div>
  );
}
