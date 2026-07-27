import { useEffect, useMemo, useState } from "react";
import { useSearch } from "@tanstack/react-router";
import {
  useAuditLog,
  useCategoriesGrouped,
  useEmployeeAttributes,
  usePolicyYears,
} from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import { ViewingYearSelect } from "@/components/configuration/ViewingYearSelect";
import { cn } from "@/lib/cn";
import { isPastPolicyPeriod } from "@/lib/policy-year";
import type { Category, InsuranceLine } from "@/types";
import { INSURANCE_LINES, LINE_LABELS } from "@/lib/insuranceLines";

export function ConfigurationPage() {
  const { data: policyYears = [], isLoading: yearsLoading } = usePolicyYears();
  const activeYearId =
    policyYears.find((y) => y.status === "active")?.id ??
    policyYears[0]?.id ??
    null;
  // Which benefit year the page views. Local to Configuration (the global
  // top-bar picker is gone; every other page follows the current year). Defaults
  // to the current year; a stale selection falls back to it. Past (ended) years
  // are read-only; the current + future years stay editable. The read-only /
  // set-current gates key off the coverage envelope (what the picker displays),
  // not the nominal span, so "is today in the period" matches the shown dates.
  const [viewingId, setViewingId] = useState<string | null>(null);
  const policyYearId =
    viewingId && policyYears.some((y) => y.id === viewingId)
      ? viewingId
      : activeYearId;
  const viewedYear = policyYears.find((y) => y.id === policyYearId) ?? null;
  const readOnly = viewedYear
    ? isPastPolicyPeriod(viewedYear.coverage_end)
    : false;

  const { data: groups = [], isLoading } = useCategoriesGrouped(
    policyYearId ?? undefined,
  );
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
      life: [],
      flex: [],
    };
    for (const g of groups) by[g.line].push(g);
    return by;
  }, [groups]);

  // Tab badge = number of products configured under each line (one group per
  // product_code), not the total category count. Skip the "(unassigned)"
  // pseudo-group — it holds categories not yet matched to a product.
  const countByLine = useMemo(() => {
    const c: Record<InsuranceLine, number> = { medical: 0, life: 0, flex: 0 };
    for (const g of groups) {
      if (g.product_code === "(unassigned)") continue;
      c[g.line] += 1;
    }
    return c;
  }, [groups]);

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
          onViewYear={setViewingId}
        />
      </div>
    );
  }

  // The benefit-year picker now sits beside the product title (passed down to
  // LineTab / FlexPanel). Only the active tab mounts, so this single node
  // renders in exactly one place at a time.
  const yearSelector = (
    <ViewingYearSelect
      value={policyYearId}
      years={policyYears}
      onChange={setViewingId}
    />
  );

  return (
    <div className="space-y-5">
      {/* Benefit-year management applies to the insured configuration; Flex is a
          separate module, so it doesn't carry the benefit-years section. Year
          management stays interactive even while viewing a read-only year. */}
      {tab !== "flex" && (
        <BenefitYearPanel
          years={policyYears}
          viewingId={policyYearId}
          onViewYear={setViewingId}
        />
      )}

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
          <Tabs value={tab} onValueChange={(v) => setTab(v as InsuranceLine)}>
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
                    yearSelector={yearSelector}
                  />
                ) : (
                  <LineTab
                    policyYearId={policyYearId}
                    line={line}
                    groups={groupsByLine[line]}
                    onSelectCategory={setSelected}
                    yearSelector={yearSelector}
                  />
                )}
              </TabsContent>
            ))}
          </Tabs>
        )}
      </div>

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
    </div>
  );
}
