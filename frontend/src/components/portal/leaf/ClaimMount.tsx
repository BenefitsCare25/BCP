/** A claim, as a mount on the member's leaf.
 *
 * "What happened to my claim?" is the question members come back for, so the
 * state is struck and everything else is subordinate to it. The old card put a
 * soft badge inline with the title and repeated the claim type verbatim in the
 * supporting line ("Emergency Accidental Outpatient Treatment · Emergency
 * Accidental Outpatient Treatment · …"), which spent two lines saying one thing.
 *
 * Shared by the member's list and the broker's employee-view preview. */
import { Link } from "@tanstack/react-router";
import type { PortalClaim } from "@/api/portal";
import { Mount } from "./Mount";
import { Money } from "./Figure";
import { ClaimStrike } from "./Strike";
import { formatDay } from "./date";

/** What the claim is FOR, in the member's words. */
export function claimTitle(claim: PortalClaim): string {
  if (claim.claim_kind === "flex") {
    return claim.flex_category_name || "Flexible benefit";
  }
  return claim.claim_type || claim.product_code || "Claim";
}

/** Where and when — the two facts that let a member recognise which receipt
 * this was, without repeating the title. */
function claimContext(claim: PortalClaim): string {
  const parts = [
    claim.dependant_name ? `For ${claim.dependant_name}` : null,
    claim.provider_name,
    formatDay(claim.incurred_date),
  ].filter(Boolean);
  return parts.join(" · ");
}

function ClaimBody({ claim }: { claim: PortalClaim }) {
  const docs = claim.documents.length;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-row text-label">
          {claim.amount_approved != null ? "You claimed" : "Amount claimed"}
        </span>
        <Money value={claim.amount_claimed} currency={claim.currency} />
      </div>
      {/* An approved figure is the outcome, so it outranks the requested one.
          It is stated separately rather than replacing it — a member whose
          claim was partly approved needs both numbers to see the difference. */}
      {claim.amount_approved != null && (
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-row text-label">Approved</span>
          <Money
            value={claim.amount_approved}
            currency={claim.currency}
            emphasis="strong"
          />
        </div>
      )}
      {docs > 0 && (
        <p className="text-row text-label">
          {docs} document{docs === 1 ? "" : "s"} attached
        </p>
      )}
    </div>
  );
}

export function ClaimMount({
  claim,
  to,
}: {
  claim: PortalClaim;
  /** Omitted on the broker preview, where cards are inert. */
  to?: boolean;
}) {
  // `interactive` only when the mount actually navigates: the 3px lift IS the
  // affordance, so a mount that lifts and then does nothing breaks the promise
  // the surface made. The broker preview's cards are inert and get the resting
  // hover instead.
  const inner = (
    <Mount
      as="article"
      label={claimTitle(claim)}
      gloss={claimContext(claim)}
      aside={<ClaimStrike status={claim.status} />}
      interactive={to}
    >
      <ClaimBody claim={claim} />
    </Mount>
  );

  if (!to) return <li>{inner}</li>;
  return (
    <li>
      <Link
        to="/portal/claims/$claimId"
        params={{ claimId: claim.id }}
        className="leaf-focus block rounded-tile"
      >
        {inner}
      </Link>
    </li>
  );
}

export function ClaimList({
  items,
  interactive = false,
}: {
  items: PortalClaim[];
  interactive?: boolean;
}) {
  return (
    <ul className="space-y-3">
      {items.map((claim) => (
        <ClaimMount key={claim.id} claim={claim} to={interactive} />
      ))}
    </ul>
  );
}
