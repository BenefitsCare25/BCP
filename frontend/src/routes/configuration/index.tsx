import { useMemo, useState } from "react";
import {
  useAIStatus,
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
import { RecommendationPanel } from "@/components/configuration/RecommendationPanel";
import { SlipPeriodBanner } from "@/components/configuration/SlipPeriodBanner";
import {
  SlipUploadButton,
  SlipUploadPanel,
  useSlipUpload,
} from "@/components/configuration/UploadCard";
import { PageGuide } from "@/components/ui/page-guide";
import { useSession } from "@/stores/session";
import type { Category, InsuranceLine } from "@/types";
import { INSURANCE_LINES, LINE_LABELS } from "@/lib/insuranceLines";

export function ConfigurationPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: groups = [], isLoading } = useCategoriesGrouped(
    policyYearId ?? undefined,
  );
  const { data: schema = [] } = useEmployeeAttributes();
  const { data: aiStatus } = useAIStatus();
  const { data: audit } = useAuditLog();
  const { data: policyYears = [] } = usePolicyYears();
  const [tab, setTab] = useState<InsuranceLine>("medical");
  const [selected, setSelected] = useState<Category | null>(null);
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

  const countByLine = useMemo(() => {
    const c: Record<InsuranceLine, number> = { medical: 0, life: 0, flex: 0 };
    for (const g of groups) c[g.line] += g.categories.length;
    return c;
  }, [groups]);

  if (!policyYearId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No policy year</CardTitle>
          <CardDescription>
            Pick a policy year from the top bar to begin.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-5 max-w-7xl">
      {/* Benefit-year management applies to the insured configuration; Flex is a
          separate module, so it doesn't carry the benefit-years section. */}
      {tab !== "flex" && <BenefitYearPanel years={policyYears} />}

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
                <FlexPanel policyYearId={policyYearId} />
              ) : (
                <LineTab
                  policyYearId={policyYearId}
                  line={line}
                  groups={groupsByLine[line]}
                  onSelectCategory={setSelected}
                />
              )}
            </TabsContent>
          ))}
        </Tabs>
      )}

      {/* Slip-driven attribute/product recommendations — insured products only. */}
      {tab !== "flex" && (
        <RecommendationPanel
          policyYearId={policyYearId}
          aiConfigured={Boolean(aiStatus?.configured)}
          hasCategories={groups.length > 0}
        />
      )}

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

      <PageGuide
        purpose="Manage benefit categories extracted from placement slips. Each category maps a plan description to a product code and matching rule. Use AI to generate rules, then bulk-confirm high-confidence ones."
        connections={[
          { label: "← Placement slips", description: "Categories are auto-extracted when a placement slip is uploaded" },
          { label: "→ Match results", description: "Confirmed categories with rules drive the employee matching engine" },
          { label: "← AI provider", description: "AI rule suggestions require a configured AI provider under Schema" },
        ]}
      />
    </div>
  );
}
