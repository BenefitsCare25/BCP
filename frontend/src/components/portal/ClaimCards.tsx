/** Claim summary card list — shared by the member portal ("My claims") and
 * the broker's read-only employee-view preview. `interactive` wraps each card
 * in a link to the portal claim detail; the preview renders static cards. */
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import type { PortalClaim } from "@/api/portal";
import { ClaimStatusBadge } from "@/components/portal/ClaimStatusBadge";
import { fmtAmount } from "@/lib/format";

function CardBody({ claim }: { claim: PortalClaim }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {claim.claim_kind === "flex"
              ? claim.flex_category_name
              : claim.benefit_key || claim.product_code}
          </span>
          <ClaimStatusBadge status={claim.status} />
        </div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          {claim.provider_name ? `${claim.provider_name} · ` : ""}
          incurred {claim.incurred_date}
          {claim.documents.length > 0 &&
            ` · ${claim.documents.length} receipt${claim.documents.length === 1 ? "" : "s"}`}
        </div>
      </div>
      <div className="text-right">
        <div className="text-sm font-semibold text-foreground">
          {claim.currency} {fmtAmount(claim.amount_claimed)}
        </div>
        {claim.amount_approved != null && (
          <div className="text-xs text-good">
            approved {claim.currency} {fmtAmount(claim.amount_approved)}
          </div>
        )}
      </div>
    </div>
  );
}

export function ClaimCards({
  items,
  interactive = false,
}: {
  items: PortalClaim[];
  interactive?: boolean;
}) {
  const cardClass =
    "block rounded-lg border border-border bg-card p-4 transition-colors";
  return (
    <div className="space-y-2">
      {items.map((claim): ReactNode =>
        interactive ? (
          <Link
            key={claim.id}
            to="/portal/claims/$claimId"
            params={{ claimId: claim.id }}
            className={`${cardClass} hover:bg-muted/50`}
          >
            <CardBody claim={claim} />
          </Link>
        ) : (
          <div key={claim.id} className={cardClass}>
            <CardBody claim={claim} />
          </div>
        ),
      )}
    </div>
  );
}
