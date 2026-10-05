import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useBlocker } from "@tanstack/react-router";
import { Save } from "lucide-react";
import { toast } from "sonner";
import { useMe, useSetMatchOverride } from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import type { CategoryGroup, Employee } from "@/types";

export function EmployeeMappingEditor({ employee, groups, actionContainer, disabled, onDirtyChange }: {
  employee: Employee; groups: CategoryGroup[]; actionContainer: HTMLElement | null;
  disabled: boolean; onDirtyChange: (dirty: boolean) => void;
}) {
  const { data: me } = useMe();
  const override = useSetMatchOverride();
  const initial = () => new Set(employee.matched_plans.flatMap((p) => p.category_id ? [p.category_id] : []));
  const [baseline, setBaseline] = useState(initial);
  const [selected, setSelected] = useState(initial);
  const [version, setVersion] = useState(employee.updated_at);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const dirty = selected.size !== baseline.size || [...selected].some((id) => !baseline.has(id));
  const editable = Boolean(me && me.role !== "broker_viewer");
  useEffect(() => {
    if (dirty || override.isPending) return;
    const latest = initial();
    setBaseline(latest); setSelected(latest); setVersion(employee.updated_at);
    // A background refresh must preserve unsaved choices; a saved roster edit advances the version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employee.updated_at]);
  const blocker = useBlocker({
    shouldBlockFn: ({ current: from, next }) => (dirty || override.isPending) &&
      (from.pathname !== next.pathname || JSON.stringify(from.search) !== JSON.stringify(next.search)),
    enableBeforeUnload: () => dirty || override.isPending,
    disabled: !dirty && !override.isPending, withResolver: true,
  });
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;
  const save = async () => {
    if (!dirty || override.isPending) return;
    setError(null);
    const committed = new Set(selected);
    try {
      const result = await override.mutateAsync({ employeeId: employee.id,
        categoryIds: [...committed], expectedUpdatedAt: version ?? undefined });
      setBaseline(committed);
      setVersion(result.updated_at ?? undefined);
      setSaved(true);
      onDirtyChange(false);
      toast.success("Employee mapping saved");
      if (blockerRef.current.status === "blocked") blockerRef.current.proceed();
    } catch (caught) {
      setError(`${formatError(caught)} Your selections are still here and have not been saved.`);
    }
  };
  const actions = <div className="w-full space-y-2">
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p role="status" className="text-sm text-muted-foreground">
        {override.isPending ? "Saving employee mapping…" : dirty ? "Unsaved mapping changes" : saved ? "Employee mapping saved" : "No unsaved mapping changes"}
      </p>
      <Button size="sm" disabled={!dirty || disabled} loading={override.isPending} onClick={() => void save()}>
        <Save className="size-4" aria-hidden="true" />Save employee mapping
      </Button>
    </div>
  </div>;
  return <section aria-labelledby="employee-mapping-heading" className="space-y-2">
    <h3 id="employee-mapping-heading" className="text-2xs uppercase tracking-wider text-muted-foreground">Manual mapping</h3>
    <p className="text-xs text-muted-foreground">Choose one employee category per product, then save. Dependant-only options are managed with dependant coverage.</p>
    <div className="max-h-56 overflow-y-auto rounded-md border border-border divide-y divide-border">
      {groups.length === 0 && <p className="p-3 text-xs text-muted-foreground">No categories configured for this policy year.</p>}
      {groups.map((group) => {
        const categories = group.categories.filter((c) => c.plan_assignments?.member_scope !== "dependant");
        return <fieldset key={group.product_code} className="p-2">
          <legend className="float-left w-full mb-1 text-2xs uppercase tracking-wider text-muted-foreground">
            {group.product_code} · {group.product_display_name}
          </legend>
          {categories.length === 0 && <p className="clear-both text-xs text-muted-foreground">Dependant-only product; no employee category to assign.</p>}
          {categories.map((category) => {
            const plan = category.plan_assignments?.plan_code;
            const insured = category.plan_assignments?.insured;
            // A broker may replace a category. Removing saved product coverage requires an administrator.
            const canUncheck = me?.role === "system_admin" || !baseline.has(category.id);
            return <label key={category.id} className="flex clear-both items-start gap-2 py-1 text-sm">
              <input type="checkbox" className="mt-1" checked={selected.has(category.id)}
                disabled={!editable || disabled || override.isPending || (selected.has(category.id) && !canUncheck)}
                onChange={(event) => {
                  const next = new Set(selected);
                  if (event.target.checked) {
                    group.categories.forEach((c) => next.delete(c.id));
                    next.add(category.id);
                  } else next.delete(category.id);
                  setSelected(next); setError(null); setSaved(false);
                  onDirtyChange(next.size !== baseline.size || [...next].some((id) => !baseline.has(id)));
                }} />
              <span className="min-w-0">
                <span>{category.display_name}{plan != null && plan !== "" ? ` · Plan ${String(plan)}` : ""}</span>
                {Boolean(insured) && <span className="block text-xs text-muted-foreground">{String(insured)}</span>}
              </span>
            </label>;
          })}
        </fieldset>;
      })}
    </div>
    {editable && !disabled && (actionContainer ? createPortal(actions, actionContainer) : actions)}
    <AlertDialog open={blocker.status === "blocked"}
      onOpenChange={(open) => { if (!open && !override.isPending && blocker.status === "blocked") blocker.reset(); }}
      title="Unsaved employee mapping" description={<div className="space-y-2"><p>Your category selections have not been saved.</p>{error && <p role="alert" className="text-error">{error}</p>}</div>}
      tone="info" cancelLabel="Continue editing" confirmLabel="Save and leave" confirmVariant="default"
      loading={override.isPending} onConfirm={() => void save()}
      secondaryLabel="Discard and leave" secondaryVariant="destructiveOutline"
      onSecondary={() => { setSelected(new Set(baseline)); onDirtyChange(false); if (blocker.status === "blocked") blocker.proceed(); }} />
  </section>;
}
