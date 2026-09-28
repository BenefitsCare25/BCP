/** Enrollment — one workflow, four tabs:
 *
 *   Overview         the period: plan it, check it's ready, open, watch, close
 *   Members          everyone in the period and each one's selection
 *   Pricing & rules  what the benefit year prices (plan price tags, leave)
 *   Coverage changes rule-based bulk edits to live coverage
 *
 * The old page had five tabs mixing yearly setup, running a period and
 * per-member work, and led with a blank create form even mid-period. */
import { useNavigate, useSearch } from "@tanstack/react-router";
import {
  PageTabsBar,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { useMe } from "@/api/hooks";
import { PeriodOverview } from "@/components/enrollment/period/PeriodOverview";
import { EnrollmentElectionsPage } from "./elections";
import { EnrollmentBulkPage } from "./bulk";
import { EnrollmentRulesPage } from "./rules";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "members", label: "Members" },
  { key: "rules", label: "Pricing & rules" },
  { key: "bulk", label: "Coverage changes" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

// Keys that older links, redirects and bookmarks still carry. `?tab=leave`
// comes from the retired company-settings tab; `elections` from the retired
// /enrollment/elections route.
const LEGACY: Record<string, { tab: TabKey; section?: string }> = {
  windows: { tab: "overview" },
  elections: { tab: "members" },
  flex: { tab: "rules" },
  leave: { tab: "rules", section: "leave" },
};

function resolveTab(raw: string | undefined): { tab: TabKey; section?: string } {
  if (TABS.some((t) => t.key === raw)) return { tab: raw as TabKey };
  return (raw && LEGACY[raw]) || { tab: "overview" };
}

export function EnrollmentPage() {
  const navigate = useNavigate();
  const { data: me } = useMe();
  const readOnly = me?.role === "broker_viewer";
  const search = useSearch({ strict: false }) as { tab?: string; section?: string };
  const resolved = resolveTab(search.tab);
  const tab: TabKey = readOnly && resolved.tab === "bulk" ? "overview" : resolved.tab;

  return (
    <div className="space-y-4">
      {readOnly && (
        <p className="rounded-lg bg-muted px-4 py-2.5 text-sm text-muted-foreground">
          You have view-only access: you can follow enrolment progress and settings
          but not change them.
        </p>
      )}
      <Tabs
        value={tab}
        onValueChange={(value) =>
          navigate({ to: "/client-relations/enrollment", search: { tab: value } })
        }
      >
        <PageTabsBar className="overflow-x-auto">
          <TabsList className="min-w-max">
            {TABS.filter((t) => !readOnly || t.key !== "bulk").map((t) => (
              <TabsTrigger key={t.key} value={t.key}>
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </PageTabsBar>
        <TabsContent value="overview">
          <PeriodOverview readOnly={readOnly} />
        </TabsContent>
        <TabsContent value="members">
          <EnrollmentElectionsPage readOnly={readOnly} />
        </TabsContent>
        <TabsContent value="rules">
          <EnrollmentRulesPage
            readOnly={readOnly}
            section={search.section ?? resolved.section}
          />
        </TabsContent>
        {!readOnly && (
          <TabsContent value="bulk">
            <EnrollmentBulkPage />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
