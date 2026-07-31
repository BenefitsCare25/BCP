import { useEffect, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { HrAdminSettings } from "@/components/settings/HrAdminSettings";
import { SchemaEntityAliasesPage } from "@/routes/schema/entity-aliases";

// Standing, company-scoped configuration: entity-matching aliases (moved out
// of the firm-wide Schema page, where they were the only company-scoped tab)
// and HR authentication.
//
// Claims configuration is deliberately NOT here any more — the grace period,
// document vocabulary and the per-claim-type AI review rules all live on the
// Claims page (/operations/claims, Settings + AI extraction tabs) beside the
// review queue they govern; `?tab=claims` redirects there. The leave policy
// likewise reads as part of the enrollment workflow, so `?tab=enrollment`
// redirects to /enrollment?tab=leave rather than falling back silently.
const SETTINGS_TABS = ["aliases", "hr"] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];
const isTab = (v: string | undefined): v is SettingsTab =>
  SETTINGS_TABS.includes(v as SettingsTab);

export function CompanySettingsPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  // Old deep links follow their content to its new home.
  useEffect(() => {
    if (search.tab === "enrollment") {
      void navigate({ to: "/enrollment", search: { tab: "leave" }, replace: true });
    } else if (search.tab === "claims") {
      void navigate({
        to: "/operations/claims",
        search: { tab: "settings" },
        replace: true,
      });
    }
  }, [search.tab, navigate]);
  const requested: SettingsTab = isTab(search.tab) ? search.tab : "aliases";
  // The Authentication tab is admin-only (firm-admin `/hr-admin` endpoints);
  // fall back to aliases if a viewer deep-links it.
  const tab: SettingsTab =
    requested === "hr" && !canAdmin ? "aliases" : requested;
  const [aliasAddOpen, setAliasAddOpen] = useState(false);

  return (
    <div className="space-y-5">
      <Tabs
        value={tab}
        onValueChange={(v) =>
          navigate({ to: "/configuration/settings", search: { tab: v } })
        }
      >
        <TabsList>
          <TabsTrigger value="aliases">Entity aliases</TabsTrigger>
          {canAdmin && <TabsTrigger value="hr">Authentication</TabsTrigger>}
        </TabsList>

        <TabsContent value="aliases">
          <div className="mb-3 flex justify-end">
            <Button onClick={() => setAliasAddOpen(true)}>
              <Plus className="size-4" /> Add alias
            </Button>
          </div>
          <SchemaEntityAliasesPage
            open={aliasAddOpen}
            onOpenChange={setAliasAddOpen}
          />
        </TabsContent>

        {canAdmin && (
          <TabsContent value="hr">
            <HrAdminSettings />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
