/** "My enrollment" — the member's own election panel, rendered by the portal
 * page (interactive) and the broker's employee-view preview (readOnly). Builds
 * on the SAME shared election components as the broker elections page, so the
 * member sees exactly the tiers, directions and flex prices a broker would
 * elect on their behalf. */
import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Loader2, Lock, Send } from "lucide-react";
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
import { Mount } from "@/components/portal/leaf/Mount";
import { actionClass } from "@/components/portal/leaf/Action";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtAmount } from "@/lib/format";
import { currencySymbol } from "@/components/portal/leaf/Figure";
import { productGloss } from "@/components/portal/leaf/glossary";

/** Leaf actions: 44px tall, printed rather than filled, matching the clinic
 * locator and the security page. The shared Button primitive tops out at 36px
 * and has no touch scale. */
/** The shared leaf actions — this panel used to carry its own copy of both
 * class strings, as did clinics and security, and the three had drifted.
 * `leafPrimaryAction` is the page's one brand fill: submitting the member's
 * choices. */
const leafAction = actionClass("quiet", { className: "px-4" });
const leafPrimaryAction = actionClass("primary", { className: "px-4" });

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
  // "S$" — the same symbol the coverage and claims leaves print, so a member
  // moving between tabs doesn't meet two currencies. Read from the scheme
  // rather than hardcoded; the shared election components default to the
  // broker's bare "$".
  const money = currencySymbol(options?.flex_currency);

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
      <Mount label="Nothing to choose right now">
        <p className="text-row text-label">
          When your company next opens a benefit selection period, this is where
          you'll change your plan, cover your family or trade leave. You'll see
          a marker on this section when it opens.
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
    <div className="space-y-4">
      {/* The deadline, and nothing else.
          `window.name` is the broker's internal label for the window ("Testing
          2026") — it names a row in their tool, not anything the member has or
          needs, and a member reading "Testing" about their own benefits has
          every reason to worry. The "Not started" chip went with it: it stated
          the absence of an action the page is already asking for, and the panel
          below shows their current selections either way. What's left is the
          only fact that changes what a member does today. */}
      <p className="text-row text-record">
        Make your changes before{" "}
        <span className="font-semibold">{fmtDate(window.closes_at)}</span>
        {" — after that your current selections are locked in."}
      </p>

      {/* Rendered in the PREVIEW too. It is a statement of where the member's
          enrollment stands, not an action, and the preview's whole contract is
          that a broker sees what the member sees — gated on `!readOnly` it made
          a submitted enrollment indistinguishable from an untouched one. */}
      {submitted && (
        <div className="flex items-start gap-2 rounded-control border border-hairline/75 bg-bar/70 p-3">
          <CheckCircle2
            className="mt-0.5 size-4 shrink-0 text-strike-approved"
            aria-hidden
          />
          <p className="text-row text-label">
            Your choices were sent
            {enrollment?.submitted_at
              ? ` on ${fmtDate(enrollment.submitted_at)}`
              : ""}{" "}
            and are being checked. You can still change them until{" "}
            {fmtDate(window.closes_at)}.
          </p>
        </div>
      )}
      {finalized && (
        <div className="flex items-start gap-2 rounded-control border border-hairline/75 bg-bar/70 p-3">
          <Lock className="mt-0.5 size-4 shrink-0 text-label" aria-hidden />
          <p className="text-row text-label">
            These choices are confirmed and your cover is updated. Contact your
            HR team if something needs to change.
          </p>
        </div>
      )}

      {/* Flex wallet balance */}
      {flex && (
        <FlexBalanceStrip
          flex={flex}
          allowOverdraft={window.allow_overdraft}
          memberLabels
          moneySymbol={money}
          shortfallHint="Your choices cost more than your allowance. Reduce them to submit, or ask your HR team."
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
              gloss={productGloss(ts.product_code)}
              memberLabels
              moneySymbol={money}
              onChange={(next) =>
                setState((s) => ({ ...s, [ts.product_code]: next }))
              }
            />
          );
        })}
        {!tierSets.length && (
          <Mount label="No plans to change">
            <p className="text-row text-label">
              This period doesn't include any plan you can change. If you were
              expecting a choice here, your HR team can tell you why.
            </p>
          </Mount>
        )}
      </div>

      {/* Leave trading */}
      {window.allow_leave && (
        <LeaveTradingCard
          action={leaveAction}
          days={leaveDays}
          leave={options?.leave ?? null}
          ratePerDay={options?.member_leave_rate ?? null}
          moneySymbol={money}
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
      {/* Two full-height actions, stacked on a phone. Saving keeps the work;
          sending is what starts the check — so the sentence stating the
          blocker sits with the button rather than in a `title` attribute,
          which a touch device never shows. */}
      {!disabled && (
        <div className="space-y-2">
          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={() => void saveElections()}
              disabled={saving}
              className={leafAction}
            >
              {saving && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Save my choices
            </button>
            <button
              type="button"
              disabled={submitting || submitBlocked}
              onClick={() => void doSubmit(false)}
              className={leafPrimaryAction}
            >
              <Send className="size-4" aria-hidden /> Send them in
            </button>
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
        title="Some choices have no flex price yet"
        // The member's symbol and the member's people: `$0` contradicted the
        // `S$` every other figure on this surface uses, and "your broker" is
        // broker vocabulary — a member's route to a question is their HR team,
        // which is who the rest of the portal names.
        description={`${
          unpricedProducts?.length
            ? `These plans change your coverage but don't have a flex price set yet, so they would draw ${money}0 from your wallet: ${unpricedProducts.join(", ")}. `
            : ""
        }You can submit anyway, or check with your HR team first.`}
        confirmLabel="Submit anyway"
        confirmVariant="default"
        loading={submitting}
        onConfirm={() => void doSubmit(true)}
      />
    </div>
  );
}
