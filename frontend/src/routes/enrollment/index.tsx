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
  useUpdateWindow,
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

/** The per-product price-tag source (slip vs manual matrix) configured for the
 * year. The source is a column on EnrollmentWindow, but it is price-tag setup,
 * so it is edited on the Price Tag tab (which writes it to every still-editable
 * window) and merely CARRIED by the create-window form. Both read it from here:
 * two readings of "what source is configured" would let a new window silently
 * revert what the Price Tag tab shows.
 *
 * It reads the newest window the tab can WRITE to (not `governing_flex_config`'s
 * latest non-draft), because reading a different window than we write to makes a
 * saved source snap straight back: on a year whose only window is still a draft,
 * the save lands on the draft while a governing-window read falls back to
 * "slip" — the toggle flips back, the matrix editor collapses and the Save
 * button disappears, all reporting success. Windows are ordered `opens_at DESC`
 * and a save writes the same map to every editable one, so [0] is both the
 * newest and (after any save through this tab) representative. Everything closed
 * → nothing is writable, so fall back to the governing window for display and
 * the toggle goes read-only. */
function configuredSourceMap(
  windows: EnrollmentWindow[] | undefined,
): Record<string, FlexPriceSource> {
  return sourceWindow(windows)?.flex_price_source ?? {};
}

function editableWindowsOf(windows: EnrollmentWindow[] | undefined) {
  // A closed window is a historical record — the server 409s a PATCH on one.
  return (windows ?? []).filter((w) => w.status !== "closed");
}

function sourceWindow(
  windows: EnrollmentWindow[] | undefined,
): EnrollmentWindow | undefined {
  return (
    editableWindowsOf(windows)[0] ??
    (windows ?? []).filter((w) => w.status !== "draft")[0]
  );
}

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
  // Needed only for the flex product list, which the new window's price-tag
  // source map is built over. The tags and their source are set on the Price Tag
  // tab — nothing here renders them.
  const { data: flexPricing } = useFlexPricing(policyYearId);
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
  // Whether benefits selections may draw more flex than the member's wallet
  // holds. Off (recommended), submit/confirm reject an overdrawn enrollment.
  const [allowOverdraft, setAllowOverdraft] = useState(false);
  // Flex funding config for this window: the company-wide drawdown rule. The
  // per-product price-tag SOURCE is also a window column, but it is price-tag
  // setup — it is chosen on the Price Tag tab and only carried here.
  const [drawdownRule, setDrawdownRule] = useState<FlexDrawdownRule>("full");
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
        Select a benefit year to manage enrolment periods.
      </p>
    );
  }

  function handleCreate() {
    if (!name.trim()) {
      toast.error("Give the enrolment period a name.");
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
    // Carry the year's configured price-tag source onto the new window (it is a
    // window column). Sending an explicit entry for EVERY product matters: the
    // resolver skips building the slip premium index when every named product is
    // priced from the matrix, so a partial map can leave an unnamed slip product
    // unpriced.
    const inherited = configuredSourceMap(windows);
    const sources = Object.fromEntries(
      flexProducts.map((p) => [p.product_id, inherited[p.product_id] ?? "slip"]),
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
          toast.success("Enrolment period created.");
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
          <h3 className="text-sm font-semibold text-foreground">New enrolment period</h3>
          <InfoHint>
            Define when members may change their benefits selection. Opening the
            period pre-fills each member with their current plan (reverse
            enrolment).
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
                className="text-2xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                Set the day limits &amp; per-day rate →
              </Link>
            )}
          </div>

          {/* Flex funding: how this window draws the wallet down (the price tags
              themselves are per policy year — see the Price Tag tab) */}
          <div className="rounded-md border border-border bg-muted/20 p-3 sm:col-span-2">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <div className="flex items-center gap-1">
                <div className="text-xs font-semibold text-foreground">Flex funding</div>
                <InfoHint>
                  How the flex wallet is drawn down for coverage in this
                  enrolment period. The wallet funds each member&apos;s coverage;
                  changing plans or trading leave adjusts what it is charged.
                </InfoHint>
              </div>
              {/* What each plan COSTS the wallet — the price tags and where they
                  come from — is per policy year and lives on its own tab. */}
              <Link
                to="/enrollment"
                search={{ tab: "flex" }}
                className="text-2xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                Set the price tags →
              </Link>
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
              <span className="basis-full text-2xs text-muted-foreground sm:basis-auto">
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
              <span className="basis-full text-2xs text-muted-foreground sm:basis-auto">
                {allowOverdraft
                  ? "Benefits selections may exceed the flex wallet — the shortfall is the member's to top up (e.g. via payroll)."
                  : "Submitting is blocked when benefits selections draw more flex than the member's wallet holds."}
              </span>
            </div>
          </div>
        </div>
        <div className="mt-3">
          <Button onClick={handleCreate} disabled={createWindow.isPending}>
            {createWindow.isPending && <Loader2 className="size-4 animate-spin" />}
            Create enrolment period
          </Button>
        </div>
      </div>

      {/* Window list */}
      <div className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-4 py-2.5 text-sm font-semibold text-foreground">
          Enrolment periods
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : !windows?.length ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            No enrolment periods yet. Create one above to start an enrolment.
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
                  <div className="text-2xs text-muted-foreground">
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
                        Benefits Selection
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
                              `Enrolment period opened — ${r.enrollments_created.toLocaleString()} enrolment(s) created.`,
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
                      title="Re-runs the open step to backfill enrolment rows for employees added after the period opened (idempotent)"
                      onClick={() =>
                        openWindow.mutate(w.id, {
                          onSuccess: (r) =>
                            r.enrollments_created > 0
                              ? toast.success(
                                  `Synced ${r.enrollments_created.toLocaleString()} new employee(s) into this enrolment period.`,
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
                      aria-label="Delete enrolment period"
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
        title="Close this enrolment period?"
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
                will be handled by the period's default behavior.
              </p>
            ) : (
              <p>
                Members who haven't made changes will keep their current plan.
              </p>
            )}
            <p>
              Every benefits selection is projected to effective coverage. This
              can't be undone.
            </p>
          </div>
        }
        confirmLabel="Close period"
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
        title="Delete this draft enrolment period?"
        description="The enrolment period will be removed. Only a draft can be deleted."
        loading={deleteWindow.isPending}
        onConfirm={() => {
          if (!confirmDelete) return;
          deleteWindow.mutate(confirmDelete.id, {
            onSuccess: () => {
              toast.success("Enrolment period deleted.");
              setConfirmDelete(null);
            },
          });
        }}
      />
    </div>
  );
}

// Tab KEYS ride the URL (deep links, the /elections legacy redirect, the
// company-settings redirect), so they stay put when a label is reworded.
const ENROLLMENT_TABS = [
  { key: "windows", label: "Enrolment Period" },
  { key: "elections", label: "Benefits Selection" },
  { key: "flex", label: "Price Tag" },
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
// Everything the flex WALLET pays for, in one place: where each product's price
// tag comes from (slip vs manual matrix), what each plan and dependant option
// costs, and the age-banded voluntary rates. It used to live inside the
// create-window form, which meant the year's prices were unreachable unless you
// were opening a window — and left the same product list rendered on two tabs.
//
// The SOURCE is a column on EnrollmentWindow, so saving it writes the (complete)
// map to every still-editable window — the OPEN one included, since a broker who
// mis-set the source has to be able to correct it without closing the period. A
// closed period keeps the source its enrolment actually ran under.
//
// Changing it mid-period does NOT retroactively reprice: each election snapshots
// its `flex_price_tag` when saved and `enrollment_flex_draft` sums those stored
// values, so what a member submitted under is what confirm checks. It changes
// what the benefit statement recomputes and what the NEXT save of a selection
// prices at.
function EnrollmentPriceTagTab() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: flexPricing, isLoading } = useFlexPricing(policyYearId);
  const flexEditor = useFlexPricingEditor(policyYearId);
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const updateWindow = useUpdateWindow();
  const [openEditor, setOpenEditor] = useState<Record<string, boolean>>({});
  // Unsaved source picks, over the saved map.
  const [sourceEdits, setSourceEdits] = useState<Record<string, FlexPriceSource>>({});

  const savedSources = configuredSourceMap(windows);
  const sourceFor = (pid: string): FlexPriceSource =>
    sourceEdits[pid] ?? savedSources[pid] ?? "slip";
  const editableWindows = editableWindowsOf(windows);
  const sourceDirty = Object.entries(sourceEdits).some(
    ([pid, v]) => v !== (savedSources[pid] ?? "slip"),
  );

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to configure flex pricing.
      </p>
    );
  }

  const products = flexPricing?.products ?? [];
  const canEditSource = editableWindows.length > 0;

  async function save() {
    // The matrix and the source are two independent writes behind one button, so
    // the matrix goes first and unconditionally: a failing source PATCH must not
    // silently discard the prices typed in the same sitting.
    if (flexEditor.dirty) flexEditor.onSave();
    if (!sourceDirty) return;
    // `windows` can refetch between render and click (someone else closes the
    // last period), which leaves the button dirty with nothing to write to —
    // Promise.all([]) would resolve and report a save that never happened.
    if (!editableWindows.length) {
      toast.error(
        "No open or draft enrolment period to write the price-tag source to.",
      );
      return;
    }
    // Write an explicit entry for EVERY product, not just the edited ones —
    // the resolver skips the slip premium index when every named product is
    // priced from the matrix, so a partial map can leave an unnamed slip
    // product unpriced.
    const map = Object.fromEntries(
      products.map((p) => [p.product_id, sourceFor(p.product_id)]),
    ) as Record<string, FlexPriceSource>;
    try {
      await Promise.all(
        editableWindows.map((w) =>
          updateWindow.mutateAsync({ id: w.id, body: { flex_price_source: map } }),
        ),
      );
      setSourceEdits({});
      if (!flexEditor.dirty) toast.success("Price-tag source saved");
    } catch (e) {
      // One period can commit while another fails, leaving them priced
      // differently. The edits are KEPT and the button stays dirty, and since a
      // retry rewrites the same complete map to all of them, pressing Save again
      // is what repairs the split — so say so rather than just echoing the error.
      toast.error(
        `Price-tag source may not have reached every enrolment period — press Save again. (${formatError(e)})`,
      );
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <h3 className="text-sm font-semibold text-foreground">Flex price tags</h3>
          <InfoHint>
            What each plan draws from the member&apos;s flex wallet — separate
            from the insurer premium. A blank matrix cell falls back to the
            placement-slip premium when the product is priced &ldquo;from
            slip&rdquo;. Buy/sell-leave is priced on the Leave tab.
          </InfoHint>
        </div>
        {(flexEditor.dirty || sourceDirty) && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void save()}
            disabled={flexEditor.saving || updateWindow.isPending}
          >
            {(flexEditor.saving || updateWindow.isPending) && (
              <Loader2 className="size-4 animate-spin" />
            )}
            Save price tags
          </Button>
        )}
      </div>
      {/* The source rides the enrollment window, so with no editable window
          there is nothing to write it to — say so rather than offering a
          control that saves nowhere. */}
      {!canEditSource && products.length > 0 && (
        <p className="mt-2 text-2xs text-muted-foreground">
          Each product is priced from the placement slip until an enrolment
          period carries a different source. Create one on the Enrolment Period
          tab to switch a product to its manual matrix.
        </p>
      )}
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
            onSourceChange={
              canEditSource
                ? (pid, v) => setSourceEdits((s) => ({ ...s, [pid]: v }))
                : undefined
            }
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
        <EnrollmentPriceTagTab />
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
