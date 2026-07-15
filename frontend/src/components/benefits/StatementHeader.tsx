import { Badge } from "@/components/ui/badge";
import type { BenefitStatement } from "@/types";

interface Props {
  employee: BenefitStatement["employee"];
  attributes: BenefitStatement["attributes"];
  isMatched: boolean;
  productCount: number;
  hasFlex?: boolean;
}

function coverageLabel(
  isMatched: boolean,
  productCount: number,
  hasFlex: boolean,
): string {
  const parts: string[] = [];
  if (isMatched) {
    parts.push(`${productCount} ${productCount === 1 ? "product" : "products"}`);
  }
  if (hasFlex) parts.push("Flex wallet");
  if (parts.length === 0) return "No coverage assigned";
  return `Covered under ${parts.join(" + ")}`;
}

export function StatementHeader({
  employee,
  attributes,
  isMatched,
  productCount,
  hasFlex = false,
}: Props) {
  const covered = isMatched || hasFlex;
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">
            {employee.employee_name ?? employee.staff_id}
          </h2>
          <p className="font-mono text-xs text-muted-foreground">
            Staff ID {employee.staff_id}
          </p>
        </div>
        <Badge variant={covered ? "good" : "warn"}>
          {coverageLabel(isMatched, productCount, hasFlex)}
        </Badge>
      </div>
      {attributes.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {attributes.map((a) => (
            <span
              key={a.key}
              className="rounded-md border border-border bg-muted/40 px-2 py-0.5 text-xs text-foreground"
            >
              <span className="text-muted-foreground">{a.label}: </span>
              {a.value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
