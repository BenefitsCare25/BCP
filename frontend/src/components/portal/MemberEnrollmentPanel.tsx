/** "My enrollment" — the member's own election panel, rendered by the portal
 * page (interactive) and the broker's employee-view preview (`readOnly`).
 *
 * It is composed of the portal's own primitives — `Mount`, `Field`, `Action`,
 * `Money`, `Strike` — not of the broker elections page's cards. Those cards
 * used to serve both surfaces by branching on `memberLabels` / `useInLeaf()`,
 * which is where the "44px controls leaked onto the broker page" and
 * "member dropdown rendered in broker tokens" defects came from, and it left
 * this route as the one member surface still drawn in the broker's radius,
 * type scale and vocabulary.
 *
 * What the two surfaces still share is `enrollment/electionCore.ts` — tier
 * resolution, dependant pricing, the flex arithmetic, the leave bounds and the
 * PUT payload. That is the part that has to agree, and now it is the only part
 * that can. */
import { useEffect, useMemo, useState } from "react";
import { Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import type { ElectionIn, ProductTierSet } from "@/api/enrollment";
import type { PortalEnrollmentData } from "@/api/portal";
import {
  type DependantRef,
  type ProductState,
  baselineElectionState,
  buildElectionsPayload,
  computeFlex,
  seedElectionState,
} from "@/components/enrollment/electionCore";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Action } from "@/components/portal/leaf/Action";
import { Mount, glassSurface } from "@/components/portal/leaf/Mount";
import { Strike } from "@/components/portal/leaf/Strike";
import { currencySymbol } from "@/components/portal/leaf/Figure";
import { formatDay } from "@/components/portal/leaf/date";
import { LeaveMount } from "@/components/portal/enrollment/LeaveMount";
import { ProductElectionMount } from "@/components/portal/enrollment/ProductElectionMount";
import { WalletMount } from "@/components/portal/enrollment/WalletMount";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtAmount } from "@/lib/format";
import { cn } from "@/lib/cn";

/** Where the member's enrollment stands, struck onto the page.
 *
 * A state on this surface is STRUCK, never badged (The Ink-Over-Tint Rule) —
 * the same construction the claims list uses, so "Sent" here and "Under review"
 * there read as one vocabulary. Rendered in the PREVIEW too: it is a statement
 * of where the enrollment stands rather than an action, and gated on
 * `!readOnly` it made a submitted enrollment indistinguishable from an
 * untouched one, which is the one thing the preview exists to show. */
function StatusNote({
  mark,
  tone,
  children,
}: {
  mark: string;
  tone: "approved" | "review";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        glassSurface,
        "flex flex-col gap-2 rounded-control p-3 sm:flex-row sm:items-baseline sm:gap-3",
      )}
    >
      <Strike tone={tone} className="shrink-0">
        {mark}
      </Strike>
      <p className="text-row text-label">{children}</p>
    </div>
  );
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
  // The ISO code, handed to `Money`, which resolves it to the symbol a member
  // writes ("SGD" → "S$"). Only the two places that compose money into a plain
  // STRING — a toast and a dialog description, neither of which can hold a
  // component — resolve it themselves.
  const currency = options?.flex_currency ?? null;
  const money = currencySymbol(currency);

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
      enrollment
        ? seedElectionState(enrollment, tierSets)
        : baselineElectionState(tierSets),
    );
    setLeaveAction(enrollment?.leave?.action ?? "none");
    setLeaveDays(String(enrollment?.leave?.days ?? 0));
  }, [enrollment, options, tierSets]);

  if (!window) {
    return (
      <Mount label="Nothing to choose right now">
        <p className="text-row text-label">
          When your company next opens a benefit selection period, this is where
          you&rsquo;ll change your plan, cover your family or trade leave.
          You&rsquo;ll see a marker on this section when it opens.
        </p>
      </Mount>
    );
  }

  const status = enrollment?.status ?? "not_started";
  const finalized = status === "confirmed" || status === "deemed";
  const submitted = status === "submitted";
  const disabled = readOnly || finalized;
  const allowDeps = window.allow_dependant_changes;

  const flex = computeFlex(
    options ?? undefined, tierSets, state, dependants, allowDeps, leaveAction, leaveDays,
    options?.leave ?? null,
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
      toast.success("Sent — you'll be told once it's confirmed.");
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
            `Your choices exceed your allowance${
              typeof balance === "number"
                ? ` by ${money}${fmtAmount(Math.abs(balance))}`
                : ""
            }. Reduce them before sending.`,
          );
          return;
        }
      }
      toast.error(formatError(e));
    }
  }

  return (
    // One column of siblings, deliberately not nested groups: `leaf-rise`
    // staggers a mount's entrance by `:nth-of-type` among its siblings, so
    // wrapping the products in their own div would restart the stagger at the
    // first product and fire it against the wallet above.
    <div className="space-y-4">
      {/* The deadline, and nothing else.
          `window.name` is the broker's internal label for the window ("Testing
          2026") — it names a row in their tool, not anything the member has or
          needs. The status notes below carry the same date when they apply, so
          this is printed only while it is still the thing that changes what a
          member does today. */}
      {!submitted && !finalized && (
        <p className="text-row text-record">
          Make your changes before{" "}
          <span className="font-semibold">{formatDay(window.closes_at)}</span>
          {" — after that your current selections are locked in."}
        </p>
      )}

      {submitted && (
        <StatusNote mark="Sent" tone="review">
          Your choices were sent
          {enrollment?.submitted_at
            ? ` on ${formatDay(enrollment.submitted_at)}`
            : ""}{" "}
          and are being checked. You can still change them until{" "}
          {formatDay(window.closes_at)}.
        </StatusNote>
      )}
      {finalized && (
        <StatusNote mark="Confirmed" tone="approved">
          These choices are confirmed and your cover is updated. Contact your HR
          team if something needs to change.
        </StatusNote>
      )}

      {flex && (
        <WalletMount flex={flex} allowOverdraft={window.allow_overdraft} />
      )}

      {tierSets.map((ts) => {
        const ps = state[ts.product_code];
        if (!ps) return null;
        return (
          <ProductElectionMount
            key={ts.product_code}
            ts={ts}
            ps={ps}
            disabled={disabled}
            allowDeps={allowDeps}
            dependants={dependants}
            flexOnChange={!!flex?.onChange}
            currency={currency}
            onChange={(next) =>
              setState((s) => ({ ...s, [ts.product_code]: next }))
            }
          />
        );
      })}
      {!tierSets.length && (
        <Mount label="No plans to change">
          <p className="text-row text-label">
            This period doesn&rsquo;t include any plan you can change. If you
            were expecting a choice here, your HR team can tell you why.
          </p>
        </Mount>
      )}

      {window.allow_leave && (
        <LeaveMount
          action={leaveAction}
          daysValue={leaveDays}
          leave={options?.leave ?? null}
          ratePerDay={options?.member_leave_rate ?? null}
          currency={currency}
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

      {/* Two full-height actions, stacked on a phone. Saving keeps the work;
          sending is what starts the check — so the sentence stating the blocker
          sits with the button rather than in a `title` attribute, which a touch
          device never shows. */}
      {!disabled && (
        <div className="space-y-2">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Action
              block="phone"
              onClick={() => void saveElections()}
              disabled={saving}
            >
              {saving && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Save my choices
            </Action>
            {/* The page's one brand-coloured fill. */}
            <Action
              tone="primary"
              block="phone"
              disabled={submitting || submitBlocked}
              onClick={() => void doSubmit(false)}
            >
              <Send className="size-4" aria-hidden /> Send them in
            </Action>
          </div>
          {submitBlocked && (
            <p className="text-row text-strike-pending">
              Your choices cost more than your allowance — reduce them before
              sending.
            </p>
          )}
        </div>
      )}
      {readOnly && !finalized && (
        <p className="text-row text-label">
          Members save and send their choices here; a broker then confirms them
          to apply the changes.
        </p>
      )}

      <AlertDialog
        open={unpricedProducts !== null}
        onOpenChange={(open) => {
          if (!open) setUnpricedProducts(null);
        }}
        title="Some choices have no price yet"
        // The member's symbol and the member's people: `$0` contradicted the
        // `S$` every other figure on this surface uses, and "your broker" is
        // broker vocabulary — a member's route to a question is their HR team,
        // which is who the rest of the portal names.
        description={`${
          unpricedProducts?.length
            ? `These plans change your coverage but don't have a price set yet, so they would draw ${money}0 from your allowance: ${unpricedProducts.join(", ")}. `
            : ""
        }You can send them anyway, or check with your HR team first.`}
        confirmLabel="Send anyway"
        confirmVariant="default"
        loading={submitting}
        onConfirm={() => void doSubmit(true)}
      />
    </div>
  );
}
