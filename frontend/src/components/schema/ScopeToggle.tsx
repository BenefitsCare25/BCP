import { Building2, Globe } from "lucide-react";
import { Segmented } from "@/components/ui/segmented";
import type { CatalogScope } from "@/api/hooks";

/** Firm-library vs company scope switch for the tenant-or-global catalog tabs
 * (employee attributes, products). "This company" shows the effective set the
 * active company uses — its own rows plus inherited firm-library defaults —
 * and creates land on the company. "Firm library" shows only the shared
 * defaults (client_id NULL) that apply to every company; writing them is
 * admin-only (enforced server-side), so a note flags read-only for others. */
export function ScopeToggle({
  scope,
  onScopeChange,
  canWriteFirm,
}: {
  scope: CatalogScope;
  onScopeChange: (scope: CatalogScope) => void;
  canWriteFirm: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <Segmented
        value={scope}
        onChange={(v) => onScopeChange(v as CatalogScope)}
        options={[
          { value: "company", label: "This company" },
          { value: "firm", label: "Firm library" },
        ]}
      />
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {scope === "firm" ? (
          <>
            <Globe className="size-3.5" />
            Shared defaults applied to every company
            {!canWriteFirm && " · admin-only to edit"}
          </>
        ) : (
          <>
            <Building2 className="size-3.5" />
            This company's rows, plus inherited firm-library defaults
          </>
        )}
      </p>
    </div>
  );
}
