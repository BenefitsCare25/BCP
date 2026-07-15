import { useNavigate, useSearch } from "@tanstack/react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PortalBenefitsPage } from "./benefits";
import { PortalUtilizationPage } from "./utilization";
import { PortalDependantsPage } from "./dependants";

const TABS = [
  { key: "benefits", label: "Benefits" },
  { key: "usage", label: "Usage" },
  { key: "dependants", label: "Dependants" },
] as const;

type CoverageTab = (typeof TABS)[number]["key"];

/** "My coverage" — benefits, usage and dependants are all read-centric views
 * of the member's current coverage, so they live as sub-tabs of one page.
 * The broker's employee-view preview (components/operations/PortalFrame)
 * mirrors this structure — keep the two in sync. */
export function PortalCoveragePage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: CoverageTab =
    search.tab === "usage" || search.tab === "dependants"
      ? search.tab
      : "benefits";

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/portal/coverage", search: { tab: value } })
      }
    >
      <TabsList>
        {TABS.map((t) => (
          <TabsTrigger key={t.key} value={t.key}>
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value="benefits">
        <PortalBenefitsPage />
      </TabsContent>
      <TabsContent value="usage">
        <PortalUtilizationPage />
      </TabsContent>
      <TabsContent value="dependants">
        <PortalDependantsPage />
      </TabsContent>
    </Tabs>
  );
}
