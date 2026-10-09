import { usePortalTranslation } from "@/i18n/portal";
/** One product's choice, as a mount: the plan, then who else it covers.
 *
 * Composition only — `PlanChoice` owns the three shapes a plan picker can take
 * and `FamilyChoice` the three a dependant list can, because each of those is a
 * branch on its own conditions and holding both in one component put a
 * 380-line function behind a heading. */
import { type ReactNode, useId } from "react";
import type { ProductTierSet } from "@/api/enrollment";
import type {
  DependantRef,
  ProductState,
} from "@/components/enrollment/electionCore";
import { dependantParticipationFor } from "@/components/enrollment/electionCore";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { glossBeside } from "@/components/portal/leaf/glossary";
import { careTone, productRouteKey } from "@/components/portal/leaf/careTone";
import type { TierPrice } from "./form/formMath";
import { FamilyChoice } from "./FamilyChoice";
import { PlanChoice } from "./PlanChoice";

export function ProductElectionMount({
  ts,
  ps,
  disabled,
  allowDeps,
  dependants,
  flexOnChange,
  currency,
  rise = true,
  compact = false,
  pricing,
  premiumFor,
  onChange,
}: {
  ts: ProductTierSet;
  ps: ProductState;
  /** Read-only: the broker preview, or an enrollment already confirmed. */
  disabled: boolean;
  allowDeps: boolean;
  dependants: DependantRef[];
  flexOnChange: boolean;
  currency: string | null;
  /** Off inside an enrollment-deck slide, whose own transition owns the
   *  arrival — see `Mount`'s `rise`. */
  rise?: boolean;
  compact?: boolean;
  /** The e-form's figures for the chosen plan — key benefit, sum insured,
   *  premium per family tier and the member's share (`PlanPricing`). */
  pricing?: ReactNode;
  /** Each plan option's premium, change and member share (e-form). */
  premiumFor?: (tierKey: string) => TierPrice | null;
  onChange: (next: ProductState) => void;
}) {
  const pt = usePortalTranslation();
  const headingId = useId();
  const label = ts.product_name ?? ts.product_code;
  // `glossBeside`, not `productGloss`: the heading is already the product's own
  // name, and a gloss that only restates it reads as a rendering fault.
  const gloss = glossBeside(label, ts.product_code, ts.product_name);
  const showFamily =
    allowDeps &&
    dependants.length > 0 &&
    !ps.declined &&
    dependantParticipationFor(ts, ps.tierKey) !== null;

  return (
    <Mount
      as="article"
      rise={rise}
      label={pt(compact ? undefined : label)}
      labelId={headingId}
      gloss={compact ? undefined : gloss}
      className={`enrolment-product tone-${careTone(productRouteKey(ts.product_code))}`}
      aside={compact ? undefined :
        // Printed, never a pill: "Included for everyone" is a fact about the
        // plan, not a status to be badged, and this world's only pill is a
        // button.
        <span className="leaf-label">
          {ts.can_decline ? pt("Your choice") : pt("Included for everyone")}
        </span>
      }
    >
      <PlanChoice
        ts={ts}
        ps={ps}
        disabled={disabled}
        flexOnChange={flexOnChange}
        currency={currency}
        compact={compact}
        premiumFor={premiumFor}
        onChange={onChange}
      />

      {showFamily && (
        <>
          <MountRule />
          <FamilyChoice
            ts={ts}
            ps={ps}
            disabled={disabled}
            dependants={dependants}
            currency={currency}
            onChange={onChange}
          />
        </>
      )}

      {pricing}
    </Mount>
  );
}
