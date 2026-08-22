import { useEffect, useState } from "react";
import { Check, Pencil, Plus, X } from "lucide-react";
import { useCreatePlan, useUpdatePlan } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";
import type { PlanDetail } from "@/types";
import { toast } from "sonner";

interface Props {
  plans: PlanDetail[];
  policyYearId: string;
  productId: string | null;
}

export function PlanTypeSettings({ plans, policyYearId, productId }: Props) {
  const [adding, setAdding] = useState(false);
  return (
    <section className="rounded-lg border border-border bg-card p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Plan types</h3>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            {plans.length} plan type{plans.length === 1 ? "" : "s"}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!productId || adding}
            onClick={() => setAdding(true)}
          >
            <Plus className="size-3.5" /> Add plan type
          </Button>
        </div>
      </div>
      <div className="grid gap-2">
        {adding && productId && (
          <NewPlanTypeRow
            plans={plans}
            policyYearId={policyYearId}
            productId={productId}
            onClose={() => setAdding(false)}
          />
        )}
        {plans.map((plan) => (
          <PlanTypeRow key={plan.id} plan={plan} />
        ))}
        {!adding && plans.length === 0 && (
          <p className="px-3 py-2 text-sm text-muted-foreground">
            No plan types yet. Add one before assigning employee categories.
          </p>
        )}
      </div>
    </section>
  );
}

function NewPlanTypeRow({
  plans,
  policyYearId,
  productId,
  onClose,
}: {
  plans: PlanDetail[];
  policyYearId: string;
  productId: string;
  onClose: () => void;
}) {
  const createPlan = useCreatePlan();
  const [name, setName] = useState(nextPlanName(plans));
  const [reportLabel, setReportLabel] = useState("");
  const save = async () => {
    if (!name.trim()) return;
    try {
      await createPlan.mutateAsync({
        product_id: productId,
        policy_year_id: policyYearId,
        display_name: name.trim(),
        report_label: reportLabel.trim() || null,
      });
      toast.success("Plan type added");
      onClose();
    } catch (error) {
      toast.error(`Plan type: ${formatError(error)}`);
    }
  };
  return (
    <div className="grid grid-cols-[minmax(10rem,0.8fr)_minmax(16rem,1.5fr)_auto] items-center gap-2 rounded-md bg-muted/40 px-3 py-2">
      <Input
        value={name}
        onChange={(event) => setName(event.target.value)}
        aria-label="New plan type name"
        className="h-8 text-sm"
      />
      <Input
        value={reportLabel}
        onChange={(event) => setReportLabel(event.target.value)}
        placeholder="Insurer report label"
        aria-label="New insurer report label"
        className="h-8 text-sm"
      />
      <div className="flex items-center gap-1">
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={save}
          disabled={createPlan.isPending || !name.trim()}
        >
          <Check className="size-3.5" />
          <span className="sr-only">Save new plan type</span>
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={onClose}
          disabled={createPlan.isPending}
        >
          <X className="size-3.5" />
          <span className="sr-only">Cancel new plan type</span>
        </Button>
      </div>
    </div>
  );
}

function nextPlanName(plans: PlanDetail[]): string {
  const used = new Set(
    plans.map((plan) =>
      (plan.display_name || plan.code).trim().toLowerCase(),
    ),
  );
  let number = plans.length + 1;
  while (used.has(`plan ${number}`)) number += 1;
  return `Plan ${number}`;
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
