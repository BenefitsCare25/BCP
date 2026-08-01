/** "What am I covered for" — the member's leaf.
 *
 * Replaces `BenefitStatement` on member surfaces. That component is the
 * broker's placement-slip renderer pointed at a member token: the server strips
 * the figures it must, but the copy, hierarchy, vocabulary and density were
 * never re-authored. This is the re-authoring.
 *
 * Roster attributes (job grade, salary band, entity) are deliberately not
 * rendered. They are how the company files the member, not an answer to any of
 * the four questions a member actually opens this page with. */
import type { BenefitStatement, Utilization } from "@/types";
import { BenefitMount } from "./BenefitMount";
import { FlexMount } from "./FlexMount";
import { Mount } from "./Mount";

export function CoverageLeaf({
  data,
  utilization,
}: {
  data: BenefitStatement;
  utilization?: Utilization | null;
}) {
  const hasFlex = Boolean(data.flex);
  // Gate on what there is to RENDER, not on `is_matched`. A member can be
  // matched and still have no coverage lines — `hydrate_plans` skips
  // matched_categories entries whose category was deleted or re-parsed — and
  // gating on the flag alone rendered an empty page with no explanation.
  const hasAnyCoverage = data.coverage.length > 0 || hasFlex;

  if (!hasAnyCoverage) {
    return (
      <Mount label="No benefits on record">
        <p className="text-row text-label">
          We don't have any benefits recorded against your name for this
          period. If you think that's wrong, your HR team can check your record.
        </p>
      </Mount>
    );
  }

  return (
    <div className="space-y-3">
      {data.coverage.map((line) => (
        <BenefitMount
          key={line.product_code}
          line={line}
          utilization={utilization}
        />
      ))}
      {data.flex && <FlexMount flex={data.flex} />}
    </div>
  );
}
