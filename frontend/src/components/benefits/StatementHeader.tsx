import { Fragment, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { fmtDate } from "@/lib/format";
import type { BenefitStatement } from "@/types";

interface Props {
  employee: BenefitStatement["employee"];
  attributes: BenefitStatement["attributes"];
  dependants: BenefitStatement["dependants"];
  coverage: BenefitStatement["coverage"];
  isMatched: boolean;
  productCount: number;
  hasFlex?: boolean;
  /** Admin controls for this person — portal access, LOG cases. They belong to
   * the PERSON, not to their cover, so they sit in this strip rather than as
   * cards above the coverage a broker opened the page to read. */
  actions?: ReactNode;
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

/**
 * Who this is, what they hold, and the two things a broker can do to them.
 *
 * Roster attributes and the family are dot-separated meta rather than pills:
 * this strip carries a name, a badge, two buttons and up to six facts, and
 * bordering every fact turned an identity line into a field of lozenges.
 * `DependantsPanel` was folded in here for the same reason — it was a whole
 * card to say "one spouse, covered under GD and GHS", and the per-product
 * answer now lives on the product's own row.
 */
export function StatementHeader({
  employee,
  attributes,
  dependants,
  coverage,
  isMatched,
  productCount,
  hasFlex = false,
  actions,
}: Props) {
  const covered = isMatched || hasFlex;
  // De-duplicated: `product_code` is not unique across a statement
  // (`hydrate_plans` emits one line per matched CATEGORY), so a dependant
  // covered by two lines of the same product yielded two badges with the same
  // React key.
  const productsFor = (depId: string): string[] => [
    ...new Set(
      coverage
        .filter((c) => c.covered_dependants.some((d) => d.id === depId))
        .map((c) => c.product_code),
    ),
  ];

  return (
    <header className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <h2 className="text-base font-semibold text-foreground">
              {employee.employee_name ?? employee.staff_id}
            </h2>
            <Badge variant={covered ? "good" : "warn"}>
              {coverageLabel(isMatched, productCount, hasFlex)}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="font-mono">{employee.staff_id}</span>
            {attributes.map((a) => (
              <Fragment key={a.key}>
                <span aria-hidden className="mx-1.5 text-subtle">
                  ·
                </span>
                {a.label} {a.value}
              </Fragment>
            ))}
          </p>
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </div>

      {/* No "Family" heading. Every row already names the relationship
        * ("· Spouse", "· Child"), so the label restated what the content says
        * and cost a whole uppercase tier to do it. The rule above the list is
        * the only separation it needs. */}
      {dependants.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <ul className="flex flex-col gap-1">
            {dependants.map((d) => {
              const products = productsFor(d.id);
              return (
                <li
                  key={d.id}
                  className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs"
                >
                  <span className="min-w-0 text-foreground">
                    {d.name ?? "Dependant"}
                    {d.relationship && (
                      <span className="text-muted-foreground">
                        {" · "}
                        {d.relationship}
                      </span>
                    )}
                    {d.dob && (
                      <span className="text-muted-foreground">
                        {" · "}
                        {fmtDate(d.dob)}
                      </span>
                    )}
                  </span>
                  <span className="flex flex-wrap gap-1">
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
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </header>
  );
}
