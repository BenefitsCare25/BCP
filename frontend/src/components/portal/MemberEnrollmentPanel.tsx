/** "My enrollment" — the member's own election panel, rendered by the portal
 * page (interactive) and the broker's employee-view preview (readOnly). Builds
 * on the SAME shared election components as the broker elections page, so the
 * member sees exactly the tiers, directions and flex prices a broker would
 * elect on their behalf. */
import { useEffect, useMemo, useState } from "react";
import { CalendarClock, CheckCircle2, Loader2, Lock, Send } from "lucide-react";
import { toast } from "sonner";
import type { ElectionIn, ProductTierSet } from "@/api/enrollment";
import type { PortalEnrollmentData } from "@/api/portal";
import {
  type DependantRef,
  ElectionProductCard,
  FlexBalanceStrip,
  LeaveTradingCard,
  type ProductState,
  buildElectionsPayload,
  computeFlex,
  seedElectionState,
} from "@/components/enrollment/electionShared";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";

const STATUS_LABEL: Record<string, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  submitted: "Submitted",
  confirmed: "Confirmed",
  deemed: "Finalized",
  declined: "Declined",
};

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Baseline-only state for a preview where no enrollment row exists yet. */
function baselineState(tierSets: ProductTierSet[]): Record<string, ProductState> {
  const next: Record<string, ProductState> = {};
  for (const ts of tierSets) {
    const baseline = ts.tiers.find((t) => t.is_baseline) ?? ts.tiers[0];
    next[ts.product_code] = {
      productCode: ts.product_code,
      tierKey: baseline?.key ?? "",
      declined: false,
      dependantIds: [],
      depOptionIds: {},
    };
  }
  return next;
}

export function MemberEnrollmentPanel({
  data,
  dependants,
  readOnly = false,
  onSaveElections,
  onSaveLeave,
  onSubmit,
  saving = false,
  savingLeave = false,
  submitting = false,
}: {
  data: PortalEnrollmentData;
  dependants: DependantRef[];
  readOnly?: boolean;
  onSaveElections?: (elections: ElectionIn[]) => Promise<unknown>;
  onSaveLeave?: (input: { action: string; days: number }) => Promise<unknown>;
  onSubmit?: (acknowledgeUnpriced: boolean) => Promise<unknown>;
  saving?: boolean;
  savingLeave?: boolean;
  submitting?: boolean;
}) {
  const { window, enrollment, options } = data;

  const productScopeSet = useMemo(
    () =>
      window?.product_scope?.length ? new Set(window.product_scope) : null,
    [window],
  );
  const tierSets = useMemo<ProductTierSet[]>(() => {
    const all = options?.products ?? [];
    return productScopeSet
      ? all.filter((p) => productScopeSet.has(p.product_code))
      : all;
  }, [options, productScopeSet]);

  const [state, setState] = useState<Record<string, ProductState>>({});
  const [leaveAction, setLeaveAction] = useState("none");
  const [leaveDays, setLeaveDays] = useState("0");
  const [unpricedProducts, setUnpricedProducts] = useState<string[] | null>(null);

  useEffect(() => {
    if (!options) return;
    setState(
      enrollment ? seedElectionState(enrollment, tierSets) : baselineState(tierSets),
    );
    setLeaveAction(enrollment?.leave?.action ?? "none");
    setLeaveDays(String(enrollment?.leave?.days ?? 0));
  }, [enrollment, options, tierSets]);

  if (!window) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <CalendarClock className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium text-foreground">
          No enrollment period is open right now
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          When your company opens an enrollment window you can review your
          plans here and choose to upgrade, downgrade or add dependants.
        </p>
      </div>
    );
  }

  const status = enrollment?.status ?? "not_started";
  const finalized = status === "confirmed" || status === "deemed";
  const submitted = status === "submitted";
  const disabled = readOnly || finalized;
  const allowDeps = window.allow_dependant_changes;

  const flex = computeFlex(
    options ?? undefined, tierSets, state, dependants, allowDeps, leaveAction, leaveDays,
  );
  const submitBlocked = !!flex && flex.balance < -0.005 && !window.allow_overdraft;

  async function saveElections() {
    if (!onSaveElections) return;
    try {
      await onSaveElections(buildElectionsPayload(state, tierSets, dependants, allowDeps));
      toast.success("Your choices are saved.");
    } catch (e) {
      toast.error(formatError(e));
    }
  }

  async function doSubmit(acknowledgeUnpriced: boolean) {
    if (!onSubmit) return;
    try {
      await onSubmit(acknowledgeUnpriced);
      setUnpricedProducts(null);
      toast.success("Submitted — your broker will review and confirm.");
    } catch (e) {
      if (e instanceof ConflictDetailError) {
        if (e.detail.code === "unpriced_elections") {
          setUnpricedProducts(
            Array.isArray(e.detail.products) ? (e.detail.products as string[]) : [],
          );
          return;
        }
        if (e.detail.code === "flex_overdrawn") {
          const balance = e.detail.balance;
          toast.error(
            `Your choices exceed your flex wallet${
              typeof balance === "number"
                ? ` by ${fmtCurrency(Math.abs(balance))}`
                : ""
            }. Reduce them before submitting.`,
          );
          return;
        }
      }
      toast.error(formatError(e));
    }
  }

  return (
    <div className="space-y-4">
      {/* Window header */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-foreground">{window.name}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Make your changes before{" "}
              <span className="font-medium text-foreground">
                {fmtDate(window.closes_at)}
              </span>
              {" — after that your current selections are locked in."}
            </p>
          </div>
          <Badge variant={finalized ? "good" : submitted ? "primary" : "outline"}>
            {STATUS_LABEL[status] ?? status}
          </Badge>
        </div>
      </div>

      {submitted && !readOnly && (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" />
          <p>
            Your choices were submitted
            {enrollment?.submitted_at
              ? ` on ${fmtDate(enrollment.submitted_at)}`
              : ""}{" "}
            and are awaiting your broker's confirmation. You can still change
            them until the window closes.
          </p>
        </div>
      )}
      {finalized && (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
          <Lock className="mt-0.5 size-4 shrink-0" />
          <p>
            Your enrollment has been finalized and your coverage updated.
            Contact your broker or HR if something needs to change.
          </p>
        </div>
      )}

      {/* Flex wallet balance */}
      {flex && (
        <FlexBalanceStrip
          flex={flex}
          allowOverdraft={window.allow_overdraft}
          shortfallHint="Your choices exceed your flex wallet. Reduce them to submit, or contact your broker."
        />
      )}

      {/* Per-product elections — the member's own cohort tiers only */}
      <div className="space-y-2">
        {tierSets.map((ts) => {
          const ps = state[ts.product_code];
          if (!ps) return null;
          return (
            <ElectionProductCard
              key={ts.product_code}
              ts={ts}
              ps={ps}
              disabled={disabled}
              allowDeps={allowDeps}
              dependants={dependants}
              flexOnChange={!!flex?.onChange}
              onChange={(next) =>
                setState((s) => ({ ...s, [ts.product_code]: next }))
              }
            />
          );
        })}
        {!tierSets.length && (
          <p className="text-sm text-muted-foreground">
            There are no plans available for you to change in this window.
          </p>
        )}
      </div>

      {/* Leave trading */}
      {window.allow_leave && (
        <LeaveTradingCard
          action={leaveAction}
          days={leaveDays}
          disabled={disabled}
          saving={savingLeave}
          onActionChange={setLeaveAction}
          onDaysChange={setLeaveDays}
          onSave={() => {
            if (!onSaveLeave) return;
            onSaveLeave({ action: leaveAction, days: Number(leaveDays) })
              .then(() => toast.success("Leave choice saved."))
              .catch((e) => toast.error(formatError(e)));
          }}
        />
      )}

      {/* Actions */}
      {!disabled && (
        <div className="flex items-center gap-2">
          <Button onClick={() => void saveElections()} disabled={saving}>
            {saving && <Loader2 className="size-4 animate-spin" />}
            Save my choices
          </Button>
          <Button
            variant="outline"
            disabled={submitting || submitBlocked}
            title={
              submitBlocked
                ? "Your choices exceed your flex wallet — reduce them to submit"
                : undefined
            }
            onClick={() => void doSubmit(false)}
          >
            <Send className="size-4" /> Submit for confirmation
          </Button>
        </div>
      )}
      {readOnly && !finalized && (
        <p className="text-xs text-muted-foreground">
          Members save their choices and submit them here; your broker then
          confirms to apply the changes.
        </p>
      )}

      <AlertDialog
        open={unpricedProducts !== null}
        onOpenChange={(open) => {
          if (!open) setUnpricedProducts(null);
        }}
        title="Some choices have no flex price yet"
        description={`${
          unpricedProducts?.length
            ? `These plans change your coverage but don't have a flex price configured yet, so they would draw $0 from your wallet: ${unpricedProducts.join(", ")}. `
            : ""
        }You can submit anyway, or check with your broker first.`}
        confirmLabel="Submit anyway"
        confirmVariant="default"
        loading={submitting}
        onConfirm={() => void doSubmit(true)}
      />
    </div>
  );
}
