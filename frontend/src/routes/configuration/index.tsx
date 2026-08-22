import { useCallback, useEffect, useMemo, useState } from "react";
import { useBlocker, useSearch } from "@tanstack/react-router";
import {
  useAuditLog,
  useCategoriesGrouped,
  useEmployeeAttributes,
  usePolicyYears,
  useProductSetups,
  useSetupProducts,
} from "@/api/hooks";
import { useRegistry } from "@/api/registry";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { SkeletonTable } from "@/components/ui/skeleton";
import { BenefitYearPanel } from "@/components/configuration/BenefitYearPanel";
import { CategoryEditPanel } from "@/components/configuration/CategoryEditPanel";
import { LineTab } from "@/components/configuration/LineTab";
import { FlexPanel } from "@/components/configuration/flex/FlexPanel";
import { FlexUploadCard } from "@/components/configuration/flex/FlexUploadCard";
import { SlipPeriodBanner } from "@/components/configuration/SlipPeriodBanner";
import {
  SlipUploadButton,
  SlipUploadPanel,
  useSlipUpload,
} from "@/components/configuration/UploadCard";
import { cn } from "@/lib/cn";
import { defaultPolicyYear, isPastPolicyPeriod } from "@/lib/policy-year";
import { useSession } from "@/stores/session";
import type { Category, InsuranceLine } from "@/types";
import {
  INSURANCE_LINES,
  LINE_LABELS,
  isProductAdded,
  lineForCode,
} from "@/lib/insuranceLines";

export function ConfigurationPage() {
  const { data: policyYears = [], isLoading: yearsLoading } = usePolicyYears();
  const selectedYearId = useSession((s) => s.currentPolicyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);
  // The shell owns one benefit-year context for every company page. Derive the
  // same default synchronously on a cold load so this page never flashes an
  // empty state before ContextBar has persisted it.
  const policyYearId =
    selectedYearId && policyYears.some((y) => y.id === selectedYearId)
      ? selectedYearId
      : defaultPolicyYear(policyYears)?.id ?? null;
  const viewedYear = policyYears.find((y) => y.id === policyYearId) ?? null;
  const readOnly = viewedYear
    ? isPastPolicyPeriod(viewedYear.coverage_end)
    : false;

  const { data: groups = [], isLoading } = useCategoriesGrouped(
    policyYearId ?? undefined,
  );
  const { data: setupProducts = [] } = useSetupProducts(
    policyYearId ?? undefined,
  );
  const { data: setups = [] } = useProductSetups(policyYearId ?? undefined);
  const { data: registry } = useRegistry();
  const { data: schema = [] } = useEmployeeAttributes();
  const { data: audit } = useAuditLog();
  // Initial line tab is deep-linkable via ?tab= (e.g. the Reports Center "Flex
  // Coverage" card links to ?tab=flex); unknown values fall back to medical.
  const search = useSearch({ strict: false }) as { tab?: string };
  const [tab, setTab] = useState<InsuranceLine>(
    INSURANCE_LINES.includes(search.tab as InsuranceLine)
      ? (search.tab as InsuranceLine)
      : "medical",
  );
  const [selected, setSelected] = useState<Category | null>(null);
  const [blockingEdit, setBlockingEdit] = useState<{
    line: InsuranceLine;
    code: string;
    name: string;
    sections: string[];
    discard: () => void;
  } | null>(null);
  const [linePromptOpen, setLinePromptOpen] = useState(false);
  const [pendingLine, setPendingLine] = useState<InsuranceLine | null>(null);
  // Switching the viewed year closes any open category editor — the panel edits
  // a category from the previously-viewed year and must not linger over another
  // year (or over a read-only past year, where the page is otherwise disabled).
  useEffect(() => {
    setSelected(null);
  }, [policyYearId]);
  // Placement-slip upload state, lifted so the trigger button sits on the tab
  // row while the extraction-result panel renders below the tabs.
  const slip = useSlipUpload(policyYearId ?? "");

  const groupsByLine = useMemo(() => {
    const by: Record<InsuranceLine, typeof groups> = {
      medical: [],
      general: [],
      life: [],
      flex: [],
    };
    for (const g of groups) by[g.line].push(g);
    return by;
  }, [groups]);

  const handleBlockingEditChange = useCallback(
    (
      editLine: InsuranceLine,
      edit: {
        code: string;
        name: string;
        sections: string[];
        discard: () => void;
      } | null,
    ) => {
      setBlockingEdit((prev) => {
        if (edit) {
          return {
            line: editLine,
            code: edit.code,
            name: edit.name,
            sections: edit.sections,
            discard: edit.discard,
          };
        }
        return prev?.line === editLine ? null : prev;
      });
    },
    [],
  );

  const switchLine = (next: InsuranceLine) => {
    if (next === tab) return;
    if (blockingEdit?.line === tab) {
      setPendingLine(next);
      setLinePromptOpen(true);
      return;
    }
    setTab(next);
  };

  const navigationBlocker = useBlocker({
    shouldBlockFn: ({ current, next }) =>
      Boolean(blockingEdit) &&
      (current.pathname !== next.pathname ||
        JSON.stringify(current.search) !== JSON.stringify(next.search)),
    enableBeforeUnload: () => Boolean(blockingEdit),
    disabled: !blockingEdit,
    withResolver: true,
  });

  useEffect(() => {
    if (navigationBlocker.status === "blocked") {
      setLinePromptOpen(true);
    }
  }, [navigationBlocker.status]);

  const closeSavePrompt = useCallback(() => {
    if (navigationBlocker.status === "blocked") {
      navigationBlocker.reset();
    }
    setPendingLine(null);
    setLinePromptOpen(false);
  }, [navigationBlocker]);

  const discardAndLeave = useCallback(() => {
    blockingEdit?.discard();
    if (navigationBlocker.status === "blocked") {
      navigationBlocker.proceed();
    } else if (pendingLine) {
      setTab(pendingLine);
    }
    setBlockingEdit(null);
    setPendingLine(null);
    setLinePromptOpen(false);
  }, [blockingEdit, navigationBlocker, pendingLine, setTab]);

  const draftCodes = useMemo(
    () => new Set(setups.map((s) => s.product_code)),
    [setups],
  );

  // Match LineTab's visible product list so badges do not count stale category
  // groups from historical product rows or aliases that collapse to one tab.
  const countByLine = useMemo(() => {
    const c: Record<InsuranceLine, number> = {
      medical: 0,
      general: 0,
      life: 0,
      flex: 0,
    };
    const seen = new Set<string>();
    for (const product of setupProducts) {
      const code = product.code.trim().toUpperCase();
      if (!code || seen.has(code) || !isProductAdded(product, draftCodes)) {
        continue;
      }
      seen.add(code);
      c[product.line ?? lineForCode(code, registry?.entries)] += 1;
    }
    return c;
  }, [draftCodes, registry, setupProducts]);

  if (!policyYearId) {
    // While the year list is still loading, show a skeleton rather than the
    // empty-state card — otherwise a cold load flashes "No benefit year".
    if (yearsLoading) {
      return <SkeletonTable rows={8} columns={4} />;
    }
    // The panel must render HERE too, not only below — it owns "Add benefit
    // year", so returning a bare card left a brand-new company with no way to
    // create its first year (the rest of the page is gated on having one).
    return (
      <div className="space-y-5">
        <Card>
          <CardHeader>
            <CardTitle>No benefit year</CardTitle>
            <CardDescription>
              Add a benefit year below to begin configuring this client. It sets
              the coverage period that categories, plans and claims all hang off.
            </CardDescription>
          </CardHeader>
        </Card>
        <BenefitYearPanel
          years={policyYears}
          viewingId={policyYearId}
          onViewYear={setPolicyYear}
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Config editing is disabled when viewing a past year. pointer-events is
          inherited, so re-enabling it on the tab navigation (Radix tablists +
          the product-form section tabs) keeps browsing available read-only. */}
      <div
        className={cn(
          readOnly &&
            "pointer-events-none select-none opacity-95 [&_[role=tablist]]:pointer-events-auto [&_.config-nav]:pointer-events-auto",
        )}
        aria-disabled={readOnly || undefined}
      >
        {isLoading ? (
          <SkeletonTable rows={8} columns={4} />
        ) : (
          <Tabs value={tab} onValueChange={(v) => switchLine(v as InsuranceLine)}>
            <div className="flex items-center justify-between gap-3">
              <TabsList>
                {INSURANCE_LINES.map((line) => (
                  <TabsTrigger key={line} value={line} className="gap-2">
                    {LINE_LABELS[line]}
                    {countByLine[line] > 0 && (
                      <Badge variant="outline">{countByLine[line]}</Badge>
                    )}
                  </TabsTrigger>
                ))}
              </TabsList>
              {/* Insured products upload an .xls placement slip; the Flex tab
                  uploads its own (AI-extracted) benefit documents. */}
              {tab === "flex" ? (
                <FlexUploadCard policyYearId={policyYearId} compact />
              ) : (
                <SlipUploadButton slip={slip} />
              )}
            </div>

            {/* Slip upload results + period guard — insured products only. */}
            {tab !== "flex" && (
              <>
                <SlipUploadPanel slip={slip} />
                <SlipPeriodBanner policyYearId={policyYearId} />
              </>
            )}

            {INSURANCE_LINES.map((line) => (
              <TabsContent key={line} value={line}>
                {line === "flex" ? (
                  <FlexPanel
                    policyYearId={policyYearId}
                  />
                ) : (
                  <LineTab
                    policyYearId={policyYearId}
                    line={line}
                    groups={groupsByLine[line]}
                    onSelectCategory={setSelected}
                    onBlockingEditChange={handleBlockingEditChange}
                  />
                )}
              </TabsContent>
            ))}
          </Tabs>
        )}
      </div>

      {/* Renewal management follows the product setup it versions and stays
          immediately above the audit trail. It remains interactive while a
          historical product setup is being viewed read-only. */}
      <BenefitYearPanel
        years={policyYears}
        viewingId={policyYearId}
        onViewYear={setPolicyYear}
      />

      {audit && audit.items.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent changes</CardTitle>
            <CardDescription>
              Last {audit.items.length} of {audit.total} mutations
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {audit.items.slice(0, 5).map((entry) => (
                <li
                  key={entry.id}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge variant="outline">{entry.action}</Badge>
                    <Badge variant="default">{entry.entity_type}</Badge>
                    {entry.cross_tenant_access && (
                      <Badge variant="error">Cross-tenant</Badge>
                    )}
                    <span className="text-xs text-muted-foreground font-mono truncate">
                      {entry.entity_id ?? "—"}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {new Date(entry.created_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <CategoryEditPanel
        category={selected}
        schema={schema}
        onClose={() => setSelected(null)}
      />
      <AlertDialog
        open={linePromptOpen}
        onOpenChange={(open) => {
          if (open) {
            setLinePromptOpen(true);
            return;
          }
          closeSavePrompt();
        }}
        title="Discard unsaved setup changes?"
        description={
          <div className="space-y-2">
            <p>
              <strong>{blockingEdit?.name ?? "This product"}</strong> has
              unsaved changes in:
            </p>
            <ul className="list-disc space-y-1 pl-5">
              {(blockingEdit?.sections ?? []).map((section) => (
                <li key={section}>{section}</li>
              ))}
            </ul>
            <p>Discarding restores the last saved setup and lets you leave.</p>
          </div>
        }
        confirmLabel="Discard changes & leave"
        cancelLabel="Continue editing"
        confirmVariant="destructive"
        tone="danger"
        onConfirm={discardAndLeave}
      />
    </div>
  );
}
