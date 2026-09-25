import { useState } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import type { ClaimLimitScope } from "@/types";

interface Props {
  idPrefix: string;
  labelledBy: string;
  selected: string[];
  scopes: ClaimLimitScope[];
  suggested: string[];
  onToggle: (scopeCode: string, checked: boolean) => void;
}

/** Claim types linked to a limit. Suggested and already-linked types show
 * first; the rest sit behind "+ N more" so a GHS product's list of inpatient
 * sub-types doesn't bury the one that matters. */
export function ScopePicker({ idPrefix, labelledBy, selected, scopes, suggested, onToggle }: Props) {
  const [showAll, setShowAll] = useState(false);
  const rank = (scope: ClaimLimitScope) =>
    suggested.includes(scope.code) ? 0 : selected.includes(scope.code) ? 1 : 2;
  const ordered = [...scopes].sort((a, b) => rank(a) - rank(b));
  const initial = ordered.filter((scope) => rank(scope) < 2);
  const compact = initial.length > 0 && initial.length < ordered.length;
  const visible = showAll || !compact ? ordered : initial;
  const hidden = ordered.length - visible.length;

  return (
    <div role="group" aria-labelledby={labelledBy} className="flex flex-wrap gap-1.5">
      {visible.map((scope) => {
        const id = `${idPrefix}-${scope.code}`;
        return (
          <label
            key={scope.code}
            htmlFor={id}
            className="inline-flex min-h-8 cursor-pointer items-center gap-2 rounded-full border border-border px-3 text-xs text-foreground hover:bg-muted has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5"
          >
            <Checkbox
              id={id}
              checked={selected.includes(scope.code)}
              onCheckedChange={(value) => onToggle(scope.code, value === true)}
            />
            {scope.label}
            {suggested.includes(scope.code) && <span className="text-2xs text-muted-foreground">Suggested</span>}
          </label>
        );
      })}
      {hidden > 0 && (
        <button
          type="button"
          className="inline-flex min-h-8 items-center rounded-full border border-dashed border-border px-3 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => setShowAll(true)}
        >
          + {hidden} more
        </button>
      )}
    </div>
  );
}
