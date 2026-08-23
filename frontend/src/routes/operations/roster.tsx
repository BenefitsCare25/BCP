import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import {
  PageTabsBar,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { usePendingDependants } from "@/components/operations/DependantApprovals";
import { useSession } from "@/stores/session";
import { RosterActionsSlot } from "./rosterTabActions";
import { EmployeesPage } from "./employees";
import { DependantsPage } from "./dependants";

const TABS = [
  { key: "employees", label: "Employees" },
  { key: "dependants", label: "Dependants" },
] as const;

type RosterTab = (typeof TABS)[number]["key"];

// Employees + dependants are two views of the same roster (same upload →
// table → detail-drawer shape), so they live as tabs of one page. The active
// tab rides ?tab= so both stay deep-linkable.
export function RosterPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: RosterTab = search.tab === "dependants" ? "dependants" : "employees";
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  // Same query the DependantApprovals card uses (shared cache entry) — badges
  // the tab so pending self-added dependants are visible from anywhere.
  const pending = usePendingDependants(policyYearId ?? "");
  const pendingCount = policyYearId ? (pending.data?.total ?? 0) : 0;
  // Slot on the tab row that each active tab portals its action buttons into.
  const [actionSlot, setActionSlot] = useState<HTMLDivElement | null>(null);

  return (
    <Tabs
      value={tab}
      onValueChange={(value) =>
        navigate({ to: "/policy-admin/member-listing", search: { tab: value } })
      }
    >
      <PageTabsBar className="flex items-center justify-between gap-3 overflow-x-auto">
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.key} value={t.key}>
              {t.label}
              {t.key === "dependants" && pendingCount > 0 && (
                <Badge
                  variant="warn"
                  className="ml-1.5"
                  title={`${pendingCount} dependant${pendingCount === 1 ? "" : "s"} awaiting approval`}
                >
                  {pendingCount}
                </Badge>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
        <div ref={setActionSlot} className="flex shrink-0 items-center gap-2" />
      </PageTabsBar>
      <RosterActionsSlot.Provider value={actionSlot}>
        <TabsContent value="employees">
          <EmployeesPage />
        </TabsContent>
        <TabsContent value="dependants">
          <DependantsPage />
        </TabsContent>
      </RosterActionsSlot.Provider>
    </Tabs>
  );
}
