import { useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EnrollmentElectionsPage } from "./elections";
import { EnrollmentBulkPage } from "./bulk";
import {
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Loader2,
  Lock,
  Play,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import {
  type EnrollmentWindow,
  type FlexDrawdownRule,
  type FlexPriceSource,
  type FlexPricingBag,
  type FlexPricingProduct,
  type LeaveRates,
  useCloseWindow,
  useCreateWindow,
  useDeleteWindow,
  useEnrollmentRoster,
  useEnrollmentWindows,
  useFlexPricing,
  useLeavePolicy,
  useOpenWindow,
  useUpsertLeavePolicy,
} from "@/api/enrollment";
import { formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";
import { type PlanRow, planRows, planScalar } from "@/lib/flexTiers";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Segmented } from "@/components/ui/segmented";
import {
  ProductFlexEditor,
  useFlexPricingEditor,
} from "@/components/enrollment/FlexPricingCard";
import { LifeVoluntaryPanel } from "@/components/enrollment/LifeVoluntaryPanel";
import { LeaveRatesEditor } from "@/components/enrollment/LeaveRatesEditor";

const STATUS_VARIANT: Record<string, "primary" | "good" | "outline"> = {
  draft: "outline",
  open: "primary",
  closed: "good",
};

function toLocalInput(): { opens: string; closes: string } {
  // Sensible default window: a 30-day span starting today (datetime-local format).
  const pad = (n: number) => String(n).padStart(2, "0");
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const now = new Date();
  const later = new Date(now.getTime() + 30 * 86_400_000);
  return { opens: fmt(now), closes: fmt(later) };
}

// The collapsed price-tag chips, one per plan. Cohort tiers that share a plan and
// price identically fold into a single chip (mirroring the editor); a plan whose
// cohorts genuinely differ stays split (each carrying its cohort label) so nothing
// is silently merged. Folds off the SAVED pricing so the chip can't disagree with
// the values it shows.
function planPreviewRows(
  product: FlexPricingProduct,
  bag: FlexPricingBag | undefined,
): PlanRow[] {
  const tags = bag?.products?.[product.product_id]?.price_tags;
  return planRows(product.tiers, (t) => [t.slip_premium, planScalar(tags, t.key)]);
}

// One plan's exact price tag: a broker-set matrix value is a sparse OVERRIDE that
// wins; otherwise the "slip" source falls back to the slip premium. A single
// number, or a range only when the row varies by age band. "—" when unpriced.
// Scans every key in the row so a folded plan reads its (consistent) value.
function planTag(
  bag: FlexPricingBag | undefined,
  product: FlexPricingProduct,
  row: PlanRow,
  source: FlexPriceSource,
): string {
  for (const key of row.keys) {
    const cell = bag?.products?.[product.product_id]?.price_tags?.[key];
    const vals = cell
      ? Object.values(cell).filter((v): v is number => typeof v === "number")
      : [];
    if (vals.length) {
      const min = Math.min(...vals);
      const max = Math.max(...vals);
      return min === max ? fmtCurrency(min) : `${fmtCurrency(min)}–${fmtCurrency(max)}`;
    }
  }
  if (source === "slip" && row.rep.slip_premium != null)
    return fmtCurrency(row.rep.slip_premium);
  return "—";
}

export function EnrollmentDashboardPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: windows, isLoading } = useEnrollmentWindows(policyYearId);
  const { data: flexPricing } = useFlexPricing(policyYearId);
  // Inline editor for the per-year price-tag matrix, surfaced under a product when
  // its window price-tag source is the manual matrix.
  const flexEditor = useFlexPricingEditor(policyYearId);
  const createWindow = useCreateWindow(policyYearId);
  const openWindow = useOpenWindow();
  const closeWindow = useCloseWindow();
  const deleteWindow = useDeleteWindow();

  const flexProducts = flexPricing?.products ?? [];
  const defaults = toLocalInput();
  const [name, setName] = useState("");
  const [opensAt, setOpensAt] = useState(defaults.opens);
  const [closesAt, setClosesAt] = useState(defaults.closes);
  const [allowLeave, setAllowLeave] = useState(false);
  const [allowDeps, setAllowDeps] = useState(true);
  // Whether elections may draw more flex than the member's wallet holds.
  // Off (recommended), submit/confirm reject an overdrawn enrollment.
  const [allowOverdraft, setAllowOverdraft] = useState(false);
  // Flex funding config for this window: company-wide drawdown rule + per-product
  // price-tag source ("slip" = from placement-slip premium, else portal matrix).
  const [drawdownRule, setDrawdownRule] = useState<FlexDrawdownRule>("full");
  const [priceSource, setPriceSource] = useState<Record<string, FlexPriceSource>>({});
  // Per-product reveal of the inline price-tag editor; defaults open for manual.
  const [openEditor, setOpenEditor] = useState<Record<string, boolean>>({});
  const [confirmClose, setConfirmClose] = useState<EnrollmentWindow | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<EnrollmentWindow | null>(null);

  // Live counts for the close-window dialog — how many members it will affect.
  const closeSubmitted = useEnrollmentRoster(confirmClose?.id, {
    status: "submitted",
    limit: 1,
  });
  const closeConfirmed = useEnrollmentRoster(confirmClose?.id, {
    status: "confirmed",
    limit: 1,
  });
  const closeTotal = useEnrollmentRoster(confirmClose?.id, { limit: 1 });

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a policy year to manage enrollment windows.
      </p>
    );
  }

  function handleCreate() {
    if (!name.trim()) {
      toast.error("Give the window a name.");
      return;
    }
    // A cleared datetime-local input yields "" → new Date("").toISOString()
    // throws a RangeError. Validate before building the payload.
    if (
      !opensAt ||
      !closesAt ||
      Number.isNaN(new Date(opensAt).getTime()) ||
      Number.isNaN(new Date(closesAt).getTime())
    ) {
      toast.error("Both open and close times are required.");
      return;
    }
    // Send an explicit source for every product (default: from slip). A complete
    // map means an unlisted product can't silently fall back to a different default.
    const sources = Object.fromEntries(
      flexProducts.map((p) => [p.product_id, priceSource[p.product_id] ?? "slip"]),
    );
    createWindow.mutate(
      {
        name: name.trim(),
        window_type: "open",
        opens_at: new Date(opensAt).toISOString(),
        closes_at: new Date(closesAt).toISOString(),
        default_behavior: "deemed_keep_current",
        allow_plan_change: true,
        allow_leave: allowLeave,
        allow_dependant_changes: allowDeps,
        allow_overdraft: allowOverdraft,
        flex_drawdown_rule: drawdownRule,
        flex_price_source: Object.keys(sources).length ? sources : null,
      },
      {
        onSuccess: () => {
          toast.success("Enrollment window created.");
          setName("");
        },
      },
    );
  }

  return (
    <div className="space-y-5">
      {/* Create window */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-1">
          <h3 className="text-sm font-semibold text-foreground">New enrollment window</h3>
          <InfoHint>
            Define when members may change elections. Opening the window pre-fills each
            member with their current plan (reverse enrollment).
          </InfoHint>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Label htmlFor="win-name">Name</Label>
            <Input
              id="win-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="2026 Open Enrollment"
            />
          </div>
          <div>
            <Label htmlFor="win-opens">Opens</Label>
            <Input
              id="win-opens"
              type="datetime-local"
              value={opensAt}
              onChange={(e) => setOpensAt(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="win-closes">Closes</Label>
            <Input
              id="win-closes"
              type="datetime-local"
              value={closesAt}
              onChange={(e) => setClosesAt(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-4 sm:col-span-2">
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Switch checked={allowLeave} onCheckedChange={setAllowLeave} />
              Leave trading
            </label>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Switch checked={allowDeps} onCheckedChange={setAllowDeps} />
              Dependants
            </label>
          </div>

          {/* Flex funding: company-wide drawdown rule + per-product price-tag source */}
          <div className="rounded-md border border-border bg-muted/20 p-3 sm:col-span-2">
            <div className="flex items-center gap-1">
              <div className="text-xs font-semibold text-foreground">Flex funding</div>
              <InfoHint>
                How the flex wallet is drawn down for coverage in this window. The
                wallet funds each member's coverage; changing plans or trading
                leave adjusts what it is charged.
              </InfoHint>
            </div>

            {/* Drawdown rule — segmented so the active choice is unambiguous */}
            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <span className="text-sm text-foreground">Drawdown rule</span>
              <Segmented
                value={drawdownRule}
                onChange={setDrawdownRule}
                options={[
                  { value: "full", label: "Full plan tag" },
                  { value: "on_change", label: "Only on plan change" },
                ]}
              />
              <span className="basis-full text-[11px] text-muted-foreground sm:basis-auto">
                {drawdownRule === "on_change"
                  ? "Only the upgrade/downgrade difference vs the default plan is deducted (a downgrade credits the wallet)."
                  : "The member's full plan price tag is deducted from the wallet."}
              </span>
            </div>

            {/* Overdraft policy — the server enforces this at submit/confirm */}
            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <label className="flex items-center gap-2 text-sm text-foreground">
                <Switch checked={allowOverdraft} onCheckedChange={setAllowOverdraft} />
                Allow overdraft
              </label>
              <span className="basis-full text-[11px] text-muted-foreground sm:basis-auto">
                {allowOverdraft
                  ? "Elections may exceed the flex wallet — the shortfall is the member's to top up (e.g. via payroll)."
                  : "Submitting is blocked when elections draw more flex than the member's wallet holds."}
              </span>
            </div>

            {flexProducts.length > 0 && (
              <div className="mt-3 border-t border-border pt-2.5">
                <div className="flex items-center justify-between gap-2 text-[11px] uppercase tracking-wider text-muted-foreground">
                  <span>Price-tag source per product</span>
                  {flexEditor.dirty ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-6 normal-case"
                      onClick={flexEditor.onSave}
                      disabled={flexEditor.saving}
                    >
                      Save price tags
                    </Button>
                  ) : (
                    <span className="normal-case">Price tag per plan · default highlighted</span>
                  )}
                </div>
                <div className="mt-1.5 divide-y divide-border">
                  {flexProducts.map((p) => {
                    const source: FlexPriceSource = priceSource[p.product_id] ?? "slip";
                    const tags = planPreviewRows(p, flexPricing?.pricing).map((row) => ({
                      row,
                      tag: planTag(flexPricing?.pricing, p, row, source),
                    }));
                    const anyPriced = tags.some((x) => x.tag !== "—");
                    const editorOpen = openEditor[p.product_id] ?? source === "manual";
                    // Products publishing a slip voluntary rate table price by
                    // age band — shape-driven (not line-gated), so any product
                    // with the table gets the age-banded panel + live preview
                    // instead of the matrix.
                    const isLifeVoluntary = (p.voluntary_rates?.length ?? 0) > 0;
                    return (
                      <div key={p.product_id} className="py-2">
                        <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                            {p.product_code}
                            <Badge variant="outline" className="text-[10px] capitalize">
                              {p.line}
                            </Badge>
                          </div>
                          {!editorOpen &&
                            (isLifeVoluntary ? (
                            <div className="mt-0.5 text-[11px] text-muted-foreground">
                              Age-banded voluntary rates ({p.voluntary_rates?.length ?? 0}{" "}
                              bands) — expand to preview premiums.
                            </div>
                          ) : anyPriced ? (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {tags.map(({ row, tag }) => (
                                <span
                                  key={row.rep.key}
                                  title={row.rep.is_baseline ? "Default plan" : undefined}
                                  className={cn(
                                    "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]",
                                    row.rep.is_baseline
                                      ? "border-transparent bg-sidebar-active text-sidebar-active-foreground"
                                      : "border-border bg-card",
                                  )}
                                >
                                  <span
                                    className={cn(
                                      !row.rep.is_baseline && "text-muted-foreground",
                                    )}
                                  >
                                    {row.rep.label}
                                    {row.cohortLabel && (
                                      <span className="ml-1 opacity-70">
                                        · {row.cohortLabel}
                                      </span>
                                    )}
                                  </span>
                                  <span
                                    className={cn(
                                      "font-medium",
                                      !row.rep.is_baseline && "text-foreground",
                                    )}
                                  >
                                    {tag}
                                  </span>
                                </span>
                              ))}
                            </div>
                          ) : (
                            <div className="mt-0.5 text-[11px] text-warn">
                              {source === "slip"
                                ? "No slip premiums for this product."
                                : "No matrix prices yet — set them below."}
                            </div>
                            ))}
                        </div>
                        <div className="flex items-center gap-1.5">
                          {/* Life-voluntary products price off the rate table, not the
                              slip-vs-matrix source, so the toggle would be a no-op. */}
                          {!isLifeVoluntary && (
                            <Segmented
                              value={source}
                              onChange={(v) =>
                                setPriceSource((s) => ({ ...s, [p.product_id]: v }))
                              }
                              options={[
                                { value: "slip", label: "From slip" },
                                { value: "manual", label: "Manual matrix" },
                              ]}
                            />
                          )}
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-7"
                            onClick={() =>
                              setOpenEditor((s) => ({ ...s, [p.product_id]: !editorOpen }))
                            }
                            aria-label={editorOpen ? "Hide price tags" : "Edit price tags"}
                          >
                            {editorOpen ? (
                              <ChevronDown className="size-4" />
                            ) : (
                              <ChevronRight className="size-4" />
                            )}
                          </Button>
                        </div>
                        </div>
                        {editorOpen && (
                          <div className="mt-2">
                            {isLifeVoluntary ? (
                              <LifeVoluntaryPanel product={p} editor={flexEditor} />
                            ) : (
                              <ProductFlexEditor
                                product={p}
                                editor={flexEditor}
                                source={source}
                              />
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="mt-3">
          <Button onClick={handleCreate} disabled={createWindow.isPending}>
            {createWindow.isPending && <Loader2 className="size-4 animate-spin" />}
            Create window
          </Button>
        </div>
      </div>

      {/* Window list */}
      <div className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-4 py-2.5 text-sm font-semibold text-foreground">
          Enrollment windows
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : !windows?.length ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            No windows yet. Create one above to start an enrollment.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {windows.map((w) => (
              <li key={w.id} className="flex items-center gap-3 px-4 py-3">
                <CalendarClock className="size-4 text-muted-foreground shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">
                      {w.name}
                    </span>
                    <Badge variant={STATUS_VARIANT[w.status] ?? "outline"}>{w.status}</Badge>
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {new Date(w.opens_at).toLocaleDateString()} —{" "}
                    {new Date(w.closes_at).toLocaleDateString()}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {w.status === "open" && (
                    <Button asChild variant="outline" size="sm">
                      <Link
                        to="/enrollment"
                        search={{ tab: "elections", window: w.id }}
                      >
                        Elections
                      </Link>
                    </Button>
                  )}
                  {w.status === "draft" && (
                    <Button
                      size="sm"
                      onClick={() =>
                        openWindow.mutate(w.id, {
                          onSuccess: (r) =>
                            toast.success(
                              `Window opened — ${r.enrollments_created.toLocaleString()} enrollment(s) created.`,
                            ),
                        })
                      }
                      disabled={openWindow.isPending}
                    >
                      <Play className="size-3.5" /> Open
                    </Button>
                  )}
                  {w.status === "open" && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={openWindow.isPending}
                      title="Re-runs the open step to backfill enrollment rows for employees added after the window opened (idempotent)"
                      onClick={() =>
                        openWindow.mutate(w.id, {
                          onSuccess: (r) =>
                            r.enrollments_created > 0
                              ? toast.success(
                                  `Synced ${r.enrollments_created.toLocaleString()} new employee(s) into this window.`,
                                )
                              : toast.info(
                                  "No new employees to sync — everyone already has an enrollment.",
                                ),
                        })
                      }
                    >
                      <RefreshCw className="size-3.5" /> Sync new employees
                    </Button>
                  )}
                  {w.status === "open" && (
                    <Button variant="outline" size="sm" onClick={() => setConfirmClose(w)}>
                      <Lock className="size-3.5" /> Close
                    </Button>
                  )}
                  {w.status === "draft" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setConfirmDelete(w)}
                      aria-label="Delete window"
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Config below the windows. Keyed by policy year so the card's local edit
          state can't leak across a year switch (the component remounts fresh). The
          flex price-tag matrix now lives inline under each manual-source product. */}
      <LeavePolicyCard key={policyYearId} policyYearId={policyYearId} />

      <AlertDialog
        open={!!confirmClose}
        onOpenChange={(o) => !o && setConfirmClose(null)}
        title="Close this enrollment window?"
        description={
          <div className="space-y-2">
            {closeSubmitted.data && closeTotal.data ? (
              <p>
                <strong>{closeSubmitted.data.total}</strong> submitted enrollment
                {closeSubmitted.data.total === 1 ? "" : "s"} will be
                auto-confirmed;{" "}
                <strong>
                  {Math.max(
                    0,
                    closeTotal.data.total -
                      closeSubmitted.data.total -
                      (closeConfirmed.data?.total ?? 0),
                  )}
                </strong>{" "}
                untouched member
                {Math.max(
                  0,
                  closeTotal.data.total -
                    closeSubmitted.data.total -
                    (closeConfirmed.data?.total ?? 0),
                ) === 1
                  ? ""
                  : "s"}{" "}
                will be handled by the window's default behavior.
              </p>
            ) : (
              <p>
                Members who haven't made changes will keep their current plan.
              </p>
            )}
            <p>
              All elections are projected to effective coverage. This can't be
              undone.
            </p>
          </div>
        }
        confirmLabel="Close window"
        confirmVariant="default"
        loading={closeWindow.isPending}
        onConfirm={() => {
          if (!confirmClose) return;
          closeWindow.mutate(confirmClose.id, {
            onSuccess: (s) => {
              toast.success(
                `Closed — ${s.confirmed} confirmed, ${s.deemed_kept} kept.`,
              );
              setConfirmClose(null);
            },
            onError: (e) => toast.error(formatError(e)),
          });
        }}
      />
      <AlertDialog
        open={!!confirmDelete}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
        title="Delete this draft window?"
        description="The window will be removed. Only draft windows can be deleted."
        loading={deleteWindow.isPending}
        onConfirm={() => {
          if (!confirmDelete) return;
          deleteWindow.mutate(confirmDelete.id, {
            onSuccess: () => {
              toast.success("Window deleted.");
              setConfirmDelete(null);
            },
          });
        }}
      />
    </div>
  );
}

function LeavePolicyCard({ policyYearId }: { policyYearId: string }) {
  const { data: policy } = useLeavePolicy(policyYearId);
  const upsert = useUpsertLeavePolicy(policyYearId);
  const [maxBuy, setMaxBuy] = useState<string>("");
  const [maxSell, setMaxSell] = useState<string>("");
  const [increment, setIncrement] = useState<string>("");
  const [leaveRates, setLeaveRates] = useState<LeaveRates | null>(null);

  const buy = maxBuy !== "" ? Number(maxBuy) : (policy?.max_buy_days ?? 0);
  const sell = maxSell !== "" ? Number(maxSell) : (policy?.max_sell_days ?? 0);
  const inc = increment !== "" ? Number(increment) : (policy?.increment_days ?? 1);
  const initialRates: LeaveRates = {
    attribute:
      policy && "attribute" in (policy.leave_rates ?? {})
        ? (policy.leave_rates as LeaveRates).attribute
        : null,
    rates:
      policy && "rates" in (policy.leave_rates ?? {})
        ? (policy.leave_rates as LeaveRates).rates
        : {},
  };
  const ratesToSave = leaveRates ?? initialRates;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-1">
        <h3 className="text-sm font-semibold text-foreground">Leave policy</h3>
        <InfoHint>
          Buy/sell-leave bounds for this policy year. Members can buy extra days
          or sell days back — day counts only, no pricing is applied here.
        </InfoHint>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor="lp-buy">Max buy (days)</Label>
          <Input
            id="lp-buy"
            type="number"
            min={0}
            value={maxBuy !== "" ? maxBuy : (policy?.max_buy_days ?? 0)}
            onChange={(e) => setMaxBuy(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="lp-sell">Max sell (days)</Label>
          <Input
            id="lp-sell"
            type="number"
            min={0}
            value={maxSell !== "" ? maxSell : (policy?.max_sell_days ?? 0)}
            onChange={(e) => setMaxSell(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="lp-inc">Increment (days)</Label>
          <Input
            id="lp-inc"
            type="number"
            min={0.5}
            step={0.5}
            value={increment !== "" ? increment : (policy?.increment_days ?? 1)}
            onChange={(e) => setIncrement(e.target.value)}
          />
        </div>
      </div>

      <div className="mt-3">
        <Label>Leave price tag</Label>
        {/* Mount only once the policy has resolved (data !== undefined) and key it
            to the loaded policy, so the editor seeds from the saved rates instead
            of capturing empties on the pre-load render and wiping them on save. */}
        {policy !== undefined && (
          <LeaveRatesEditor
            key={policy?.id ?? "new"}
            policyYearId={policyYearId}
            value={initialRates}
            onChange={setLeaveRates}
          />
        )}
      </div>

      <div className="mt-3 flex justify-end">
        <Button
          variant="outline"
          disabled={upsert.isPending}
          onClick={() =>
            upsert.mutate(
              {
                allow_buy: buy > 0,
                allow_sell: sell > 0,
                min_buy_days: 0,
                max_buy_days: buy,
                min_sell_days: 0,
                max_sell_days: sell,
                increment_days: inc || 1,
                leave_rates: ratesToSave,
                notes: null,
              },
              { onSuccess: () => toast.success("Leave policy saved.") },
            )
          }
        >
          {upsert.isPending && <Loader2 className="size-4 animate-spin" />}
          Save leave policy
        </Button>
      </div>
    </div>
  );
}

const ENROLLMENT_TABS = [
  { key: "windows", label: "Windows & leave" },
  { key: "elections", label: "Elections" },
  { key: "bulk", label: "Bulk plan update" },
] as const;

type EnrollmentTab = (typeof ENROLLMENT_TABS)[number]["key"];

// The enrollment surface is one workflow (open a window → manage member
// elections → bulk adjust), so it renders as tabs of a single page. The
// active tab + selected window ride the URL so the windows-list "Elections"
// button and external deep links land on the right tab.
export function EnrollmentPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as {
    tab?: string;
    window?: string;
  };
  const tab: EnrollmentTab =
    search.tab === "elections" || search.tab === "bulk"
      ? search.tab
      : "windows";

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/enrollment", search: { tab: value } })
      }
    >
      <TabsList>
        {ENROLLMENT_TABS.map((t) => (
          <TabsTrigger key={t.key} value={t.key}>
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value="windows">
        <EnrollmentDashboardPage />
      </TabsContent>
      <TabsContent value="elections">
        <EnrollmentElectionsPage />
      </TabsContent>
      <TabsContent value="bulk">
        <EnrollmentBulkPage />
      </TabsContent>
    </Tabs>
  );
}
