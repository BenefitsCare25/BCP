/** State, validation and save for the period form. The component is layout
 * only; everything that decides what gets sent lives here. */
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  type DefaultBehavior,
  type EnrollmentWindow,
  type FlexDrawdownRule,
  type WindowCreate,
  type WindowPatch,
  useCreateWindow,
  useUpdateWindow,
} from "@/api/enrollment";
import { useCategoriesGrouped } from "@/api/hooks";
import { formatError } from "@/lib/errors";
import { fromLocalInput, toLocalInput } from "./periodMeta";

export type PeriodFormMode = "create" | "draft" | "open";

export interface PeriodFormState {
  name: string;
  opensAt: string;
  closesAt: string;
  allowPlanChange: boolean;
  allowDeps: boolean;
  allowLeave: boolean;
  selfService: boolean;
  defaultBehavior: DefaultBehavior;
  scope: string[] | null;
  usesFlex: boolean;
  drawdown: FlexDrawdownRule;
  overdraft: boolean;
}

function initialState(w: EnrollmentWindow | undefined): PeriodFormState {
  const now = new Date();
  return {
    name: w?.name ?? "",
    opensAt: toLocalInput(w?.opens_at ?? now),
    closesAt: toLocalInput(w?.closes_at ?? new Date(now.getTime() + 30 * 86_400_000)),
    allowPlanChange: w?.allow_plan_change ?? true,
    allowDeps: w?.allow_dependant_changes ?? true,
    allowLeave: w?.allow_leave ?? false,
    selfService: w?.member_self_service ?? true,
    defaultBehavior: w?.default_behavior ?? "deemed_keep_current",
    scope: w?.product_scope?.length ? w.product_scope : null,
    usesFlex: w?.uses_flex ?? false,
    drawdown: w?.flex_drawdown_rule ?? "full",
    overdraft: w?.allow_overdraft ?? false,
  };
}

function validate(f: PeriodFormState, opens: string | null, closes: string | null) {
  if (!f.name.trim()) return "Give the period a name.";
  if (!opens || !closes) return "Both the start and the deadline are required.";
  if (opens >= closes) return "The deadline must be after the start.";
  if (f.scope !== null && f.scope.length === 0) {
    return "Pick at least one product, or include all of them.";
  }
  return null;
}

/** Every field a draft accepts. */
function fullBody(f: PeriodFormState, opens: string, closes: string) {
  return {
    name: f.name.trim(),
    opens_at: opens,
    closes_at: closes,
    default_behavior: f.defaultBehavior,
    allow_plan_change: f.allowPlanChange,
    allow_leave: f.allowLeave,
    allow_dependant_changes: f.allowDeps,
    member_self_service: f.selfService,
    product_scope: f.scope,
    uses_flex: f.usesFlex,
    flex_drawdown_rule: f.drawdown,
    allow_overdraft: f.usesFlex && f.overdraft,
  };
}

/** Only what the server still accepts once members are inside the period. */
function openBody(f: PeriodFormState, w: EnrollmentWindow, closes: string): WindowPatch {
  return {
    name: f.name.trim(),
    closes_at: closes,
    member_self_service: f.selfService,
    allow_overdraft: w.uses_flex && f.overdraft,
  };
}

export function usePeriodForm({
  mode,
  window,
  policyYearId,
  onDone,
}: {
  mode: PeriodFormMode;
  window?: EnrollmentWindow;
  policyYearId: string;
  onDone: (w: EnrollmentWindow) => void;
}) {
  const [form, setForm] = useState<PeriodFormState>(() => initialState(window));
  const set = <K extends keyof PeriodFormState>(key: K, value: PeriodFormState[K]) =>
    setForm((s) => ({ ...s, [key]: value }));
  const create = useCreateWindow(policyYearId);
  const update = useUpdateWindow();
  const { data: groups } = useCategoriesGrouped(policyYearId);
  const productCodes = useMemo(
    () =>
      [...new Set((groups ?? []).map((g) => g.product_code))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [groups],
  );
  const opens = fromLocalInput(form.opensAt);
  const closes = fromLocalInput(form.closesAt);
  const error = validate(form, opens, closes);

  function submit() {
    if (error || !opens || !closes) return;
    const handlers = {
      onSuccess: (w: EnrollmentWindow) => {
        toast.success(mode === "create" ? "Enrolment period drafted." : "Period updated.");
        onDone(w);
      },
      onError: (e: unknown) => toast.error(formatError(e)),
    };
    if (mode === "create") {
      const body: WindowCreate = { window_type: "open", ...fullBody(form, opens, closes) };
      create.mutate(body, handlers);
    } else if (window) {
      const body =
        mode === "open" ? openBody(form, window, closes) : fullBody(form, opens, closes);
      update.mutate({ id: window.id, body }, handlers);
    }
  }

  return {
    form,
    set,
    error,
    submit,
    productCodes,
    locked: mode === "open",
    pending: create.isPending || update.isPending,
  };
}
