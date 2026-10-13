import { ChevronRight } from "lucide-react";
import type { JoinerProductRules } from "@/api/employeeListing";
import { count } from "./format";

/**
 * How people NOT on this listing (later joiners) will be placed: each
 * product's categories as rules learned from the listing. People on the
 * listing keep their listed category; exceptions are those whose listed
 * category differs from their group's. Collapsed: it explains, it doesn't ask.
 */
export function JoinerRules({ products }: { products: JoinerProductRules[] }) {
  const withRules = products.filter((p) => p.rules.length > 0);
  if (withRules.length === 0) return null;
  return (
    <details className="group rounded-lg border border-border">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
        <ChevronRight
          aria-hidden
          className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90 motion-reduce:transition-none"
        />
        <span className="font-medium">How joiners not on this listing are placed</span>
        <span className="ml-auto whitespace-nowrap text-xs text-muted-foreground">
          {count(withRules.length, "product")}
        </span>
      </summary>
      <ul className="border-t border-border">
        {withRules.map((product) => (
          <li key={product.product_code} className="border-t border-border px-3 py-2 first:border-t-0">
            <p className="text-sm font-medium text-foreground">
              {product.product_code}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                by {product.attributes.join(" and ")}
                {product.exceptions > 0 &&
                  ` · ${count(product.exceptions, "listed exception")} keep their listed cover`}
              </span>
            </p>
            <ul className="mt-1 space-y-0.5">
              {product.rules.map((rule) => (
                <li key={rule.category_id} className="break-words text-xs text-muted-foreground">
                  <span className="text-foreground">{rule.category_label}</span>
                  {" ← "}
                  {rule.rule}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </details>
  );
}
