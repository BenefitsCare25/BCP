import { Badge } from "@/components/ui/badge";
import { fmtDate } from "@/lib/format";
import type { BenefitStatement } from "@/types";

interface Props {
  dependants: BenefitStatement["dependants"];
  coverage: BenefitStatement["coverage"];
}

export function DependantsPanel({ dependants, coverage }: Props) {
  if (dependants.length === 0) return null;

  // Map each dependant → the product codes whose coverage extends to them.
  const productsFor = (depId: string): string[] =>
    coverage
      .filter((c) => c.covered_dependants.some((d) => d.id === depId))
      .map((c) => c.product_code);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-foreground">Dependants</h3>
      <ul className="mt-2 space-y-2">
        {dependants.map((d) => {
          const products = productsFor(d.id);
          return (
            <li
              key={d.id}
              className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 first:border-t-0 first:pt-0"
            >
              <div className="text-xs">
                <span className="text-foreground">{d.name ?? "Dependant"}</span>
                {d.relationship && (
                  <span className="text-muted-foreground"> · {d.relationship}</span>
                )}
                {d.dob && (
                  <span className="text-muted-foreground"> · {fmtDate(d.dob)}</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {products.length > 0 ? (
                  products.map((p) => (
                    <Badge key={p} variant="outline">
                      {p}
                    </Badge>
                  ))
                ) : (
                  <span className="text-2xs text-muted-foreground">
                    Not covered under any product
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
