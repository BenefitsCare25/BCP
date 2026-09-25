import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { ClaimLimitScope } from "@/types";

interface Props {
  idPrefix: string;
  selected: string[];
  scopes: ClaimLimitScope[];
  suggested: string[];
  legend: string;
  description: string;
  onToggle: (scopeCode: string, checked: boolean) => void;
}

/** Claim types a limit applies to. Suggested and already-chosen types show
 * first; the rest sit behind one "show more" so a GHS product's list of
 * inpatient sub-types doesn't bury the one that matters. */
export function ScopePicker({
  idPrefix,
  selected,
  scopes,
  suggested,
  legend,
  description,
  onToggle,
}: Props) {
  const [showAll, setShowAll] = useState(false);
  const rank = (scope: ClaimLimitScope) =>
    suggested.includes(scope.code) ? 0 : selected.includes(scope.code) ? 1 : 2;
  const ordered = [...scopes].sort((a, b) => rank(a) - rank(b));
  const initial = ordered.filter((scope) => rank(scope) < 2);
  const compact = initial.length > 0 && initial.length < ordered.length;
  const visible = showAll || !compact ? ordered : initial;
  const hidden = ordered.length - visible.length;

  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-foreground">{legend}</legend>
      <p className="text-2xs leading-5 text-muted-foreground">{description}</p>
      <div className="grid gap-1 sm:grid-cols-2">
        {visible.map((scope) => {
          const id = `${idPrefix}-${scope.code}`;
          return (
            <label
              key={scope.code}
              htmlFor={id}
              className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 text-xs text-foreground hover:bg-muted"
            >
              <Checkbox
                id={id}
                checked={selected.includes(scope.code)}
                onCheckedChange={(value) => onToggle(scope.code, value === true)}
              />
              <span className="min-w-0 flex-1">{scope.label}</span>
              {suggested.includes(scope.code) && (
                <span className="text-2xs text-muted-foreground">Suggested</span>
              )}
            </label>
          );
        })}
      </div>
      {hidden > 0 && (
        <Button type="button" size="sm" variant="ghost" onClick={() => setShowAll(true)}>
          <Plus className="size-3.5" aria-hidden />
          Show {hidden} other claim type{hidden === 1 ? "" : "s"}
        </Button>
      )}
    </fieldset>
  );
}
