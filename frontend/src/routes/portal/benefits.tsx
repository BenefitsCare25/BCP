/** "What's covered" — the member's own leaf. */
import { usePortalMe, usePortalStatement } from "@/api/portal";
import { CoverageLeaf, type CoverageSelection } from "@/components/portal/leaf/CoverageLeaf";
import { Mount } from "@/components/portal/leaf/Mount";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { isNotFoundError } from "@/lib/errors";
import { useCompany } from "@/components/portal/useCompany";

export function PortalBenefitsPage({
  selection,
  onSelectionChange,
}: {
  /** Selected care route and person, carried in `?p=` / `?who=`. */
  selection?: CoverageSelection;
  onSelectionChange?: (next: CoverageSelection) => void;
} = {}) {
  const statement = usePortalStatement();
  const { data: profile } = usePortalMe();
  const company = useCompany();

  if (statement.isLoading) return <LeafSkeleton label="Loading your benefits" />;

  // Only a 404 means "no active coverage" — other failures get a retryable
  // error state instead of the confident no-coverage copy.
  if (statement.isError && !isNotFoundError(statement.error)) {
    return <PortalErrorState onRetry={() => void statement.refetch()} />;
  }

  if (statement.isError || !statement.data) {
    const awaitingPublication = profile?.policy_year === null;
    return (
      <Mount label={awaitingPublication ? "Benefits not live yet" : "No benefits linked to your account"}>
        <p className="text-row text-label">
          {awaitingPublication
            ? "Your company's benefit year is still being prepared. Your coverage will appear here after it goes live. Ask your HR team if you need details sooner."
            : "We couldn't find a benefit record linked to your account for the live year. Ask your HR team to check your employee record."}
        </p>
      </Mount>
    );
  }

  return (
    <CoverageLeaf
      data={statement.data}
      selection={selection}
      onSelectionChange={onSelectionChange}
      company={company}
    />
  );
}
