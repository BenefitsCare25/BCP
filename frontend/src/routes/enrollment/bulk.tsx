/**
 * Coverage changes — move, decline or revert coverage for many members at once.
 *
 * Four panels, one page: WHO (a rule over the roster), WHAT changes (a set, one
 * row per product), CHECK & APPLY (summary first, warnings, rows as the
 * drill-down), and what has already been applied. The page it replaces asked the
 * broker to paste every staff ID, offered only "set plan" of the actions the API
 * supports, and rendered every affected member as a row whether there were four
 * of them or two thousand.
 *
 * Four rules the flow depends on:
 *
 * - **Apply sends the RULE, not the ticked ids.** Unticking a row adds an
 *   exclusion to the same query, so what is applied is provably the population
 *   that was previewed — and the request stays the same size at 4 members or
 *   4,000.
 * - **A preview belongs to the inputs that produced it.** Any change to the
 *   selection or the change set clears it, because an Apply button enabled
 *   against a stale preview applies numbers nobody checked.
 * - **An unacknowledged warning blocks Apply here as well as on the server.**
 *   The server is the gate; doing it in the UI too means the broker meets the
 *   decision while looking at the numbers, not as a 409 after pressing Apply.
 * - **Every apply carries a request id.** A double-click, or a retry after a
 *   timeout on a batch that actually committed, must not apply twice.
 */
import { useMemo, useState } from "react";
import { History, Loader2, Play, Search, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import { useMe, usePlans, useProducts } from "@/api/hooks";
import { useMemberFacets } from "@/api/memberQuery";
import {
  type BulkBatchDetail,
  type BulkRequest,
  type BulkResult,
  useApplyBulk,
  useBulkHistory,
  usePreviewBulk,
} from "@/api/enrollment";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SectionLabel } from "@/components/ui/section-label";
import { InfoHint } from "@/components/ui/tooltip";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { PaginationControls } from "@/components/ui/pagination-controls";
import {
  EMPTY_SELECTOR,
  MemberSelector,
  type SelectorState,
  fromQuery,
  selectorIsEmpty,
  toQuery,
} from "@/components/enrollment/bulk/MemberSelector";
import {
  type ChangeDraft,
  ChangeSetEditor,
  EMPTY_CHANGE,
  type ProductOption,
  changeSetError,
  toChanges,
} from "@/components/enrollment/bulk/ChangeSetEditor";
import {
  WarningPanel,
  outstandingWarnings,
} from "@/components/enrollment/bulk/WarningPanel";
import { BatchHistory } from "@/components/enrollment/bulk/BatchHistory";

const MAX_FULLY_REVERSIBLE_CHANGES = 5000;

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

const ROWS_PER_PAGE = 100;

export function EnrollmentBulkPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: me } = useMe();
  const { data: plans } = usePlans(policyYearId);
  const { data: products } = useProducts();
  const { data: facets, isLoading: facetsLoading } = useMemberFacets(policyYearId);
  const history = useBulkHistory(policyYearId);
  const preview = usePreviewBulk(policyYearId);
  const apply = useApplyBulk(policyYearId);

  const [changes, setChanges] = useState<ChangeDraft[]>([EMPTY_CHANGE]);
  const [selector, setSelector] = useState<SelectorState>(EMPTY_SELECTOR);
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [result, setResult] = useState<BulkResult | null>(null);
  const [page, setPage] = useState(0);
  const [confirmApply, setConfirmApply] = useState(false);
  // Identifies this APPLY ATTEMPT, not this click. Minted when a preview lands
  // and cleared whenever the inputs change, so every retry of the same checked
  // batch carries the same id and the server can replay it instead of applying
  // it twice.
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
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
  const productOptions: ProductOption[] = useMemo(() => {
    const byCode = new Map<string, ProductOption>();
    const meta = new Map((products ?? []).map((p) => [p.code, p]));
    for (const p of facets?.products ?? []) {
      byCode.set(p.code, {
        code: p.code,
        name: p.name,
        plans: plansByCode[p.code] ?? [],
        hasDependants: meta.get(p.code)?.has_dependants ?? false,
        planHeadcount: new Map(p.plans.map((pl) => [pl.code, pl.count])),
      });
    }
    for (const [code, planCodes] of Object.entries(plansByCode)) {
      if (byCode.has(code)) continue;
      byCode.set(code, {
        code,
        name: meta.get(code)?.display_name ?? null,
        plans: planCodes,
        hasDependants: meta.get(code)?.has_dependants ?? false,
        planHeadcount: new Map(),
      });
    }
    return [...byCode.values()].sort((a, b) => a.code.localeCompare(b.code));
  }, [facets, plansByCode, products]);

  // The FIRST change is what the coverage filters resolve against — a member can
  // be on Plan 1 of GHS and Plan 3 of GTL, so "everyone on Plan 1" would name
  // two populations if the scope were per change.
  const scopeCode = changes[0]?.productCode || undefined;
  const scopeFacet = facets?.products.find((p) => p.code === scopeCode);

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
        Member Coverage shows each member&apos;s current plan.
      </p>
    );
  }

  /** Any input change invalidates the preview it produced — and the warnings
   *  that were accepted against it, which described a different population. */
  function resetResult() {
    setResult(null);
    setPage(0);
    setUntickedChanging(0);
    setAcknowledged([]);
    setAttemptId(null);
  }

  function updateSelector(next: SelectorState) {
    setSelector(next);
    resetResult();
  }

  function updateChanges(next: ChangeDraft[]) {
    // Switching the scoping product invalidates a plan filter chosen for the
    // previous one — that code would silently match nobody.
    const scopeChanged = next[0]?.productCode !== changes[0]?.productCode;
    setChanges(next);
    if (scopeChanged) setSelector((prev) => ({ ...prev, currentPlanCodes: [] }));
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

  function buildRequest(extra?: Partial<BulkRequest>): BulkRequest | null {
    const problem = changeSetError(changes);
    if (problem) {
      toast.error(problem);
      return null;
    }
    if (selectorIsEmpty(selector)) {
      toast.error("Select some members first.");
      return null;
    }
    return { query: toQuery(selector), changes: toChanges(changes), ...extra };
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
          // One id per checked batch, kept across paging so reading page 3 and
          // then applying doesn't mint a second one.
          setAttemptId((prev) => prev ?? crypto.randomUUID());
          // The request carried the exclusions, so the server's counts already
          // leave the unticked rows out. Not clearing this subtracted them a
          // SECOND time — paging after unticking three people quoted an Apply
          // count three lower than what apply would actually change.
          setUntickedChanging(0);
          // Keep only acceptances the new preview still raises: a warning that
          // no longer applies must not stay ticked, and one that has just
          // appeared must not arrive pre-accepted.
          setAcknowledged((prev) =>
            prev.filter((code) => r.warnings.some((w) => w.code === code)),
          );
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  function runApply() {
    if (!result?.selection_digest || !attemptId) {
      toast.error("Run Preview again before applying this change.");
      return;
    }
    // The digest from the preview rides along: if the roster moved since, the
    // server refuses rather than applying to a population nobody approved.
    //
    // The request id is minted ONCE per previewed body (`attemptId`, cleared by
    // `resetResult`) and reused by every retry of it. Generating a fresh one
    // here made the guard unreachable for the case it exists for: a second
    // press, or a retry after a timeout on a batch that actually committed,
    // carried a DIFFERENT id and applied the whole thing again.
    const body = buildRequest({
      selection_digest: result.selection_digest,
      acknowledge: acknowledged,
      request_id: attemptId,
    });
    if (!body) return;
    apply.mutate(body, {
      onSuccess: (r) => {
        setResult(r);
        setPage(0);
        setConfirmApply(false);
        toast.success(`Applied — ${r.counts.applied ?? 0} change(s) written.`);
      },
      onError: (e) => {
        setConfirmApply(false);
        if (e instanceof ConflictDetailError) {
          if (e.detail.code === "selection_changed") {
            setResult(null);
            toast.error(
              "The roster changed since this preview — run Preview again and check the numbers.",
            );
            return;
          }
          if (e.detail.code === "unacknowledged_warnings") {
            toast.error("Confirm each warning below before applying.");
            return;
          }
        }
        toast.error(formatError(e));
      },
    });
  }

  function loadPastSelection(detail: BulkBatchDetail) {
    setSelector(fromQuery(detail.query));
    setChanges(
      detail.changes.length
        ? detail.changes.map((c) => ({
            productCode: c.product_code,
            action: c.action,
            targetPlan: c.target_plan_code ?? "",
            dependantMode:
              c.dependant_action?.mode === "include_all" ||
              c.dependant_action?.mode === "exclude_all"
                ? c.dependant_action.mode
                : "",
          }))
        : [EMPTY_CHANGE],
    );
    resetResult();
    setShowHistory(false);
  }

  const applied = !!result && (result.counts.applied ?? 0) > 0;
  const outstanding = result ? outstandingWarnings(result.warnings, acknowledged) : [];
  const willChange = result
    ? Math.max(
        0,
        (result.counts.would_apply ?? 0) +
          (result.counts.applied ?? 0) -
          (applied ? 0 : untickedChanging),
      )
    : 0;
  const changedProducts = [...new Set(changes.map((c) => c.productCode).filter(Boolean))];

  return (
    <div className="space-y-4">
      {/* ── 1. Who ─────────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-1">
          <h2 className="text-sm font-semibold text-foreground">
            1. Who changes
          </h2>
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
            productCode={scopeCode}
            productId={scopeFacet?.id}
            state={selector}
            onChange={updateSelector}
          />
        </div>
      </section>

      {/* ── 2. What ────────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-1">
          <h2 className="text-sm font-semibold text-foreground">2. What changes</h2>
          <InfoHint>
            Several products can move in one run — they apply together or not at
            all, so a renewal never leaves the roster half-moved.
          </InfoHint>
        </div>
        <div className="mt-3">
          <ChangeSetEditor
            drafts={changes}
            products={productOptions}
            disabled={apply.isPending}
            onChange={updateChanges}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
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
            disabled={
              apply.isPending ||
              !result ||
              willChange === 0 ||
              willChange > MAX_FULLY_REVERSIBLE_CHANGES ||
              applied ||
              outstanding.length > 0
            }
            title={
              !result
                ? "Run Preview first"
                : outstanding.length
                  ? "Confirm the warnings below first"
                  : willChange > MAX_FULLY_REVERSIBLE_CHANGES
                    ? `Narrow the selection to ${MAX_FULLY_REVERSIBLE_CHANGES.toLocaleString()} changes or fewer so the whole batch remains undoable`
                  : undefined
            }
          >
            <Play className="size-4" />
            {result && !applied
              ? `Apply ${willChange} change${willChange === 1 ? "" : "s"}`
              : "Apply"}
          </Button>
          {!result && (
            <span className="text-xs text-muted-foreground">
              Preview first — Apply runs against the population you just checked.
            </span>
          )}
          {result && outstanding.length > 0 && !applied && (
            <span className="text-xs text-warn">
              Confirm {outstanding.length} warning
              {outstanding.length === 1 ? "" : "s"} below to continue.
            </span>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => setShowHistory((v) => !v)}
          >
            <History className="size-4" />
            {showHistory ? "Hide past changes" : "Past changes"}
          </Button>
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
                    {g.product_code && (
                      <span className="font-medium text-foreground">
                        {g.product_code}
                      </span>
                    )}
                    <span className="text-muted-foreground">
                      {g.from_plan ?? "—"}
                    </span>
                    <span aria-hidden>&rarr;</span>
                    <span className="text-foreground">
                      {g.declined_after
                        ? "Declined"
                        : g.reverted
                          ? `${g.to_plan ?? "—"} (cohort default)`
                          : (g.to_plan ?? "—")}
                    </span>
                    <span className="text-muted-foreground">· {g.count}</span>
                  </Badge>
                ))}
              </div>
            )}

            <ImpactLine result={result} />
          </div>

          {result.warnings.length > 0 && (
            <div className="border-b border-border px-4 py-3">
              <WarningPanel
                warnings={result.warnings}
                acknowledged={acknowledged}
                readOnly={applied}
                onToggle={(code, accepted) =>
                  setAcknowledged((prev) =>
                    accepted ? [...prev, code] : prev.filter((c) => c !== code),
                  )
                }
              />
            </div>
          )}

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
                    {changedProducts.length > 1 && (
                      <th className="py-2 pr-4 font-medium">Product</th>
                    )}
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
                        key={`${r.employee_id ?? r.staff_id ?? i}-${r.product_code ?? ""}`}
                        className={cn(excluded && "opacity-50")}
                      >
                        <td className="py-2 pr-4 font-mono text-xs">
                          {r.staff_id ?? "—"}
                        </td>
                        <td className="py-2 pr-4">{r.employee_name ?? "—"}</td>
                        {changedProducts.length > 1 && (
                          <td className="py-2 pr-4 text-xs text-muted-foreground">
                            {r.product_code ?? "—"}
                          </td>
                        )}
                        <td className={cn("py-2 pr-4", OUTCOME_COLOR[r.outcome])}>
                          {OUTCOME_LABEL[r.outcome] ?? r.outcome}
                        </td>
                        <td className="py-2 pr-4 text-xs text-muted-foreground">
                          {r.declined_before ? "Declined" : (r.from_plan ?? "—")}
                          {" → "}
                          {r.declined_after ? "Declined" : (r.to_plan ?? "—")}
                          {r.override_cleared && r.outcome !== "no_change" && (
                            <span className="text-2xs"> (cohort default)</span>
                          )}
                        </td>
                        <td className="py-2 pr-4 text-xs text-muted-foreground">
                          {r.reason ?? ""}
                          {r.warnings.length > 0 && (
                            <span className="text-warn">
                              {r.reason ? " · " : ""}
                              {r.warnings.join(", ").replace(/_/g, " ")}
                            </span>
                          )}
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
                has already happened. Reading page 2 of a completed batch is
                what "Past changes" is for. */}
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

      {/* ── 4. What has already been applied ───────────────────────────── */}
      {showHistory && (
        <section className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-1">
            <h2 className="text-sm font-semibold text-foreground">Past changes</h2>
            <InfoHint>
              Re-run loads the stored selection back into the builder — it
              re-resolves against today&apos;s roster and still has to be
              previewed. Undo puts each member back to what the batch replaced,
              skipping anyone whose coverage has moved since.
            </InfoHint>
          </div>
          <div className="mt-3">
            <BatchHistory
              batches={history.data}
              loading={history.isLoading}
              onReRun={loadPastSelection}
            />
          </div>
        </section>
      )}

      <AlertDialog
        open={confirmApply}
        onOpenChange={setConfirmApply}
        title={`Apply ${willChange} change${willChange === 1 ? "" : "s"}?`}
        description={
          `This updates effective coverage on ${changedProducts.join(", ") || "the selected products"} ` +
          `immediately.` +
          (acknowledged.length
            ? ` You have accepted ${acknowledged.length} warning${acknowledged.length === 1 ? "" : "s"}, which is recorded on the change.`
            : "") +
          " Every change in this batch can be undone from Past changes."
        }
        confirmLabel="Apply"
        confirmVariant="default"
        loading={apply.isPending}
        onConfirm={runApply}
      />
    </div>
  );
}

/**
 * The money line. Flex is exact (the price tag is what this tool writes);
 * cover and premium come from resolving each member's target to a tier of their
 * own cohort, so members whose basis will not resolve are reported as excluded
 * rather than silently counted as zero.
 */
function ImpactLine({ result }: { result: BulkResult }) {
  const i = result.impact;
  const parts: React.ReactNode[] = [];
  if (i.flex_price_tag_delta !== 0) {
    parts.push(
      <span key="flex">
        Flex price tags{" "}
        <Delta value={i.flex_price_tag_delta} />
      </span>,
    );
  }
  if (i.annual_premium_delta !== 0) {
    parts.push(
      <span key="premium">
        Annual premium <Delta value={i.annual_premium_delta} />
      </span>,
    );
  }
  if (i.sum_insured_delta !== 0) {
    parts.push(
      <span key="si">
        Sum insured <Delta value={i.sum_insured_delta} invert />
      </span>,
    );
  }
  if (!parts.length && !i.unpriced && !i.financials_unresolved) return null;

  return (
    <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {parts.map((p, idx) => (
        <span key={idx} className="flex items-center gap-1">
          {idx > 0 && <span aria-hidden>·</span>}
          {p}
        </span>
      ))}
      <span>
        across {i.members_changing} change{i.members_changing === 1 ? "" : "s"}
      </span>
      {i.unpriced > 0 && (
        <span className="text-warn">· {i.unpriced} unpriced (not in the total)</span>
      )}
      {i.financials_unresolved > 0 && (
        <span>
          · {i.financials_unresolved} with no per-member figure (not in the
          premium or cover totals)
        </span>
      )}
    </p>
  );
}

/** A rise in spend is a warning; a rise in COVER is not — hence `invert`. */
function Delta({ value, invert }: { value: number; invert?: boolean }) {
  const bad = invert ? value < 0 : value > 0;
  return (
    <span className={cn("font-medium", bad ? "text-warn" : "text-good")}>
      {value > 0 ? "+" : ""}
      {fmtCurrency(value)}
    </span>
  );
}
