import { ArrowRight } from "lucide-react";
import type { ListingEntity, ListingMapping } from "@/api/employeeListing";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { count } from "./format";

/**
 * Each Entity spelling on the listing against the slip's insured entities.
 *
 * A category insured on an entity only matches employees whose Entity
 * resolves to it, so a typo ("STA Pharamceutical") quietly unmatches every
 * joiner spelled that way. The likely match is offered, never assumed: the
 * broker ticks it to create the alias on apply (neither name is rewritten).
 */
export function EntityLinks({
  entities,
  mapping,
  onLinkChange,
  disabled,
}: {
  entities: ListingEntity[];
  mapping: ListingMapping;
  onLinkChange: (spelling: string, target: string | null) => void;
  disabled: boolean;
}) {
  if (entities.length === 0) return null;
  const open = entities.filter((e) => e.status === "unmatched");
  const resolved = entities.filter((e) => e.status !== "unmatched");
  return (
    <section aria-labelledby="listing-entities" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="listing-entities" className="text-sm font-semibold text-foreground">
          Insured entities
        </h3>
        <span className="text-xs text-muted-foreground">
          {open.length > 0
            ? `${count(open.length, "spelling")} not on the slip`
            : `All ${count(entities.length, "spelling")} match the slip`}
        </span>
      </div>
      <ul className="overflow-hidden rounded-lg border border-border">
        {open.map((entity) => (
          <li key={entity.spelling} className="space-y-1 border-t border-border px-3 py-2 first:border-t-0">
            <p className="text-sm text-foreground">
              <span className="break-words">{entity.spelling}</span>
              <span className="text-muted-foreground"> · {count(entity.employees, "employee")}</span>
            </p>
            {entity.suggestion ? (
              <label className="flex cursor-pointer items-start gap-2 text-xs text-foreground">
                <Checkbox
                  className="mt-px"
                  checked={mapping.entity_links[entity.spelling] === entity.suggestion}
                  onCheckedChange={(v) =>
                    onLinkChange(entity.spelling, v === true ? entity.suggestion : null)
                  }
                  disabled={disabled}
                />
                <span>
                  Tick if this is <strong className="font-medium">{entity.suggestion}</strong>{" "}
                  on the slip — the spelling is linked when applying
                </span>
              </label>
            ) : (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="warn">No slip match</Badge>
                These employees won&apos;t match a category restricted to an entity. Add an
                alias under Member Listing after applying.
              </p>
            )}
          </li>
        ))}
        {resolved.length > 0 && (
          <li className="border-t border-border first:border-t-0">
            <details>
              <summary className="cursor-pointer list-none px-3 py-2 text-xs text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
                {count(resolved.length, "spelling")} match the slip&apos;s entities{" "}
                <span className="text-foreground underline underline-offset-2">Show</span>
              </summary>
              <ul className="space-y-1 px-3 pb-2">
                {resolved.map((entity) => (
                  <li
                    key={entity.spelling}
                    className="grid gap-1 text-xs sm:grid-cols-[minmax(0,1fr)_1rem_minmax(0,1fr)] sm:items-center"
                  >
                    <span className="min-w-0 break-words text-foreground">
                      {entity.spelling}
                      <span className="text-muted-foreground"> · {entity.employees.toLocaleString()}</span>
                    </span>
                    <ArrowRight aria-hidden className="hidden size-3.5 text-muted-foreground sm:block" />
                    <span className="min-w-0 break-words text-muted-foreground">
                      {entity.entity}
                      {entity.status === "aliased" && " (alias)"}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          </li>
        )}
      </ul>
    </section>
  );
}
