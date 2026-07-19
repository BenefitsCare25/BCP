import type { ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { useFlexScheme } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SkeletonTable } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { FlexSchemeForm } from "./FlexSchemeForm";
import { FlexOverviewCard } from "./FlexOverviewCard";

interface Props {
  policyYearId: string;
  // Benefit-year picker (Flex has no per-product title, so it rides the
  // overview card's header alongside the coverage download).
  yearSelector?: ReactNode;
}

/**
 * Flexible Benefits tab. A policy year has at most one Flex scheme. The flex
 * document upload lives at the page top (replacing the placement-slip card on
 * this tab); once a scheme is extracted (or manually created) the guided
 * review/edit form appears. The family-status headcount + coverage checks are
 * shown above it, tying the scheme back to the actual member population.
 */
export function FlexPanel({ policyYearId, yearSelector }: Props) {
  const { data: scheme, isLoading, isError, error, refetch } =
    useFlexScheme(policyYearId);

  if (isLoading) {
    return <SkeletonTable rows={6} columns={3} />;
  }

  // A real fetch failure must NOT render the upload hero — a scheme may
  // already exist, and re-uploading over it would spend AI budget. Only a
  // true 404 (mapped to null by the hook) shows the upload flow.
  if (isError) {
    return (
      <Card>
        <CardContent className="p-5 text-center space-y-2">
          <AlertTriangle className="mx-auto size-5 text-muted-foreground" />
          <p className="text-sm font-medium text-foreground">
            Couldn't load the Flex scheme
          </p>
          <p className="text-xs text-muted-foreground">{formatError(error)}</p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            <RefreshCw className="size-4" /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <FlexOverviewCard
        policyYearId={policyYearId}
        yearSelector={yearSelector}
      />
      {scheme && <FlexSchemeForm policyYearId={policyYearId} scheme={scheme} />}
    </div>
  );
}
