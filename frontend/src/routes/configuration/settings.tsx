import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import { useMe, usePolicyYears, useUpdatePolicyYear } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InfoHint } from "@/components/ui/tooltip";
import { DocTypeSettings } from "@/components/claims/DocTypeSettings";
import { CompaniesSettingsCard } from "@/components/configuration/CompaniesSettingsCard";
import { HrAdminSettings } from "@/components/settings/HrAdminSettings";
import { LeavePolicyCard } from "@/components/enrollment/LeavePolicyCard";
import { SchemaEntityAliasesPage } from "@/routes/schema/entity-aliases";
import { formatError } from "@/lib/errors";

// Standing, company-scoped configuration lives here so operational pages stay
// focused on their workflow: claim behaviour + document vocabulary (moved off
// the Claims review queue), the leave policy (moved off the enrollment window
// form), and entity-matching aliases (moved out of the firm-wide Schema page,
// where they were the only company-scoped tab).
const SETTINGS_TABS = [
  "claims",
  "enrollment",
  "aliases",
  "hr",
  "companies",
] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];
const isTab = (v: string | undefined): v is SettingsTab =>
  SETTINGS_TABS.includes(v as SettingsTab);

// Claim-submission grace period, bound to the current benefit year — the year
// claims submit against. Edit buffer is committed on blur; blank clears the
// deadline. (Number(), not parseInt, so "30.5"/"30x" are rejected rather than
// truncated; the draft survives a validation error so the value isn't lost.)
function ClaimGracePeriodField() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const update = useUpdatePolicyYear();
  const year = years.find((y) => y.id === policyYearId) ?? null;
  const [draft, setDraft] = useState<string | null>(null);

  if (!year) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to set a claim submission deadline.
      </p>
    );
  }

  const commit = async () => {
    if (draft === null) return;
    const trimmed = draft.trim();
    const next = trimmed === "" ? null : Number(trimmed);
    if (next !== null && (!Number.isInteger(next) || next < 0)) {
      toast.error("Grace period must be a whole number of days (or blank).");
      return;
    }
    if (next === year.claim_grace_period_days) {
      setDraft(null);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: year.id,
        payload: { claim_grace_period_days: next },
      });
      toast.success("Claim grace period updated");
      setDraft(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <div className="flex flex-col gap-1.5 sm:max-w-md">
      <div className="flex items-center gap-1">
        <Label htmlFor="claim-grace">Claim submission grace period (days)</Label>
        <InfoHint>
          Days after the current benefit year's coverage period ends during
          which members may still submit claims. Leave blank for no submission
          deadline.
        </InfoHint>
      </div>
      <Input
        id="claim-grace"
        type="number"
        min={0}
        placeholder="No deadline"
        className="h-9 w-40"
        value={draft ?? (year.claim_grace_period_days?.toString() ?? "")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    </div>
  );
}

export function CompanySettingsPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const requested: SettingsTab = isTab(search.tab) ? search.tab : "claims";
  // The Companies + HR tabs are admin-only (firm-admin `/hr-admin` endpoints);
  // fall back to Claims if a viewer deep-links one.
  const adminOnly = requested === "companies" || requested === "hr";
  const tab: SettingsTab = adminOnly && !canAdmin ? "claims" : requested;
  const policyYearId = useSession((s) => s.currentPolicyYearId);
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
          <TabsTrigger value="claims">Claims</TabsTrigger>
          <TabsTrigger value="enrollment">Enrollment</TabsTrigger>
          <TabsTrigger value="aliases">Entity aliases</TabsTrigger>
          {canAdmin && <TabsTrigger value="hr">HR access</TabsTrigger>}
          {canAdmin && <TabsTrigger value="companies">Companies</TabsTrigger>}
        </TabsList>

        <TabsContent value="claims" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Claim submission</CardTitle>
              <CardDescription>
                Governs when members may submit claims for the current benefit
                year.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ClaimGracePeriodField />
            </CardContent>
          </Card>
          <DocTypeSettings />
        </TabsContent>

        <TabsContent value="enrollment">
          {policyYearId ? (
            <LeavePolicyCard key={policyYearId} policyYearId={policyYearId} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Select a benefit year to configure the leave policy.
            </p>
          )}
        </TabsContent>

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

        {canAdmin && (
          <TabsContent value="companies">
            <CompaniesSettingsCard />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
