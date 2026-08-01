/** "My claims" — the member's claims for the current benefit year. */
import { Link } from "@tanstack/react-router";
import { FilePlus2 } from "lucide-react";
import { usePortalClaims } from "@/api/portal";
import { ClaimList } from "@/components/portal/leaf/ClaimMount";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount } from "@/components/portal/leaf/Mount";
import { actionClass } from "@/components/portal/leaf/Action";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function PortalClaimsPage() {
  const claims = usePortalClaims();
  useDocumentTitle("My claims");

  if (claims.isLoading) return <LeafSkeleton label="Loading your claims" />;

  // A 404 keeps the confident empty state below; anything else is a fetch
  // failure and must not read as "no claims yet".
  if (claims.isError && !isNotFoundError(claims.error)) {
    return <PortalErrorState onRetry={() => void claims.refetch()} />;
  }

  const rows = claims.data?.items ?? [];

  return (
    <div className="space-y-3">
      {/* THE primary action of the member portal, and the page's one brand
          fill. Full width on a phone: submitting a claim is the reason the
          member is on this screen, and a right-aligned small button is the
          hardest thing to hit one-handed. */}
      <Link
        to="/portal/claims/new"
        className={actionClass("primary", { block: "phone" })}
      >
        <FilePlus2 className="size-4" aria-hidden />
        Make a claim
      </Link>

      {rows.length === 0 ? (
        <Mount label="No claims yet">
          <p className="text-row text-label">
            When you pay for treatment that your benefits cover, send us the
            receipt here and we'll tell you where it's up to.
          </p>
        </Mount>
      ) : (
        <ClaimList items={rows} interactive />
      )}
    </div>
  );
}
