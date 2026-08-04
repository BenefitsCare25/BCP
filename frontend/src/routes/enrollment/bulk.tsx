/**
 * Coverage changes — move, decline or adjust one product's coverage for many
 * members at once.
 *
 * Three panels, one page: WHO (a rule over the roster), WHAT changes, and CHECK
 * & APPLY (summary first, rows as the drill-down). The page it replaces asked
 * the broker to paste every staff ID, offered only "set plan" of the three
 * actions the API has always supported, and rendered every affected member as a
 * row whether there were four of them or two thousand.
 *
 * Two rules the flow depends on:
 *
 * - **Apply sends the RULE, not the ticked ids.** Unticking a row adds an
 *   exclusion to the same query, so what is applied is provably the population
 *   that was previewed — and the request stays the same size at 4 members or
 *   4,000.
 * - **A preview belongs to the inputs that produced it.** Any change to the
 *   selection or the change itself clears it, because an Apply button enabled
 *   against a stale preview applies numbers nobody checked.
 */
import { useMemo, useState } from "react";
import { Loader2, Play, Search, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import { useMe, usePlans, useProducts } from "@/api/hooks";
import { useMemberFacets } from "@/api/memberQuery";
import {
  type BulkRequest,
  type BulkResult,
  useApplyBulk,
  usePreviewBulk,
} from "@/api/enrollment";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import { InfoHint } from "@/components/ui/tooltip";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { PaginationControls } from "@/components/ui/pagination-controls";
import {
  EMPTY_SELECTOR,
  MemberSelector,
  type SelectorState,
  selectorIsEmpty,
  toQuery,
} from "@/components/enrollment/bulk/MemberSelector";

const OUTCOME_COLOR: Record<string, string> = {
  applied: "text-good",
  would_apply: "text-info",
  no_change: "text-muted-foreground",
  skipped: "text-warn",
  error: "text-error",
};

const OUTCOME_LABEL: Record<string, string> = {
  applied: "changed",
  would_apply: "will change",
  no_change: "already set",
  skipped: "skipped",
  error: "error",
};

type BulkAction = "set_plan" | "decline";
type DependantMode = "" | "include_all" | "exclude_all";

const ROWS_PER_PAGE = 100;

export function EnrollmentBulkPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: me } = useMe();
  const { data: plans } = usePlans(policyYearId);
  const { data: products } = useProducts();
  const { data: facets, isLoading: facetsLoading } = useMemberFacets(policyYearId);
  const preview = usePreviewBulk(policyYearId);
  const apply = useApplyBulk(policyYearId);

  const [productCode, setProductCode] = useState("");
  const [action, setAction] = useState<BulkAction>("set_plan");
  const [targetPlan, setTargetPlan] = useState("");
  const [dependantMode, setDependantMode] = useState<DependantMode>("");
  const [selector, setSelector] = useState<SelectorState>(EMPTY_SELECTOR);
  const [result, setResult] = useState<BulkResult | null>(null);
  const [page, setPage] = useState(0);
  const [confirmApply, setConfirmApply] = useState(false);
  // Rows unticked SINCE the preview ran. The server's counts include them (the
  // preview resolved before they were excluded), so the button has to subtract
  // them or it would offer to apply more changes than it will make. Reset by
  // every preview/apply, after which the excluded members aren't in the result
  // at all.
  const [untickedChanging, setUntickedChanging] = useState(0);

  // Plans configured in THIS benefit year, per product, with their DISTINCT
  // codes — a plan_code can repeat within a product (e.g. GPA "(Option N)"), so
  // dedupe to avoid duplicate React keys / indistinguishable dropdown items.
  const plansByCode = useMemo(() => {
    const idToCode = new Map((products ?? []).map((p) => [p.id, p.code]));
    const sets: Record<string, Set<string>> = {};
    for (const pl of plans?.items ?? []) {
      const code = idToCode.get(pl.product_id);
      if (!code) continue;
      (sets[code] ??= new Set()).add(pl.code);
    }
    return Object.fromEntries(
      Object.entries(sets).map(([code, s]) => [code, [...s]]),
    ) as Record<string, string[]>;
  }, [plans, products]);

  /**
   * The product list is the UNION of two real sources, never a static list:
   * products members are actually matched to (the facets, which is what the
   * server can change coverage for) and products with plans configured this
   * year (configured but not yet matched to anyone).
   *
   * Listing only plan-carrying products — the old behaviour — silently hid
   * every product that has categories but no Plan rows (WICA, GBT), so a
   * broker could not bulk-DECLINE them even though the server accepts it:
   * `resolve_product_by_code` resolves through plans OR categories.
   */
  const productOptions = useMemo(() => {
    const byCode = new Map<
      string,
      { code: string; name: string | null; plans: string[]; hasDependants: boolean }
    >();
    const meta = new Map((products ?? []).map((p) => [p.code, p]));
    for (const p of facets?.products ?? []) {
      byCode.set(p.code, {
        code: p.code,
        name: p.name,
        plans: plansByCode[p.code] ?? [],
        hasDependants: meta.get(p.code)?.has_dependants ?? false,
      });
    }
    for (const [code, planCodes] of Object.entries(plansByCode)) {
      if (byCode.has(code)) continue;
      byCode.set(code, {
        code,
        name: meta.get(code)?.display_name ?? null,
        plans: planCodes,
        hasDependants: meta.get(code)?.has_dependants ?? false,
      });
    }
    return [...byCode.values()].sort((a, b) => a.code.localeCompare(b.code));
  }, [facets, plansByCode, products]);

  const selectedProduct = productOptions.find((p) => p.code === productCode);
  const planOptions = selectedProduct?.plans ?? [];
  const productFacet = facets?.products.find((p) => p.code === productCode);
  const planHeadcount = useMemo(
    () => new Map((productFacet?.plans ?? []).map((p) => [p.code, p.count])),
    [productFacet],
  );

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to change coverage in bulk.
      </p>
    );
  }

  // Everything on this page is a POST — the live headcount and the dry-run as
  // much as the apply — and `require_write_access` 403s every POST for a
  // `broker_viewer`. Rendering the form anyway would fire a failing request per
  // keystroke into the notification centre and offer buttons that can only
  // fail. Same reasoning as the claim message thread.
  if (me?.role === "broker_viewer") {
    return (
      <p className="text-sm text-muted-foreground">
        Changing coverage in bulk needs write access — your role is read-only.
        Coverage &amp; Members shows each member&apos;s current plan.
      </p>
    );
  }

  /** Any input change invalidates the preview it produced. */
  function resetResult() {
    setResult(null);
    setPage(0);
    setUntickedChanging(0);
  }

  function updateSelector(next: SelectorState) {
    setSelector(next);
    resetResult();
  }

  /**
   * Untick / re-tick one previewed row.
   *
   * Deliberately does NOT clear the preview: an exclusion narrows a population
   * the broker has already checked, and the digest is taken before exclusions
   * are subtracted, so the guard still holds. Clearing here would make removing
   * three people from a 400-member preview cost a full re-run each time.
   */
  function toggleExclude(employeeId: string, include: boolean, changing: boolean) {
    setSelector((prev) => ({
      ...prev,
      excludedIds: include
        ? prev.excludedIds.filter((x) => x !== employeeId)
        : [...prev.excludedIds, employeeId],
    }));
    if (changing) setUntickedChanging((n) => Math.max(0, n + (include ? -1 : 1)));
  }

  function buildRequest(digest?: string | null): BulkRequest | null {
    if (!productCode) {
      toast.error("Pick a product.");
      return null;
    }
    if (action === "set_plan" && !targetPlan) {
      toast.error("Pick the plan to move members to.");
      return null;
    }
    if (selectorIsEmpty(selector)) {
      toast.error("Select some members first.");
      return null;
    }
    return {
      product_code: productCode,
      action,
      target_plan_code: action === "set_plan" ? targetPlan : null,
      selector: toQuery(selector),
      dependant_action: dependantMode
        ? { mode: dependantMode, dependant_ids: [] }
        : null,
      ...(digest ? { selection_digest: digest } : {}),
    };
  }

  function runPreview(nextPage = 0) {
    const body = buildRequest();
    if (!body) return;
    preview.mutate(
      { body, offset: nextPage * ROWS_PER_PAGE, limit: ROWS_PER_PAGE },
      {
        onSuccess: (r) => {
          setResult(r);
          setPage(nextPage);
          // The request carried the exclusions, so the server's counts already
          // leave the unticked rows out. Not clearing this subtracted them a
          // SECOND time — paging after unticking three people quoted an Apply
          // count three lower than what apply would actually change.
          setUntickedChanging(0);
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  function runApply() {
    // The digest from the preview rides along: if the roster moved since, the
    // server refuses rather than applying to a population nobody approved.
    const body = buildRequest(result?.selection_digest);
    if (!body) return;
    apply.mutate(body, {
      onSuccess: (r) => {
        setResult(r);
        setPage(0);
        setConfirmApply(false);
        toast.success(`Applied — ${r.counts.applied ?? 0} member(s) changed.`);
      },
      onError: (e) => {
        setConfirmApply(false);
        if (
          e instanceof ConflictDetailError &&
          e.detail.code === "selection_changed"
        ) {
          setResult(null);
          toast.error(
            "The roster changed since this preview — run Preview again and check the numbers.",
          );
          return;
        }
        toast.error(formatError(e));
      },
    });
  }

  const applied = !!result && (result.counts.applied ?? 0) > 0;
  const willChange = result
    ? Math.max(
        0,
        (result.counts.would_apply ?? 0) +
          (result.counts.applied ?? 0) -
          (applied ? 0 : untickedChanging),
      )
    : 0;

  return (
    <div className="space-y-4">
      {/* ── 1. Who ─────────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-1">
          <h3 className="text-sm font-semibold text-foreground">
            1. Who changes
          </h3>
          <InfoHint>
            Filter the roster instead of typing staff IDs. Everything the filters
            match is selected; you can paste extra members in, and untick anyone
            in the results before applying.
          </InfoHint>
        </div>
        <div className="mt-3">
          <MemberSelector
            policyYearId={policyYearId}
            facets={facets}
            facetsLoading={facetsLoading}
            productCode={productCode || undefined}
            productId={productFacet?.id}
            state={selector}
            onChange={updateSelector}
          />
        </div>
      </section>

      {/* ── 2. What ────────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-foreground">2. What changes</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label htmlFor="bulk-product">Product</Label>
            <NativeSelect
              id="bulk-product"
              className="w-full"
              value={productCode}
              onChange={(e) => {
                const next = e.target.value;
                const opt = productOptions.find((p) => p.code === next);
                setProductCode(next);
                setTargetPlan("");
                // A product with no plans configured this year can only be
                // declined. Switching the action here rather than leaving an
                // impossible one selected: "Move to a plan" with an empty plan
                // list is a control that cannot be completed.
                if (opt && opt.plans.length === 0) setAction("decline");
                // Dependant cover means nothing on a product that has none.
                if (opt && !opt.hasDependants) setDependantMode("");
                // The current-plan filter is scoped to the product — a code from
                // the previous product would silently match nobody.
                setSelector({ ...selector, currentPlanCodes: [] });
                resetResult();
              }}
            >
              <option value="">Select product</option>
              {productOptions.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.code}
                  {p.name && p.name !== p.code ? ` — ${p.name}` : ""}
                </option>
              ))}
            </NativeSelect>
          </div>

          <div>
            <Label htmlFor="bulk-action">Action</Label>
            <NativeSelect
              id="bulk-action"
              className="w-full"
              value={action}
              onChange={(e) => {
                setAction(e.target.value as BulkAction);
                resetResult();
              }}
              disabled={!productCode}
            >
              <option value="set_plan" disabled={planOptions.length === 0}>
                Move to a plan
              </option>
              <option value="decline">Decline the product</option>
            </NativeSelect>
          </div>

          <div>
            <Label htmlFor="bulk-target">Move to plan</Label>
            <NativeSelect
              id="bulk-target"
              className="w-full"
              value={targetPlan}
              onChange={(e) => {
                setTargetPlan(e.target.value);
                resetResult();
              }}
              disabled={!productCode || action !== "set_plan" || !planOptions.length}
            >
              <option value="">
                {productCode && !planOptions.length
                  ? "No plans configured this year"
                  : "Select plan"}
              </option>
              {planOptions.map((c) => (
                <option key={c} value={c}>
                  {c}
                  {planHeadcount.has(c) ? ` — ${planHeadcount.get(c)} today` : ""}
                </option>
              ))}
            </NativeSelect>
          </div>

          <div>
            <div className="flex items-center gap-1">
              <Label htmlFor="bulk-deps">Dependant cover</Label>
              <InfoHint>
                Leave unchanged unless you mean to move it — “Cover all” elects
                every active dependant, “Cover none” removes them all. Disabled
                on a product that carries no dependant cover.
              </InfoHint>
            </div>
            <NativeSelect
              id="bulk-deps"
              className="w-full"
              value={dependantMode}
              onChange={(e) => {
                setDependantMode(e.target.value as DependantMode);
                resetResult();
              }}
              disabled={
                !productCode ||
                action === "decline" ||
                !selectedProduct?.hasDependants
              }
            >
              <option value="">
                {selectedProduct && !selectedProduct.hasDependants
                  ? "No dependant cover on this product"
                  : "Leave unchanged"}
              </option>
              <option value="include_all">Cover all dependants</option>
              <option value="exclude_all">Cover no dependants</option>
            </NativeSelect>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            onClick={() => runPreview(0)}
            disabled={preview.isPending}
          >
            {preview.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Search className="size-4" />
            )}
            Preview
          </Button>
          <Button
            onClick={() => setConfirmApply(true)}
            disabled={apply.isPending || !result || willChange === 0 || applied}
            title={result ? undefined : "Run Preview first"}
          >
            <Play className="size-4" />
            {result && !applied ? `Apply ${willChange} change${willChange === 1 ? "" : "s"}` : "Apply"}
          </Button>
          {!result && (
            <span className="text-xs text-muted-foreground">
              Preview first — Apply runs against the population you just checked.
            </span>
          )}
        </div>
      </section>

      {/* ── 3. Check & apply ───────────────────────────────────────────── */}
      {result && (
        <section className="rounded-lg border border-border bg-card">
          <div className="border-b border-border px-4 py-3">
            <div className="flex flex-wrap items-center gap-4 text-sm">
              {Object.entries(result.counts)
                .filter(([, v]) => v > 0)
                .map(([k, v]) => (
                  <span key={k} className={cn("font-medium", OUTCOME_COLOR[k])}>
                    {v} {OUTCOME_LABEL[k] ?? k.replace("_", " ")}
                  </span>
                ))}
              {result.counts.error > 0 && (
                <TriangleAlert className="size-4 text-error" />
              )}
              {untickedChanging > 0 && !applied && (
                // The server counted before these were unticked, so the header
                // and the Apply button would otherwise quote different numbers
                // with no explanation of the gap.
                <span className="text-muted-foreground">
                  {untickedChanging} excluded from this run
                </span>
              )}
            </div>

            {result.groups.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {result.groups.map((g, i) => (
                  <Badge key={i} variant="outline" className="gap-1.5">
                    <span className="text-muted-foreground">
                      {g.from_plan ?? "—"}
                    </span>
                    <span aria-hidden>&rarr;</span>
                    <span className="text-foreground">
                      {g.declined_after ? "Declined" : (g.to_plan ?? "—")}
                    </span>
                    <span className="text-muted-foreground">· {g.count}</span>
                  </Badge>
                ))}
              </div>
            )}

            {(result.impact.flex_price_tag_delta !== 0 ||
              result.impact.unpriced > 0) && (
              <p className="mt-3 text-xs text-muted-foreground">
                Flex price tags{" "}
                <span
                  className={cn(
                    "font-medium",
                    result.impact.flex_price_tag_delta > 0 ? "text-warn" : "text-good",
                  )}
                >
                  {result.impact.flex_price_tag_delta > 0 ? "+" : ""}
                  {fmtCurrency(result.impact.flex_price_tag_delta)}
                </span>{" "}
                across {result.impact.members_changing} member
                {result.impact.members_changing === 1 ? "" : "s"}
                {result.impact.unpriced > 0 && (
                  <span className="text-warn">
                    {" "}
                    · {result.impact.unpriced} unpriced (not in the total)
                  </span>
                )}
              </p>
            )}
          </div>

          <div className="px-4 py-3">
            <SectionLabel>
              Members ({result.rows_total.toLocaleString()})
            </SectionLabel>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-4 font-medium">Staff</th>
                    <th className="py-2 pr-4 font-medium">Name</th>
                    <th className="py-2 pr-4 font-medium">Outcome</th>
                    <th className="py-2 pr-4 font-medium">From &rarr; To</th>
                    <th className="py-2 pr-4 font-medium">Note</th>
                    <th className="py-2 font-medium text-right">Include</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {result.rows.map((r, i) => {
                    const excluded =
                      !!r.employee_id && selector.excludedIds.includes(r.employee_id);
                    return (
                      <tr
                        key={r.employee_id ?? r.staff_id ?? i}
                        className={cn(excluded && "opacity-50")}
                      >
                        <td className="py-2 pr-4 font-mono text-xs">
                          {r.staff_id ?? "—"}
                        </td>
                        <td className="py-2 pr-4">{r.employee_name ?? "—"}</td>
                        <td className={cn("py-2 pr-4", OUTCOME_COLOR[r.outcome])}>
                          {OUTCOME_LABEL[r.outcome] ?? r.outcome}
                        </td>
                        <td className="py-2 pr-4 text-xs text-muted-foreground">
                          {r.declined_before ? "Declined" : (r.from_plan ?? "—")}
                          {" → "}
                          {r.declined_after ? "Declined" : (r.to_plan ?? "—")}
                        </td>
                        <td className="py-2 pr-4 text-xs text-muted-foreground">
                          {r.reason ?? ""}
                        </td>
                        <td className="py-2 text-right">
                          {r.employee_id && r.outcome !== "error" && (
                            <input
                              type="checkbox"
                              className="size-4 accent-[var(--color-primary)]"
                              aria-label={`Include ${r.staff_id ?? "member"}`}
                              checked={!excluded}
                              disabled={applied}
                              onChange={(e) =>
                                // Unticking writes an EXCLUSION on the query, not
                                // a shorter id list — the rule survives, and the
                                // apply request stays the same size at 4 members
                                // or 4,000.
                                toggleExclude(
                                  r.employee_id as string,
                                  e.target.checked,
                                  r.outcome === "would_apply",
                                )
                              }
                            />
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {/* Paging re-runs the PREVIEW, so it must not be offered on an
                applied result: it would replace the record of what just ran
                with a fresh dry-run and re-enable Apply against a batch that
                has already happened. Reading page 2 of a completed batch needs
                the stored record (phase 4). */}
            {applied ? (
              result.rows_total > result.rows.length && (
                <p className="pt-3 text-xs text-muted-foreground">
                  Showing the first {result.rows.length} of{" "}
                  {result.rows_total.toLocaleString()} rows.
                </p>
              )
            ) : (
              <PaginationControls
                page={page}
                pages={Math.ceil((result.rows_total || 0) / ROWS_PER_PAGE)}
                onPageChange={(next) => runPreview(next)}
              />
            )}
          </div>
        </section>
      )}

      <AlertDialog
        open={confirmApply}
        onOpenChange={setConfirmApply}
        title={`Apply this change to ${willChange} member${willChange === 1 ? "" : "s"}?`}
        description={
          action === "decline"
            ? `This opts ${willChange} member(s) out of ${productCode}. Their coverage ends immediately.`
            : `This moves ${willChange} member(s) to ${targetPlan} on ${productCode}. It updates their effective coverage immediately.`
        }
        confirmLabel="Apply"
        confirmVariant="default"
        loading={apply.isPending}
        onConfirm={runApply}
      />
    </div>
  );
}
