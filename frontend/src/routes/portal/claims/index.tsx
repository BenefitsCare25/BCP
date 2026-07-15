/** "My claims" — the member's claims for the active policy year. */
import { Link } from "@tanstack/react-router";
import { FilePlus2, ReceiptText } from "lucide-react";
import { usePortalClaims } from "@/api/portal";
import { ClaimCards } from "@/components/portal/ClaimCards";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { isNotFoundError } from "@/lib/errors";

export function PortalClaimsPage() {
  const claims = usePortalClaims();

  if (claims.isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }

  // A 404 keeps the confident empty state below; anything else is a fetch
  // failure and must not read as "no claims yet".
  if (claims.isError && !isNotFoundError(claims.error)) {
    return <PortalErrorState onRetry={() => void claims.refetch()} />;
  }

  const rows = claims.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">
          {rows.length > 0
            ? `${claims.data?.total ?? rows.length} claim${rows.length === 1 ? "" : "s"} this policy year`
            : "My claims"}
        </h2>
        <Button asChild size="sm">
          <Link to="/portal/claims/new">
            <FilePlus2 className="size-4" />
            <span className="ml-1">Submit a claim</span>
          </Link>
        </Button>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <ReceiptText className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium text-foreground">No claims yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Submit a claim with your receipts — you can track its status here.
          </p>
        </div>
      ) : (
        <ClaimCards items={rows} interactive />
      )}
    </div>
  );
}
