/** Launch readiness for the benefit year being viewed: blockers, the employee
 * categories still awaiting confirmation, and the lifecycle actions. */
import { useState } from "react";
import { Archive, CheckCircle2, Rocket } from "lucide-react";
import { toast } from "sonner";
import { useBulkConfirmCategories } from "@/api/categories";
import {
  useArchivePolicyYear,
  useEligibilityMappings,
  usePolicyYearReadiness,
  useSetCurrentPolicyYear,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import type { EligibilityMappingItem, PolicyYear } from "@/types";

function countByProduct(items: EligibilityMappingItem[]): string {
  const counts = new Map<string, number>();
  for (const item of items) {
    const code = item.product_code ?? "Unassigned";
    counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  return [...counts].sort(([a], [b]) => a.localeCompare(b))
    .map(([code, count]) => `${code} ${count}`).join(" · ");
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function PendingCategoriesSummary({ confirmable, unresolved }: {
  confirmable: EligibilityMappingItem[];
  unresolved: EligibilityMappingItem[];
}) {
  return (
    <div className="space-y-2 text-sm text-foreground">
      <p>
        {plural(confirmable.length, "employee category has", "employee categories have")} validated
        rules ready to confirm together.
        {unresolved.length > 0 &&
          ` ${plural(unresolved.length, "needs", "need")} individual review.`}
      </p>
      {unresolved.length > 0 && (
        <details className="text-muted-foreground">
          <summary className="cursor-pointer font-medium text-foreground">
            Review categories needing attention
          </summary>
          <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-auto pl-5">
            {unresolved.map((item) => (
              <li key={item.category_id}>
                {item.product_code ?? "Unassigned"}: {item.display_name}
                {item.category_status === "draft"
                  ? " — draft; open it to finish the rule"
                  : item.errors[0] && ` — ${item.errors[0]}`}
              </li>
            ))}
          </ul>
          <p className="mt-2">
            Open each product&rsquo;s Employee Category &amp; Plan Type section to correct and
            confirm its rule.
          </p>
        </details>
      )}
    </div>
  );
}

function ConfirmValidatedDialog({ open, onOpenChange, policyYearId, confirmable, unresolvedCount, refreshing }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  policyYearId: string;
  confirmable: EligibilityMappingItem[];
  unresolvedCount: number;
  refreshing: boolean;
}) {
  const bulkConfirm = useBulkConfirmCategories();
  const expected = confirmable.length;

  const confirm = async () => {
    try {
      const result = await bulkConfirm.mutateAsync(policyYearId);
      onOpenChange(false);
      if (result.confirmed > 0) {
        toast.success(`${plural(result.confirmed, "employee category", "employee categories")} confirmed`);
      }
      // The list can go stale between opening the dialog and confirming (another
      // broker, a re-validation). Say so instead of letting the count disagree.
      const unaccounted = expected - result.confirmed - result.skipped_invalid_rules;
      if (result.skipped_invalid_rules > 0) {
        toast.warning(`${plural(result.skipped_invalid_rules, "rule", "rules")} failed the latest checks and need individual review`);
      }
      if (unaccounted > 0) {
        toast.warning(
          `${plural(unaccounted, "category", "categories")} changed since this list loaded and ${
            unaccounted === 1 ? "was" : "were"
          } not confirmed. Review the list again.`,
        );
      }
    } catch (error) {
      toast.error(formatError(error));
    }
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={onOpenChange}
      tone="info"
      title="Confirm validated employee categories?"
      description={
        <div className="space-y-3">
          <p>
            {plural(expected, "rule", "rules")} matched the uploaded employee listing and passed
            validation. Confirming assigns the corresponding benefit plans to matching employees.
          </p>
          <p className="font-medium text-foreground">{countByProduct(confirmable)}</p>
          <details>
            <summary className="cursor-pointer font-medium text-foreground">
              Inspect all {plural(expected, "rule", "rules")}
            </summary>
            <ul className="mt-2 max-h-48 space-y-2 overflow-auto rounded-md border border-border p-3">
              {confirmable.map((item) => (
                <li key={item.category_id}>
                  <span className="font-medium text-foreground">
                    {item.product_code ?? "Unassigned"}: {item.display_name}
                  </span>
                  {item.rule_human_readable && (
                    <span className="block">{item.rule_human_readable}</span>
                  )}
                  {item.matched_count != null && (
                    <span className="block">{item.matched_count} employees matched</span>
                  )}
                </li>
              ))}
            </ul>
          </details>
          <p>
            {unresolvedCount > 0 &&
              `${plural(unresolvedCount, "category still needs", "categories still need")} individual review and will remain unconfirmed. `}
            Any rule that fails the latest checks will also be skipped.
          </p>
        </div>
      }
      confirmLabel={`Confirm ${plural(expected, "rule", "rules")}`}
      confirmVariant="default"
      loading={bulkConfirm.isPending}
      confirmDisabled={expected === 0 || refreshing}
      onConfirm={confirm}
    />
  );
}

export function LaunchReadiness({ year, readOnly, onError }: {
  year: PolicyYear;
  readOnly: boolean;
  /** Surfaces a lifecycle failure in the panel's own error banner. */
  onError: (message: string | null) => void;
}) {
  const readiness = usePolicyYearReadiness(year.id);
  const setCurrent = useSetCurrentPolicyYear();
  const archive = useArchivePolicyYear();
  const [confirmOpen, setConfirmOpen] = useState(false);
  // Only an editable draft can bulk-confirm, so only there is the (heavy)
  // mapping summary worth fetching.
  const reviewable = !readOnly && year.status === "draft";
  const mapping = useEligibilityMappings(reviewable ? year.id : undefined);
  const pending = reviewable
    ? (mapping.data?.categories ?? []).filter((item) => item.category_status !== "confirmed")
    : [];
  const confirmable = pending.filter((item) => item.bulk_confirmable);
  const unresolved = pending.filter((item) => !item.bulk_confirmable);

  const runLifecycle = async (action: "live" | "archive") => {
    onError(null);
    try {
      if (action === "live") {
        await setCurrent.mutateAsync(year.id);
        toast.success("Benefit year is now live");
      } else {
        await archive.mutateAsync(year.id);
        toast.success("Benefit year archived");
      }
    } catch (error) {
      const message = formatError(error);
      onError(message);
      toast.error(message);
    }
  };

  return (
    <section className="rounded-lg border border-border bg-muted/30 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="size-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Launch readiness</h3>
          </div>
          {readiness.isLoading ? (
            <p className="text-sm text-muted-foreground">Checking configuration…</p>
          ) : readiness.isError ? (
            <p role="alert" className="text-sm text-error">
              Could not check launch readiness. {formatError(readiness.error)}
            </p>
          ) : readiness.data?.ready ? (
            <p className="text-sm text-good">Required configuration is complete.</p>
          ) : (
            <ul className="list-disc space-y-1 pl-5 text-sm text-error">
              {(readiness.data?.blockers ?? ["Readiness could not be confirmed."]).map(
                (blocker) => <li key={blocker}>{blocker}</li>,
              )}
            </ul>
          )}
          {readiness.data?.warnings.map((warning) => (
            <p key={warning} className="text-sm text-warn">{warning}</p>
          ))}
          {pending.length > 0 && (
            <PendingCategoriesSummary confirmable={confirmable} unresolved={unresolved} />
          )}
        </div>
        {!readOnly && (
          <div className="flex flex-wrap gap-2">
            {confirmable.length > 0 && (
              <Button size="sm" variant="outline" onClick={() => setConfirmOpen(true)}>
                Review {plural(confirmable.length, "validated rule", "validated rules")}
              </Button>
            )}
            {year.status !== "active" && (
              <Button
                size="sm"
                loading={setCurrent.isPending}
                disabled={!readiness.data?.ready}
                onClick={() => runLifecycle("live")}
              >
                <Rocket className="size-4" />
                Make live
              </Button>
            )}
            {year.status === "draft" && (
              <Button
                size="sm"
                variant="outline"
                loading={archive.isPending}
                onClick={() => runLifecycle("archive")}
              >
                <Archive className="size-4" />
                Archive
              </Button>
            )}
          </div>
        )}
      </div>
      {reviewable && (
        <ConfirmValidatedDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          policyYearId={year.id}
          confirmable={confirmable}
          unresolvedCount={unresolved.length}
          refreshing={mapping.isFetching}
        />
      )}
    </section>
  );
}
