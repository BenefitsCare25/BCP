/** Employee view — a read-only, pixel-faithful preview of what an employee
 * sees when they sign in to the member portal. Data comes from the broker
 * `/employees/{id}/portal-preview/*` endpoints (member-gated statements —
 * financials stripped server-side); member actions are shown disabled.
 * Any nav/tab change in components/portal/PortalShell must be mirrored here. */
import { useEffect, useMemo, useState } from "react";
import {
  Eye,
  FilePlus2,
  FileWarning,
  Loader2,
  LogOut,
  ReceiptText,
  ShieldCheck,
  UserPlus,
  Users,
} from "lucide-react";
import {
  usePortalPreviewContext,
  usePreviewCards,
  usePreviewClaims,
  usePreviewClinics,
  usePreviewDependants,
  usePreviewEnrollment,
  usePreviewStatement,
  usePreviewUtilization,
} from "@/api/portalPreview";
import { useBrokerCardArtwork } from "@/api/panelCards";
import type { ClinicSearchParams } from "@/api/panelListings";
import { ClinicLocator } from "@/components/portal/ClinicLocator";
import { MemberCardList } from "@/components/portal/MemberCard";
import { BenefitStatement } from "@/components/benefits/BenefitStatement";
import { UtilizationView } from "@/components/benefits/UtilizationView";
import type { DependantRef } from "@/components/enrollment/electionShared";
import { ClaimCards } from "@/components/portal/ClaimCards";
import {
  DependantsTable,
  dependantName,
  dependantRelationship,
} from "@/components/portal/DependantsTable";
import { MemberEnrollmentPanel } from "@/components/portal/MemberEnrollmentPanel";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import { isNotFoundError } from "@/lib/errors";
import { formatPolicyRange } from "@/lib/policy-year";

// Mirrors the live shell nav in components/portal/PortalShell — change both
// together.
const TABS = [
  { key: "coverage", label: "My coverage" },
  { key: "claims", label: "My claims" },
  { key: "card", label: "My card" },
  { key: "clinics", label: "Find a clinic" },
  { key: "enrollment", label: "My enrollment" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

// Mirrors the sub-tabs of routes/portal/coverage.
const COVERAGE_TABS = [
  { key: "benefits", label: "Benefits" },
  { key: "usage", label: "Usage" },
  { key: "dependants", label: "Dependants" },
] as const;

type CoverageTabKey = (typeof COVERAGE_TABS)[number]["key"];

const ACCOUNT_BADGE = {
  invited: { variant: "warn" as const, label: "Portal: invited" },
  active: { variant: "good" as const, label: "Portal: active" },
  disabled: { variant: "error" as const, label: "Portal: disabled" },
};

function NoCoverageCard() {
  return (
    <div className="rounded-lg border border-border bg-card p-8 text-center">
      <FileWarning className="mx-auto size-6 text-muted-foreground" />
      <p className="mt-2 text-sm font-medium text-foreground">
        No active coverage found
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        This is what the employee sees when their record isn't on the current
        roster or the policy year isn't active yet.
      </p>
    </div>
  );
}

function BenefitsTab({ employeeId }: { employeeId: string }) {
  const statement = usePreviewStatement(employeeId);
  // Mirrors the member surface — the preview must show the same remaining
  // balances the member sees. Never gates rendering.
  const utilization = usePreviewUtilization(employeeId);
  if (statement.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  // Only a 404 is the member's "no coverage" experience — other failures are
  // broker-side fetch errors and get a retryable error state.
  if (statement.isError && !isNotFoundError(statement.error)) {
    return <PortalErrorState onRetry={() => void statement.refetch()} />;
  }
  if (statement.isError || !statement.data) return <NoCoverageCard />;
  return <BenefitStatement data={statement.data} utilization={utilization.data} />;
}

function ClaimsTab({ employeeId }: { employeeId: string }) {
  const claims = usePreviewClaims(employeeId);
  if (claims.isLoading) return <Skeleton className="h-48 w-full" />;
  if (claims.isError && !isNotFoundError(claims.error)) {
    return <PortalErrorState onRetry={() => void claims.refetch()} />;
  }
  const rows = claims.data?.items ?? [];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">
          {rows.length > 0
            ? `${claims.data?.total ?? rows.length} claim${rows.length === 1 ? "" : "s"} this policy year`
            : "My claims"}
        </h2>
        <Button size="sm" disabled title="Disabled in preview — members submit claims from their own sign-in">
          <FilePlus2 className="size-4" />
          <span className="ml-1">Submit a claim</span>
        </Button>
      </div>
      {rows.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <ReceiptText className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium text-foreground">No claims yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            The employee hasn't submitted any claims this policy year.
          </p>
        </div>
      ) : (
        <ClaimCards items={rows} />
      )}
    </div>
  );
}

function UtilizationTab({ employeeId }: { employeeId: string }) {
  const { data, isLoading, isError, error, refetch } =
    usePreviewUtilization(employeeId);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">My usage</h1>
        <p className="text-sm text-muted-foreground">
          How much of each benefit you've used this policy year.
        </p>
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading usage…
        </div>
      ) : isError && !isNotFoundError(error) ? (
        <PortalErrorState onRetry={() => void refetch()} />
      ) : isError || !data ? (
        <NoCoverageCard />
      ) : (
        <UtilizationView data={data} />
      )}
    </div>
  );
}

function DependantsTab({ employeeId }: { employeeId: string }) {
  const dependants = usePreviewDependants(employeeId);
  if (dependants.isLoading) return <Skeleton className="h-48 w-full" />;
  if (dependants.isError && !isNotFoundError(dependants.error)) {
    return <PortalErrorState onRetry={() => void dependants.refetch()} />;
  }
  const rows = dependants.data ?? [];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">My dependants</h2>
        <Button size="sm" disabled title="Disabled in preview — members add dependants from their own sign-in">
          <UserPlus className="size-4" />
          <span className="ml-1">Add dependant</span>
        </Button>
      </div>
      {rows.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <Users className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium text-foreground">
            No dependants on record
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Dependants the employee adds appear here pending broker approval.
          </p>
        </div>
      ) : (
        <DependantsTable rows={rows} />
      )}
    </div>
  );
}

function CardTab({ employeeId }: { employeeId: string }) {
  const cards = usePreviewCards(employeeId);
  if (cards.isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading cards…
      </div>
    );
  }
  if (cards.error) {
    return isNotFoundError(cards.error) ? <NoCoverageCard /> : <PortalErrorState onRetry={() => void cards.refetch()} />;
  }
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">My card</h1>
        <p className="text-sm text-muted-foreground">
          Show this at a panel clinic. One card per plan you're covered under,
          plus a card for each covered family member.
        </p>
      </div>
      <MemberCardList
        cards={cards.data?.items ?? []}
        useArtwork={useBrokerCardArtwork}
        emptyMessage="No e-cards have been issued for this employee's plan yet."
      />
    </div>
  );
}

function ClinicsTab({ employeeId }: { employeeId: string }) {
  // key={employeeId} remounts the locator (fresh filters/origin) per employee,
  // so the injected hook needs no memoization — it always calls the same
  // hooks in the same order.
  const useClinics = (params: ClinicSearchParams) =>
    usePreviewClinics(employeeId, params);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Find a clinic</h1>
        <p className="text-sm text-muted-foreground">
          Panel clinics covered under your policy — use your device location or
          enter a postal code to see the 10 nearest first.
        </p>
      </div>
      <ClinicLocator key={employeeId} useClinicsQuery={useClinics} />
    </div>
  );
}

function EnrollmentTab({ employeeId }: { employeeId: string }) {
  const enrollment = usePreviewEnrollment(employeeId);
  const dependants = usePreviewDependants(employeeId);
  const dependantRefs = useMemo<DependantRef[]>(
    () =>
      (dependants.data ?? [])
        .filter((d) => d.status === "active")
        .map((d) => ({
          id: d.id,
          name: dependantName(d),
          relationship: dependantRelationship(d),
        })),
    [dependants.data],
  );
  if (enrollment.isLoading) return <Skeleton className="h-48 w-full" />;
  if (enrollment.isError && !isNotFoundError(enrollment.error)) {
    return <PortalErrorState onRetry={() => void enrollment.refetch()} />;
  }
  if (enrollment.isError) return <NoCoverageCard />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">My enrollment</h1>
        <p className="text-sm text-muted-foreground">
          Review your plans and make changes while the enrollment window is
          open.
        </p>
      </div>
      <MemberEnrollmentPanel
        data={
          enrollment.data ?? { window: null, enrollment: null, options: null }
        }
        dependants={dependantRefs}
        readOnly
      />
    </div>
  );
}

/** Mirrors the "My coverage" sub-tab page (routes/portal/coverage). */
function CoverageTab({ employeeId }: { employeeId: string }) {
  const [tab, setTab] = useState<CoverageTabKey>("benefits");
  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as CoverageTabKey)}>
      <TabsList>
        {COVERAGE_TABS.map((t) => (
          <TabsTrigger key={t.key} value={t.key}>
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value="benefits">
        <BenefitsTab employeeId={employeeId} />
      </TabsContent>
      <TabsContent value="usage">
        <UtilizationTab employeeId={employeeId} />
      </TabsContent>
      <TabsContent value="dependants">
        <DependantsTab employeeId={employeeId} />
      </TabsContent>
    </Tabs>
  );
}

/** The portal replica — chrome copied from PortalShell so the preview stays
 * visually faithful, with tabs instead of routes and actions disabled. */
export function PortalFrame({ employeeId }: { employeeId: string }) {
  const { data: ctx } = usePortalPreviewContext(employeeId);
  const [tab, setTab] = useState<TabKey>("coverage");

  // Re-selecting a different employee restarts the walkthrough on coverage.
  useEffect(() => setTab("coverage"), [employeeId]);

  const memberLabel =
    ctx?.member_account?.display_name ||
    ctx?.member_account?.email ||
    ctx?.employee.employee_name ||
    ctx?.employee.staff_id;

  return (
    <div className="space-y-3">
      {/* Preview banner — everything below it is the member's view. */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 px-3 py-2">
        <Eye className="size-4 shrink-0 text-warn" />
        <p className="min-w-0 flex-1 text-xs text-foreground">
          <span className="font-semibold">Employee view</span> — read-only
          preview of the portal as{" "}
          <span className="font-medium">
            {ctx?.employee.employee_name ?? "this employee"}
          </span>{" "}
          sees it. Member actions are disabled.
        </p>
        {ctx &&
          (ctx.member_account ? (
            <Badge variant={ACCOUNT_BADGE[ctx.member_account.status].variant}>
              {ACCOUNT_BADGE[ctx.member_account.status].label}
            </Badge>
          ) : (
            <Badge variant="outline">No portal account</Badge>
          ))}
        {ctx && !ctx.is_active_policy_year && (
          <Badge variant="warn">Not the active policy year</Badge>
        )}
        {ctx?.enrollment_open && <Badge variant="warn">Enrollment open</Badge>}
      </div>

      <div className="overflow-hidden rounded-xl border border-border shadow-sm">
        <header className="border-b border-border bg-card">
          <div className="flex h-14 items-center justify-between px-4">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-5 text-primary" />
              <span className="text-sm font-semibold text-foreground">
                My Benefits Portal
              </span>
              {ctx?.policy_year && (
                <span className="ml-2 hidden text-xs text-muted-foreground sm:inline">
                  {formatPolicyRange(
                    ctx.policy_year.start_date,
                    ctx.policy_year.end_date,
                  )}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden text-xs text-muted-foreground sm:inline">
                {memberLabel}
              </span>
              <Button variant="ghost" size="sm" disabled title="Disabled in preview">
                <LogOut className="size-4" />
                <span className="ml-1">Sign out</span>
              </Button>
            </div>
          </div>
          <nav className="flex gap-1 px-4 pb-2">
            {TABS.map((item) => {
              // Mirror the live shell: the enrollment tab gets a dot while a
              // window is open.
              const highlight =
                item.key === "enrollment" &&
                ctx?.enrollment_open &&
                tab !== item.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                    tab === item.key
                      ? "bg-sidebar-active text-sidebar-active-foreground font-medium"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {item.label}
                  {highlight && (
                    <span
                      className="size-1.5 rounded-full bg-warn"
                      title="Enrollment window open"
                    />
                  )}
                </button>
              );
            })}
          </nav>
        </header>
        <main className="bg-background px-4 py-6">
          <div className="mx-auto max-w-4xl">
            {tab === "coverage" && <CoverageTab employeeId={employeeId} />}
            {tab === "claims" && <ClaimsTab employeeId={employeeId} />}
            {tab === "card" && <CardTab employeeId={employeeId} />}
            {tab === "clinics" && <ClinicsTab employeeId={employeeId} />}
            {tab === "enrollment" && <EnrollmentTab employeeId={employeeId} />}
          </div>
        </main>
      </div>
    </div>
  );
}
