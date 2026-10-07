import { useState } from "react";
import { usePatchCategory } from "@/api/hooks";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import type { Category, PlanAssignment, RateModel } from "@/types";
import { toast } from "sonner";
import { assignmentGaps, type AssignmentGapContext } from "./setup/setupGaps";
import { AssignmentField } from "./setup/SetupPrimitives";

type DependantParticipation = "not_covered" | "compulsory" | "voluntary";

export function DependantAssignmentFields({
  category,
  rateModel,
  gapContext,
}: {
  category: Category;
  rateModel: RateModel;
  /** The card's checklist context, so this mark agrees with the checklist. */
  gapContext: AssignmentGapContext;
}) {
  const patch = usePatchCategory();
  const assignments = (category.plan_assignments ?? {}) as PlanAssignment;
  const [participation, setParticipation] = useState<DependantParticipation>(
    category.participation_detail?.dependant ?? "not_covered",
  );
  const [rate, setRate] = useState(
    assignments.dependant_rate != null ? String(assignments.dependant_rate) : "",
  );

  const saveParticipation = (value: DependantParticipation) => {
    setParticipation(value);
    patch.mutate(
      {
        id: category.id,
        patch: {
          participation_detail: {
            ...(category.participation_detail ?? {}),
            dependant: value === "not_covered" ? null : value,
          },
        },
      },
      {
        onError: (error) =>
          toast.error(`Dependant participation: ${formatError(error)}`),
      },
    );
  };

  const saveRate = () => {
    const trimmed = rate.trim();
    const next = Number(trimmed);
    if (trimmed === "" || !Number.isFinite(next)) {
      setRate(
        assignments.dependant_rate != null
          ? String(assignments.dependant_rate)
          : "",
      );
      return;
    }
    if (next === assignments.dependant_rate) return;
    patch.mutate(
      {
        id: category.id,
        patch: {
          plan_assignments: { ...assignments, dependant_rate: next },
        },
      },
      {
        onError: (error) =>
          toast.error(`Premium rate per dependant: ${formatError(error)}`),
      },
    );
  };

  const typedRate = Number(rate);
  const rateMissing = assignmentGaps(
    {
      ...category,
      participation_detail: {
        ...(category.participation_detail ?? {}),
        dependant: participation === "not_covered" ? null : participation,
      },
      plan_assignments: {
        ...assignments,
        dependant_rate: rate.trim() !== "" && Number.isFinite(typedRate) ? typedRate : null,
      },
    } as Category,
    gapContext,
  ).some((gap) => gap.field === "dependant_rate");

  return (
    <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
      <AssignmentField label="Dependant Participation">
        <Select
          value={participation}
          onValueChange={(value) =>
            saveParticipation(value as DependantParticipation)
          }
        >
          <SelectTrigger className="h-8 w-44 text-sm">
            <SelectValue placeholder="Select" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="not_covered">Not covered</SelectItem>
            <SelectItem value="compulsory">Compulsory</SelectItem>
            <SelectItem value="voluntary">Voluntary</SelectItem>
          </SelectContent>
        </Select>
      </AssignmentField>
      {rateModel !== "tiered" ? (
        <AssignmentField label="Premium Rate Per Dependant" missing={rateMissing}>
          <Input
            type="number"
            value={rate}
            onChange={(event) => setRate(event.target.value)}
            onBlur={saveRate}
            disabled={participation === "not_covered"}
            aria-invalid={rateMissing || undefined}
            className={cn("h-8 w-44 text-sm", rateMissing && "border-warn")}
          />
        </AssignmentField>
      ) : (
        <p className="text-xs text-muted-foreground">
          Dependant premiums use the EO, ES, EC and EF tier rates above.
        </p>
      )}
    </div>
  );
}

