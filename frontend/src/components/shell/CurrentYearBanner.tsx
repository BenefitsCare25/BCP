import { Link } from "@tanstack/react-router";
import { usePolicyYears } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { defaultPolicyYear } from "@/lib/policy-year";

/**
 * Inline recovery state for settings whose vocabulary depends on a benefit
 * year. The default is date-derived; there is no manual "set current" action.
 */
export function NoCurrentYearNotice() {
  const yearsQuery = usePolicyYears();
  const years = yearsQuery.data ?? [];

  if (yearsQuery.isPending) return <Skeleton className="h-24 w-full" />;
  if (yearsQuery.isError) {
    return (
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t load this company&apos;s benefit years.
      </p>
    );
  }

  const fallback = defaultPolicyYear(years);
  return (
    <div className="space-y-2.5 rounded-md border border-border bg-error-soft/40 p-3.5">
      <p className="text-sm font-medium text-foreground">
        {years.length === 0
          ? "This company has no benefit year yet."
          : "No benefit year covers today."}
      </p>
      <p className="max-w-prose text-xs text-muted-foreground">
        Add or update a benefit year so today falls within its policy period.
        The platform will then use it automatically for the member portal,
        claims, products and panel setup.
      </p>
      <Button asChild size="sm" variant="outline" className="h-7">
        <Link to="/client-relations/company-benefits">
          {fallback ? "Review benefit-year dates" : "Add a benefit year"}
        </Link>
      </Button>
    </div>
  );
}
