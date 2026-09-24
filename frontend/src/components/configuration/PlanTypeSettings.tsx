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
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const selectedPlan = plans.find((plan) => plan.id === selectedPlanId);
  return (
    <section className="rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-1 text-sm font-semibold text-foreground">Plan types:</h3>
        {plans.length > 0 && (
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Plan types">
            {plans.map((plan) => (
              <Button
                key={plan.id}
                size="sm"
                variant={selectedPlanId === plan.id ? "secondary" : "outline"}
                className="max-w-full rounded-full"
                aria-expanded={selectedPlanId === plan.id}
                aria-controls={selectedPlanId === plan.id ? "plan-type-editor" : undefined}
                title={plan.report_label ? `Insurer report label: ${plan.report_label}` : undefined}
                onClick={() => {
                  setAdding(false);
                  setSelectedPlanId((current) => current === plan.id ? null : plan.id);
                }}
              >
                <span className="truncate">{plan.display_name || plan.code}</span>
                <Pencil className="size-3 shrink-0" aria-hidden="true" />
                <span className="sr-only">Edit plan type</span>
              </Button>
            ))}
          </div>
        )}
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={!productId || adding}
          onClick={() => {
            setSelectedPlanId(null);
            setAdding(true);
          }}
        >
          <Plus className="size-3.5" /> Add plan type
        </Button>
      </div>
      {!adding && !selectedPlan && plans.length === 0 && (
        <p className="mt-3 text-sm text-muted-foreground">
          No plan types yet. Add one before assigning employee categories.
        </p>
      )}
      {adding && productId && (
        <NewPlanTypeRow
          plans={plans}
          policyYearId={policyYearId}
          productId={productId}
          onClose={() => setAdding(false)}
        />
      )}
      {selectedPlan && (
        <PlanTypeEditor
          key={selectedPlan.id}
          plan={selectedPlan}
          onClose={() => setSelectedPlanId(null)}
        />
      )}
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
    <div className="mt-3 grid gap-2 rounded-md bg-muted/40 p-3 sm:grid-cols-[minmax(10rem,1fr)_minmax(12rem,1.5fr)_auto] sm:items-center">
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

function PlanTypeEditor({ plan, onClose }: { plan: PlanDetail; onClose: () => void }) {
  const updatePlan = useUpdatePlan();
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
      onClose();
      return;
    }
    try {
      await updatePlan.mutateAsync({ id: plan.id, patch });
      toast.success("Plan type saved");
      onClose();
    } catch (error) {
      toast.error(`Plan type: ${formatError(error)}`);
    }
  };

  return (
    <div id="plan-type-editor" className="mt-3 grid gap-2 rounded-md bg-muted/40 p-3 sm:grid-cols-[minmax(10rem,1fr)_minmax(12rem,1.5fr)_auto] sm:items-center">
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
        <Button size="icon-sm" variant="ghost" onClick={save} disabled={updatePlan.isPending || !name.trim()}>
          <Check className="size-3.5" />
          <span className="sr-only">Save plan type</span>
        </Button>
        <Button size="icon-sm" variant="ghost" onClick={onClose} disabled={updatePlan.isPending}>
          <X className="size-3.5" />
          <span className="sr-only">Cancel plan type edit</span>
        </Button>
      </div>
    </div>
  );
}
