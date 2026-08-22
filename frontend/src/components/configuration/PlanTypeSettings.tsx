import { useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { useUpdatePlan } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";
import type { PlanDetail } from "@/types";
import { toast } from "sonner";

export function PlanTypeSettings({ plans }: { plans: PlanDetail[] }) {
  if (plans.length === 0) return null;
  return (
    <section className="rounded-lg border border-border bg-card p-3">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Plan types</h3>
        <span className="text-xs text-muted-foreground">
          {plans.length} plan type{plans.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="grid gap-2">
        {plans.map((plan) => (
          <PlanTypeRow key={plan.id} plan={plan} />
        ))}
      </div>
    </section>
  );
}

function PlanTypeRow({ plan }: { plan: PlanDetail }) {
  const updatePlan = useUpdatePlan();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(plan.display_name || plan.code);
  const [reportLabel, setReportLabel] = useState(plan.report_label ?? "");

  useEffect(() => {
    setName(plan.display_name || plan.code);
    setReportLabel(plan.report_label ?? "");
  }, [plan]);

  const save = async () => {
    const nextName = name.trim();
    const nextReportLabel = reportLabel.trim();
    if (!nextName) return;
    const patch: Partial<PlanDetail> = {};
    if (nextName !== plan.display_name) patch.display_name = nextName;
    if (nextReportLabel !== (plan.report_label ?? "")) {
      patch.report_label = nextReportLabel || null;
    }
    if (Object.keys(patch).length === 0) {
      setEditing(false);
      return;
    }
    try {
      await updatePlan.mutateAsync({ id: plan.id, patch });
      toast.success("Plan type saved");
      setEditing(false);
    } catch (error) {
      toast.error(`Plan type: ${formatError(error)}`);
    }
  };

  return (
    <div className="grid grid-cols-[minmax(10rem,0.8fr)_minmax(16rem,1.5fr)_auto] items-center gap-2 rounded-md bg-muted/40 px-3 py-2">
      {editing ? (
        <>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-label="Plan type name"
            className="h-8 text-sm"
          />
          <Input
            value={reportLabel}
            onChange={(event) => setReportLabel(event.target.value)}
            placeholder="Insurer report label"
            aria-label="Insurer report label"
            className="h-8 text-sm"
          />
          <div className="flex items-center gap-1">
            <Button size="icon-sm" variant="ghost" onClick={save}>
              <Check className="size-3.5" />
              <span className="sr-only">Save plan type</span>
            </Button>
            <Button size="icon-sm" variant="ghost" onClick={() => setEditing(false)}>
              <X className="size-3.5" />
              <span className="sr-only">Cancel plan type edit</span>
            </Button>
          </div>
        </>
      ) : (
        <>
          <span className="truncate text-sm font-medium text-foreground">
            {plan.display_name || plan.code}
          </span>
          <span className="truncate text-sm text-muted-foreground">
            {plan.report_label || "No insurer report label"}
          </span>
          <Button size="icon-sm" variant="ghost" onClick={() => setEditing(true)}>
            <Pencil className="size-3.5" />
            <span className="sr-only">Edit plan type</span>
          </Button>
        </>
      )}
    </div>
  );
}
