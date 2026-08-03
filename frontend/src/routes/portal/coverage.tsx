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
import { useCompany } from "@/components/portal/useCompany";

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
  const company = useCompany();
  const search = useSearch({ strict: false }) as { tab?: string; p?: string };
  const tab: CoverageTab =
    search.tab === "usage" || search.tab === "dependants"
      ? search.tab
      : "benefits";

  useDocumentTitle(TABS.find((t) => t.key === tab)?.title);

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        // `p` is deliberately NOT carried across: it names a slide of the
        // coverage deck, which only "What's covered" has. TanStack replaces the
        // whole search object, so dropping it is the default and the right
        // behaviour — a stale product key on the family tab would come back the
        // next time someone returned to this one.
        navigate({
          to: "/portal/$company/coverage",
          params: { company },
          search: { tab: value },
        })
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
        {/* The selected benefit lives in the URL so it survives a refresh, the
            back button and a shared link. `replace` because stepping through
            nine products is reading, not navigating: without it the browser's
            Back button walks back through every product visited instead of
            leaving the page. */}
        <PortalBenefitsPage
          productKey={search.p ?? null}
          onProductKeyChange={(p) =>
            navigate({
              to: "/portal/$company/coverage",
              params: { company },
              search: { tab: "benefits", p },
              replace: true,
            })
          }
        />
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
