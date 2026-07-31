import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePatchCategory, usePlans } from "@/api/hooks";
import { formatError } from "@/lib/errors";
import type { Category, PlanAssignment, PlanDetail, RateModel } from "@/types";
import { toast } from "sonner";

// The Dependant configuration section: one card per employee category, mirroring
// the "Employee Category & Plan Type" cards but for the dependant's participation
// + rate. Rendered below the employee section when Spouse/Child is ticked in
// Member Cover Eligibility.
export function DependantCards({
  policyYearId,
  productId,
  rateModel,
  categories,
}: {
  policyYearId: string;
  productId: string | null;
  rateModel: RateModel;
  categories: Category[];
}) {
  const { data: plans } = usePlans(policyYearId, productId ?? undefined);
  const planOptions = useMemo(() => plans?.items ?? [], [plans]);

  if (categories.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Add an employee category first — dependant cover mirrors it.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        Each card mirrors an employee category. Set whether dependant cover is
        compulsory (auto-included) or voluntary (an opt-in flex add), and the
        per-dependant rate. Dependants ride the employee's plan.
      </p>
      {categories.map((c) => (
        <DependantCard
          key={`${c.id}:${c.updated_at}`}
          category={c}
          planOptions={planOptions}
          rateModel={rateModel}
        />
      ))}
    </div>
  );
}

// Dependant configuration for one employee category. Dependants ride the
// employee's plan (read-only mirror); only their participation and per-dependant
// rate are editable here. Everything writes back onto the SAME Category row
// (participation_detail.dependant + plan_assignments.dependant_rate), so coverage
// and flex pricing read it off the employee category as they already do.
export function DependantCard({
  category,
  planOptions,
  rateModel,
}: {
  category: Category;
  planOptions: PlanDetail[];
  rateModel: RateModel;
}) {
  const patch = usePatchCategory();
  const assignments = (category.plan_assignments ?? {}) as PlanAssignment;
  const planCode = assignments.plan_code != null ? String(assignments.plan_code) : "";
  const planName =
    planOptions.find((p) => String(p.code) === planCode)?.display_name ||
    planCode ||
    "—";

  const [participation, setParticipation] = useState(
    category.participation_detail?.dependant ?? "",
  );
  const [rate, setRate] = useState(
    assignments.dependant_rate != null ? String(assignments.dependant_rate) : "",
  );

  const savePatch = (p: Partial<Category>, label: string) =>
    patch.mutate(
      { id: category.id, patch: p },
      { onError: (e) => toast.error(`${label}: ${formatError(e)}`) },
    );

  // Merge-preserve the employee/direction split when writing the dependant scope.
  const saveParticipation = (v: "compulsory" | "voluntary") => {
    setParticipation(v);
    savePatch(
      {
        participation_detail: { ...(category.participation_detail ?? {}), dependant: v },
      },
      "Dependant participation",
    );
  };

  const saveRate = () => {
    const cur = assignments.dependant_rate ?? null;
    const trimmed = rate.trim();
    const n = Number(trimmed);
    if (trimmed === "" || !Number.isFinite(n)) {
      setRate(cur != null ? String(cur) : "");
      return;
    }
    if (n === cur) return;
    savePatch(
      {
        plan_assignments: {
          ...assignments,
          dependant_rate: n,
        } as Category["plan_assignments"],
      },
      "Premium rate per dependant",
    );
  };

  // Tiered medical encodes dependants in the EO/ES/EC/EF tiers on the employee
  // card, so there's no separate dependant rate to enter here.
  const showRate = rateModel !== "tiered";

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="grid grid-cols-[1.6fr_1fr_1fr] items-end gap-3">
        <ReadOnlyField label="Employee Category" value={category.display_name} />
        <ReadOnlyField label="Plan Type" value={planName} />
        <Field label="Dependant Participation">
          <Select
            value={participation || ""}
            onValueChange={(v) => saveParticipation(v as "compulsory" | "voluntary")}
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue placeholder="Select…" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="compulsory">Compulsory</SelectItem>
              <SelectItem value="voluntary">Voluntary</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
        {showRate ? (
          <Field label="Premium Rate Per Dependant">
            <Input
              type="number"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              onBlur={saveRate}
              placeholder="e.g. 396.90"
              className="h-8 w-44 text-sm"
            />
          </Field>
        ) : (
          <p className="text-2xs text-muted-foreground">
            Dependant premiums are set in the EO/ES/EC/EF tier rates on the
            employee card.
          </p>
        )}
        <p className="w-full text-2xs text-muted-foreground">
          {participation === "voluntary"
            ? "Voluntary — the member opts in to cover dependants, drawing down flex dollars."
            : participation === "compulsory"
              ? "Compulsory — dependants are automatically covered (no flex drawdown)."
              : "Set whether dependant cover is automatic (compulsory) or an opt-in flex add (voluntary)."}
        </p>
      </div>
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

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return (
    <Field label={label}>
      <div className="flex h-8 items-center truncate text-sm text-muted-foreground">
        {value}
      </div>
    </Field>
  );
}
