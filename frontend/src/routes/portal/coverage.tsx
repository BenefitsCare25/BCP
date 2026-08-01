import { useNavigate, useSearch } from "@tanstack/react-router";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import {
  LeafTabsList,
  LeafTabsTrigger,
} from "@/components/portal/leaf/TabStrip";
import { HeadRail } from "@/components/portal/leaf/HeadRail";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { PortalBenefitsPage } from "./benefits";
import { PortalUtilizationPage } from "./utilization";
import { PortalDependantsPage } from "./dependants";

/** Tab labels are the member's question, not the system's noun. "Usage" named
 * a database concept; "What's left" is what they came to find out. */
const TABS = [
  { key: "benefits", label: "What's covered", title: "What's covered" },
  { key: "usage", label: "What's left", title: "What's left" },
  { key: "dependants", label: "My family", title: "My family" },
] as const;

type CoverageTab = (typeof TABS)[number]["key"];

/** "My coverage" — what's covered, what's left, and who else is on the plan are
 * three readings of one record, so they live as sub-tabs of one page.
 * The broker's employee-view preview (components/operations/PortalFrame)
 * mirrors this structure — keep the two in sync. */
export function PortalCoveragePage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: CoverageTab =
    search.tab === "usage" || search.tab === "dependants"
      ? search.tab
      : "benefits";

  useDocumentTitle(TABS.find((t) => t.key === tab)?.title);

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/portal/coverage", search: { tab: value } })
      }
    >
      {/* At `lg` and up the strip moves into the centre of the shell's heading
          row, between the member's name and the benefit-year control — the row
          was carrying two items and a wide gap, and the strip sat under it
          repeating the same horizontal band. The `lg:` sizing below is the
          same decision as the rail's breakpoint: the rail only exists at `lg`,
          so these are exactly the widths at which the strip is in the row, and
          it tightens there to sit as one control in a header rather than as a
          full-size band. */}
      <HeadRail>
        <LeafTabsList label="Coverage" className="lg:p-1">
          {TABS.map((t) => (
            <LeafTabsTrigger key={t.key} value={t.key} className="lg:px-4">
              {t.label}
            </LeafTabsTrigger>
          ))}
        </LeafTabsList>
      </HeadRail>
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
