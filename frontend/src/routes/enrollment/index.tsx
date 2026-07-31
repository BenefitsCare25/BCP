import { useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EnrollmentElectionsPage } from "./elections";
import { EnrollmentBulkPage } from "./bulk";
import {
  CalendarClock,
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
  useCloseWindow,
  useCreateWindow,
  useDeleteWindow,
  useEnrollmentRoster,
  useEnrollmentWindows,
  useFlexPricing,
  useOpenWindow,
} from "@/api/enrollment";
import { formatError } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Segmented } from "@/components/ui/segmented";
import { useFlexPricingEditor } from "@/components/enrollment/FlexPricingCard";
import { FlexProductList } from "@/components/enrollment/FlexProductList";
import { LeavePolicyCard } from "@/components/enrollment/LeavePolicyCard";

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

export function EnrollmentDashboardPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: windows, isLoading } = useEnrollmentWindows(policyYearId);
  const { data: flexPricing } = useFlexPricing(policyYearId);
  // Read-only here: the window form previews each product's price tags but the
  // matrix itself is edited on the Flex tab (it is per policy year, not per
  // window). FlexProductList still needs an editor instance to type-check.
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
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 sm:col-span-2">
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Switch checked={allowLeave} onCheckedChange={setAllowLeave} />
              Leave trading
            </label>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Switch checked={allowDeps} onCheckedChange={setAllowDeps} />
              Dependants
            </label>
            {/* The switch only EXPOSES trading — the day caps and per-day rate
                that decide what members can actually do live on the Leave tab. */}
            {allowLeave && (
              <Link
                to="/enrollment"
                search={{ tab: "leave" }}
                className="text-[11px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                Set the day limits &amp; per-day rate →
              </Link>
            )}
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
                  {/* The SOURCE is a window column and is set here; the price
                      tags themselves are per policy year and live on the Flex
                      tab, so this stays a preview. */}
                  <Link
                    to="/enrollment"
                    search={{ tab: "flex" }}
                    className="normal-case underline underline-offset-2 hover:text-foreground"
                  >
                    Edit price tags →
                  </Link>
                </div>
                <div className="mt-1.5">
                  <FlexProductList
                    products={flexProducts}
                    pricing={flexPricing?.pricing}
                    editor={flexEditor}
                    sourceFor={(pid) => priceSource[pid] ?? "slip"}
                    onSourceChange={(pid, v) =>
                      setPriceSource((s) => ({ ...s, [pid]: v }))
                    }
                  />
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

const ENROLLMENT_TABS = [
  { key: "windows", label: "Windows" },
  { key: "elections", label: "Elections" },
  { key: "flex", label: "Flex" },
  { key: "leave", label: "Leave" },
  { key: "bulk", label: "Bulk plan update" },
] as const;

type EnrollmentTab = (typeof ENROLLMENT_TABS)[number]["key"];
const isEnrollmentTab = (v: string | undefined): v is EnrollmentTab =>
  ENROLLMENT_TABS.some((t) => t.key === v);

// The leave policy is standing per-year config, but it's part of the enrollment
// workflow (a window's "Leave trading" switch is what exposes it to members), so
// it lives here rather than on the company Settings page —
// /configuration/settings?tab=enrollment redirects to this tab.
// Everything the flex WALLET pays for, in one place: what each plan and
// dependant option costs, and the age-banded voluntary rates. All of it is per
// policy YEAR — it used to live inside the create-window form, which meant the
// year's prices were unreachable unless you were opening a window. The
// per-window price-tag SOURCE (slip vs matrix) stays on the window form,
// because it is a column on the window.
function EnrollmentFlexTab() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: flexPricing, isLoading } = useFlexPricing(policyYearId);
  const flexEditor = useFlexPricingEditor(policyYearId);
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const [openEditor, setOpenEditor] = useState<Record<string, boolean>>({});

  // The source each product is actually priced by = the governing window's
  // choice (latest non-draft, mirroring the backend's `governing_flex_config`),
  // so the preview here matches what members are charged. The list endpoint
  // already orders opens_at DESC, so the newest is [0] — taking the LAST element
  // read the OLDEST window and could show a slip tag while members were being
  // charged the matrix value.
  const governing = (windows ?? []).filter((w) => w.status !== "draft")[0];
  const sourceFor = (pid: string): FlexPriceSource =>
    governing?.flex_price_source?.[pid] ?? "slip";

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to configure flex pricing.
      </p>
    );
  }

  const products = flexPricing?.products ?? [];

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <h3 className="text-sm font-semibold text-foreground">Flex price tags</h3>
          <InfoHint>
            What each plan draws from the member&apos;s flex wallet — separate
            from the insurer premium. A blank matrix cell falls back to the
            placement-slip premium when the window prices that product
            &ldquo;from slip&rdquo;. Buy/sell-leave is priced on the Leave tab.
          </InfoHint>
        </div>
        {flexEditor.dirty && (
          <Button
            size="sm"
            variant="outline"
            onClick={flexEditor.onSave}
            disabled={flexEditor.saving}
          >
            {flexEditor.saving && <Loader2 className="size-4 animate-spin" />}
            Save price tags
          </Button>
        )}
      </div>
      <div className="mt-3">
        {isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : (
          <FlexProductList
            products={products}
            pricing={flexPricing?.pricing}
            editor={flexEditor}
            sourceFor={sourceFor}
            openEditor={openEditor}
            onToggleEditor={(pid) =>
              setOpenEditor((s) => ({
                ...s,
                [pid]: !(s[pid] ?? sourceFor(pid) === "manual"),
              }))
            }
            emptyHint={
              <p className="text-sm text-muted-foreground">
                No flex-priced products in this benefit year yet. Products appear
                here once the placement slip is parsed and the flex scheme is
                confirmed.
              </p>
            }
          />
        )}
      </div>
    </div>
  );
}

function EnrollmentLeaveTab() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to configure the leave policy.
      </p>
    );
  }
  return <LeavePolicyCard key={policyYearId} policyYearId={policyYearId} />;
}

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
  const tab: EnrollmentTab = isEnrollmentTab(search.tab) ? search.tab : "windows";

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
      <TabsContent value="flex">
        <EnrollmentFlexTab />
      </TabsContent>
      <TabsContent value="leave">
        <EnrollmentLeaveTab />
      </TabsContent>
      <TabsContent value="bulk">
        <EnrollmentBulkPage />
      </TabsContent>
    </Tabs>
  );
}
