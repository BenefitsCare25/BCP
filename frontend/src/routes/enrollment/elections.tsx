import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CheckCircle2, LockOpen, Loader2, RotateCcw, Send, Users } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import { useBenefitStatement } from "@/api/hooks";
import {
  type ProductTierSet,
  useConfirmEnrollment,
  useEnrollment,
  useEnrollmentOptions,
  useEnrollmentRoster,
  useEnrollmentWindows,
  useReopenEnrollment,
  useResetEnrollment,
  useSetElections,
  useSetLeave,
  useSubmitEnrollment,
} from "@/api/enrollment";
import { CoverageHistory } from "@/components/enrollment/CoverageHistory";
import { CoverageRevertControls } from "@/components/enrollment/CoverageRevertControls";
import { EmployeePicker } from "@/components/operations/EmployeePicker";
import {
  ElectionProductCard,
  FlexBalanceStrip,
  LeaveTradingCard,
  type ProductState,
  buildElectionsPayload,
  computeFlex,
  seedElectionState,
} from "@/components/enrollment/electionShared";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function EnrollmentElectionsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const search = useSearch({ strict: false }) as { window?: string };
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const openWindows = useMemo(
    () => (windows ?? []).filter((w) => w.status === "open"),
    [windows],
  );
  const navigate = useNavigate();
  const [windowId, setWindowId] = useState<string | undefined>(search.window);
  useEffect(() => {
    if (windows === undefined) return; // still loading — don't discard the param yet
    // A stale deep link (?window= pointing at a closed/deleted window) would
    // otherwise leave the selector blank — fall back to the latest open window.
    const isValid = !!windowId && openWindows.some((w) => w.id === windowId);
    if (isValid) return;
    const fallback = openWindows[0]?.id;
    if (fallback) {
      setWindowId(fallback);
      if (search.window && search.window !== fallback) {
        void navigate({
          to: "/enrollment",
          search: { tab: "elections", window: fallback },
          replace: true,
        });
      }
    }
  }, [windows, openWindows, windowId, search.window, navigate]);

  const window = windows?.find((w) => w.id === windowId);
  const [q, setQ] = useState("");
  const { data: roster, isLoading } = useEnrollmentRoster(windowId, { q });
  const [selected, setSelected] = useState<string | null>(null);

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a policy year to manage enrollments.
      </p>
    );
  }
  if (!openWindows.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No open enrollment window. Open one from the Windows &amp; leave tab first.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Window</span>
          <Select value={windowId} onValueChange={(v) => { setWindowId(v); setSelected(null); }}>
            <SelectTrigger className="w-[240px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {openWindows.map((w) => (
                <SelectItem key={w.id} value={w.id}>
                  {w.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="relative flex-1 min-w-[200px]">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name or staff ID"
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <EmployeePicker
          items={(roster?.items ?? []).map((it) => ({
            id: it.id,
            name: it.employee_name ?? it.staff_id,
            subtitle: it.staff_id,
            trailing: <StatusDot status={it.status} />,
          }))}
          selectedId={selected}
          onSelect={setSelected}
          isLoading={isLoading}
          emptyText="No members."
          header={
            <div className="px-1 pb-2 text-[11px] text-muted-foreground">
              {roster?.total ?? 0} members
            </div>
          }
        />

        <div>
          {!selected ? (
            <div className="rounded-lg border border-dashed border-border p-10 text-center">
              <Users className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-sm text-muted-foreground">
                Select a member to manage their elections.
              </p>
            </div>
          ) : (
            <ElectionPanel
              key={selected}
              enrollmentId={selected}
              allowLeave={window?.allow_leave ?? false}
              allowDeps={window?.allow_dependant_changes ?? false}
              allowOverdraft={window?.allow_overdraft ?? false}
              productScope={window?.product_scope ?? null}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const map: Record<string, string> = {
    not_started: "text-muted-foreground/60",
    in_progress: "text-warn",
    submitted: "text-primary",
    confirmed: "text-good",
    deemed: "text-muted-foreground",
    declined: "text-muted-foreground",
  };
  return (
    <span className={cn("text-[10px] capitalize", map[status] ?? "text-muted-foreground")}>
      {status.replace("_", " ")}
    </span>
  );
}

function ElectionPanel({
  enrollmentId,
  allowLeave,
  allowDeps,
  allowOverdraft,
  productScope,
}: {
  enrollmentId: string;
  allowLeave: boolean;
  allowDeps: boolean;
  allowOverdraft: boolean;
  productScope: string[] | null;
}) {
  const { data: enr, isLoading } = useEnrollment(enrollmentId);
  const { data: options } = useEnrollmentOptions(enrollmentId);
  const setElections = useSetElections();
  const setLeave = useSetLeave();
  const submit = useSubmitEnrollment();
  const confirm = useConfirmEnrollment();
  const reset = useResetEnrollment();
  const reopen = useReopenEnrollment();
  const [confirmReset, setConfirmReset] = useState(false);
  // Products the server flagged as changed-but-unpriced at submit — the broker
  // can fix pricing or deliberately submit anyway.
  const [unpricedProducts, setUnpricedProducts] = useState<string[] | null>(null);

  // Dependants come from the read-only statement (reuses the existing endpoint).
  const empId = enr?.employee_id ?? null;
  const { data: statement } = useBenefitStatement(allowDeps ? empId : null);
  const dependants = statement?.dependants ?? [];

  const productScopeSet = useMemo(
    () => (productScope?.length ? new Set(productScope) : null),
    [productScope],
  );

  // Electable tier sets for this member, scoped to the window's product scope.
  // Each set lists only the member's own cohort tiers — not every product plan.
  const tierSets = useMemo<ProductTierSet[]>(() => {
    const all = options?.products ?? [];
    return productScopeSet
      ? all.filter((p) => productScopeSet.has(p.product_code))
      : all;
  }, [options, productScopeSet]);

  const [state, setState] = useState<Record<string, ProductState>>({});
  const [leaveAction, setLeaveAction] = useState<string>("none");
  const [leaveDays, setLeaveDays] = useState<string>("0");

  useEffect(() => {
    if (!enr || !options) return;
    setState(seedElectionState(enr, tierSets));
    setLeaveAction(enr.leave?.action ?? "none");
    setLeaveDays(String(enr.leave?.days ?? 0));
  }, [enr, options, tierSets]);

  if (isLoading || !enr) {
    return (
      <div className="flex items-center gap-2 px-2 py-10 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading…
      </div>
    );
  }

  const finalized = enr.status === "confirmed" || enr.status === "deemed";

  // Running flex balance: wallet − Σ coverage price tags + live buy/sell-leave
  // impact (buy spends, sell credits) at the member's per-day leave rate.
  const flex = computeFlex(
    options, tierSets, state, dependants, allowDeps, leaveAction, leaveDays,
  );

  // The live balance mirrors the server-side wallet guard: an overdrawn
  // enrollment can't be submitted unless the window allows overdrafts (the
  // server re-checks regardless — this only saves a doomed round trip).
  const submitBlocked = !!flex && flex.balance < -0.005 && !allowOverdraft;

  function doSubmit(acknowledgeUnpriced: boolean) {
    submit.mutate(
      { id: enrollmentId, acknowledgeUnpriced },
      {
        onSuccess: () => {
          setUnpricedProducts(null);
          toast.success("Submitted.");
        },
        onError: (e) => {
          if (e instanceof ConflictDetailError) {
            if (e.detail.code === "unpriced_elections") {
              setUnpricedProducts(
                Array.isArray(e.detail.products)
                  ? (e.detail.products as string[])
                  : [],
              );
              return;
            }
            if (e.detail.code === "flex_overdrawn") {
              const balance = e.detail.balance;
              toast.error(
                `Elections overdraw the flex wallet${
                  typeof balance === "number"
                    ? ` by ${fmtCurrency(Math.abs(balance))}`
                    : ""
                }. Reduce the elections, or enable overdraft on the window.`,
              );
              return;
            }
          }
          toast.error(formatError(e));
        },
      },
    );
  }

  function saveElections() {
    const elections = buildElectionsPayload(state, tierSets, dependants, allowDeps);
    setElections.mutate(
      { id: enrollmentId, elections },
      { onSuccess: () => toast.success("Elections saved."), onError: (e) => toast.error(formatError(e)) },
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-foreground">
            {enr.employee_name ?? enr.staff_id}
          </h3>
          <p className="font-mono text-xs text-muted-foreground">{enr.staff_id}</p>
        </div>
        <Badge variant={finalized ? "good" : "outline"}>{enr.status.replace("_", " ")}</Badge>
      </div>

      {/* Flex wallet balance — wallet minus the price tags of selected coverage */}
      {flex && (
        <FlexBalanceStrip
          flex={flex}
          allowOverdraft={allowOverdraft}
          shortfallHint="Elections exceed the flex wallet. Reduce the elections to submit, or ask an admin to allow overdrafts on this window."
        />
      )}

      {/* Per-product elections — only the member's own cohort tiers */}
      <div className="space-y-2">
        {tierSets.map((ts) => {
          const ps = state[ts.product_code];
          if (!ps) return null;
          return (
            <ElectionProductCard
              key={ts.product_code}
              ts={ts}
              ps={ps}
              disabled={finalized}
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
            This member has no products in their cohort to elect.
          </p>
        )}
      </div>

      {/* Leave trading */}
      {allowLeave && (
        <LeaveTradingCard
          action={leaveAction}
          days={leaveDays}
          disabled={finalized}
          saving={setLeave.isPending}
          onActionChange={setLeaveAction}
          onDaysChange={setLeaveDays}
          onSave={() =>
            setLeave.mutate(
              { id: enrollmentId, action: leaveAction, days: Number(leaveDays) },
              {
                onSuccess: () => toast.success("Leave saved."),
                onError: (e) => toast.error(formatError(e)),
              },
            )
          }
        />
      )}

      {/* Actions */}
      {!finalized && (
        <div className="flex items-center gap-2">
          <Button onClick={saveElections} disabled={setElections.isPending}>
            {setElections.isPending && <Loader2 className="size-4 animate-spin" />}
            Save elections
          </Button>
          <Button
            variant="outline"
            disabled={submit.isPending || submitBlocked}
            title={
              submitBlocked
                ? "Elections exceed the flex wallet — reduce them or enable overdraft on the window"
                : undefined
            }
            onClick={() => doSubmit(false)}
          >
            <Send className="size-4" /> Submit
          </Button>
          <Button
            variant="outline"
            disabled={confirm.isPending}
            onClick={() =>
              confirm.mutate(enrollmentId, {
                onSuccess: () => toast.success("Confirmed — coverage updated."),
                onError: (e) => toast.error(formatError(e)),
              })
            }
          >
            <CheckCircle2 className="size-4" /> Confirm
          </Button>
          <Button
            variant="ghost"
            disabled={reset.isPending}
            onClick={() => setConfirmReset(true)}
            title="Discard in-progress elections and return to the window baseline"
          >
            <RotateCcw className="size-4" /> Discard changes
          </Button>
        </div>
      )}

      {/* Confirmed enrollment: reopen for further changes while the window is
          still open (re-enables edit → submit → confirm). */}
      {enr.status === "confirmed" && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">
            This enrollment is confirmed. Reopen it to change plans again while the
            window is open — coverage stays as-is until you re-submit and confirm.
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={reopen.isPending}
            onClick={() =>
              reopen.mutate(enrollmentId, {
                onSuccess: () => toast.success("Reopened — you can edit elections again."),
                onError: (e) => toast.error(formatError(e)),
              })
            }
          >
            {reopen.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <LockOpen className="size-4" />
            )}
            Reopen for changes
          </Button>
        </div>
      )}

      {/* Coverage history + (when finalized) revert to the window baseline */}
      <div className="border-t border-border pt-3 space-y-3">
        {finalized && empId && (
          <CoverageRevertControls
            employeeId={empId}
            hasBaseline={!!enr.baseline_snapshot?.products}
            windowId={enr.window_id}
          />
        )}
        <CoverageHistory employeeId={empId} limit={4} />
      </div>

      <AlertDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Discard in-progress elections?"
        description="This clears this member's unsaved plan and leave elections for the window, returning them to their baseline (pre-enrollment) coverage. Already-confirmed coverage is not affected."
        confirmLabel="Discard"
        confirmVariant="default"
        loading={reset.isPending}
        onConfirm={() =>
          reset.mutate(enrollmentId, {
            onSuccess: () => {
              toast.success("Elections reset to baseline.");
              setConfirmReset(false);
            },
            onError: (e) => toast.error(formatError(e)),
          })
        }
      />

      <AlertDialog
        open={unpricedProducts !== null}
        onOpenChange={(open) => {
          if (!open) setUnpricedProducts(null);
        }}
        title="Some elections have no flex price"
        description={`${
          unpricedProducts?.length
            ? `These products change coverage but have no configured flex price, so they would draw $0 from the wallet: ${unpricedProducts.join(", ")}. `
            : ""
        }This is usually a pricing gap — a missing slip premium or matrix row, or an age-banded product without the member's date of birth. Configure pricing first, or submit anyway if the $0 draw is intended.`}
        confirmLabel="Submit anyway"
        confirmVariant="default"
        loading={submit.isPending}
        onConfirm={() => doSubmit(true)}
      />
    </div>
  );
}
