/** The paper form's figures for the plan the member has chosen: what the plan
 * gives (room & board, sum insured), the annual premium for each family
 * composition, and the share the member pays.
 *
 * Premiums appear only where the broker set an employee share for the product
 * (`ProductContribution`) — the premium of a company-paid plan is not a price
 * the member acts on. The key benefit and sum insured always appear. */
import type { ContributionTier, PlanFact, ProductContribution } from "@/api/enrollmentForms";
import { coverWording } from "@/lib/basis";
import { Money } from "@/components/portal/leaf/Figure";
import { MountRow, MountRule } from "@/components/portal/leaf/Mount";

const ROWS: { key: "EO" | "ES" | "EC" | "EF"; label: string }[] = [
  { key: "EO", label: "Employee only" },
  { key: "ES", label: "Employee and spouse" },
  { key: "EC", label: "Employee and child(ren)" },
  { key: "EF", label: "Employee and family" },
];

function shareGloss(contribution: ProductContribution): string {
  const parts: string[] = [];
  // No share set for the member's own cover means the company pays it.
  parts.push(
    !contribution.employee_pct
      ? "your own cover is paid by the company"
      : `you pay ${contribution.employee_pct}% of your own cover`,
  );
  if (!contribution.employee_pct && contribution.upgrade_pct) {
    parts.push(`you pay ${contribution.upgrade_pct}% of the extra for a higher plan`);
  }
  if (contribution.dependant_pct !== null) {
    parts.push(
      contribution.dependant_pct === 0
        ? "family cover is paid by the company"
        : `${contribution.employee_pct ? "" : "you pay "}${contribution.dependant_pct}% of family cover`,
    );
  }
  const text = parts.join(", ");
  return text ? text[0].toUpperCase() + text.slice(1) + "." : "";
}

function PremiumRows({ tier }: { tier: ContributionTier }) {
  if (tier.mode === "flat") {
    return (
      <>
        <MountRow term="Annual premium (you)">
          <Money value={tier.premium.EO ?? null} />
        </MountRow>
        {tier.premium_per_dependant !== null && (
          <MountRow term="Per family member covered">
            <Money value={tier.premium_per_dependant} />
          </MountRow>
        )}
      </>
    );
  }
  return (
    <>
      {ROWS.filter((r) => tier.premium[r.key] !== undefined).map((r) => (
        <MountRow key={r.key} term={r.label}>
          <Money value={tier.premium[r.key] ?? null} />
        </MountRow>
      ))}
    </>
  );
}

export function PlanPricing({
  fact,
  contribution,
  tierKey,
  compact = false,
}: {
  fact: PlanFact | undefined;
  contribution: ProductContribution | undefined;
  tierKey: string;
  compact?: boolean;
}) {
  const tier = contribution?.tiers.find((t) => t.tier_key === tierKey);
  const basis = coverWording(fact?.basis, fact?.max_sum_insured);
  const hasFacts = !!(fact?.highlight || fact?.sum_insured || basis || fact?.insurer);
  if (!hasFacts && !tier) return null;
  const breakdown = tier && contribution ? (
    <div>
      <p className="text-row font-medium text-record">
        Premium by family cover
        {contribution.gst_included ? " (incl. GST)" : " (before GST)"}
      </p>
      <dl><PremiumRows tier={tier} /></dl>
      <p className="text-row text-label">{shareGloss(contribution)}</p>
    </div>
  ) : null;
  return (
    <>
      <MountRule />
      {hasFacts && (
        <dl>
          {fact?.insurer && <MountRow term="Insurer">{fact.insurer}</MountRow>}
          {fact?.highlight && <MountRow term="Key benefit">{fact.highlight}</MountRow>}
          {fact?.sum_insured ? (
            <MountRow term="Sum insured">
              <Money value={fact.sum_insured} />
            </MountRow>
          ) : basis ? (
            <MountRow term="Sum insured">{basis}</MountRow>
          ) : null}
        </dl>
      )}
      {breakdown && (compact ? (
        <details className="enrolment-cost-details">
          <summary className="leaf-focus text-row font-semibold text-record">Premium &amp; contribution details</summary>
          {breakdown}
        </details>
      ) : breakdown)}
    </>
  );
}
