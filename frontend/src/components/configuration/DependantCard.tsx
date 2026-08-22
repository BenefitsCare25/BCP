import type { ReactNode } from "react";
import { useState } from "react";
import { usePatchCategory } from "@/api/hooks";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatError } from "@/lib/errors";
import type { Category, PlanAssignment, RateModel } from "@/types";
import { toast } from "sonner";

type DependantParticipation = "not_covered" | "compulsory" | "voluntary";

export function DependantAssignmentFields({
  category,
  rateModel,
}: {
  category: Category;
  rateModel: RateModel;
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

  return (
    <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
      <Field label="Dependant Participation">
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
      </Field>
      {rateModel !== "tiered" ? (
        <Field label="Premium Rate Per Dependant">
          <Input
            type="number"
            value={rate}
            onChange={(event) => setRate(event.target.value)}
            onBlur={saveRate}
            disabled={participation === "not_covered"}
            className="h-8 w-44 text-sm"
          />
        </Field>
      ) : (
        <p className="text-xs text-muted-foreground">
          Dependant premiums use the EO, ES, EC and EF tier rates above.
        </p>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}
