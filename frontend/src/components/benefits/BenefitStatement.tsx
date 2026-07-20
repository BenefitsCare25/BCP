import { FileWarning } from "lucide-react";
import type {
  BenefitStatement as BenefitStatementData,
  Utilization,
} from "@/types";
import { StatementHeader } from "./StatementHeader";
import { CoverageCard } from "./CoverageCard";
import { FlexCoverageCard } from "./FlexCoverageCard";
import { DependantsPanel } from "./DependantsPanel";

export function BenefitStatement({
  data,
  utilization,
}: {
  data: BenefitStatementData;
  /**
   * Claim usage for the same employee. Optional so a surface can render the
   * statement before (or without) it; when present each benefit line shows what
   * is left, which is the question a member actually opens this page to answer.
   */
  utilization?: Utilization | null;
}) {
  const hasFlex = Boolean(data.flex);
  const hasAnyCoverage = data.is_matched || hasFlex;

  return (
    <div className="space-y-4">
      <StatementHeader
        employee={data.employee}
        attributes={data.attributes}
        isMatched={data.is_matched}
        productCount={data.coverage.length}
        hasFlex={hasFlex}
      />

      {hasAnyCoverage ? (
        <>
          {data.dependants.length > 0 && (
            <DependantsPanel
              dependants={data.dependants}
              coverage={data.coverage}
            />
          )}
          <div className="space-y-3">
            {data.coverage.map((line) => (
              <CoverageCard
                key={line.product_code}
                line={line}
                utilization={utilization}
              />
            ))}
            {data.flex && <FlexCoverageCard flex={data.flex} />}
          </div>
        </>
      ) : (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <FileWarning className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium text-foreground">
            No coverage assigned
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            This employee did not match any product category. Review their
            attributes or run matching from the Employees page.
          </p>
        </div>
      )}
    </div>
  );
}
