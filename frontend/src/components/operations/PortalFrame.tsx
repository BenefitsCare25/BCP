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
  UserPlus,
} from "lucide-react";
import {
  usePortalPreviewContext,
  usePreviewCards,
  usePreviewClaims,
  usePreviewClinics,
  usePreviewDependants,
  usePreviewEnrollment,
  usePreviewMessages,
  usePreviewStatement,
  usePreviewUtilization,
} from "@/api/portalPreview";
import { useBrokerCardArtwork } from "@/api/panelCards";
import type { ClinicSearchParams } from "@/api/panelListings";
import { ClinicLocator } from "@/components/portal/ClinicLocator";
import { CardLeaf } from "@/components/portal/leaf/CardLeaf";
import { CoverageLeaf } from "@/components/portal/leaf/CoverageLeaf";
import { UsageLeaf } from "@/components/portal/leaf/UsageLeaf";
import { DependantsLeaf } from "@/components/portal/leaf/DependantsLeaf";
import type { DependantRef } from "@/components/enrollment/electionCore";
import { ClaimList } from "@/components/portal/leaf/ClaimMount";
import {
  HomeMosaicView,
  type HomeDest,
} from "@/components/portal/HomeMosaic";
import { Mount } from "@/components/portal/leaf/Mount";
import { MessageRows } from "@/components/portal/leaf/MessageMount";
import { Strike } from "@/components/portal/leaf/Strike";
import { Action } from "@/components/portal/leaf/Action";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import {
  LeafTabsList,
  LeafTabsTrigger,
} from "@/components/portal/leaf/TabStrip";
import { dependantName, dependantRelationship } from "@/lib/dependant";
import { MemberEnrollmentPanel } from "@/components/portal/MemberEnrollmentPanel";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { BenefitYearControl } from "@/components/portal/BenefitYearControl";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import { LeafScopeContext } from "@/lib/leaf-scope";
import { isNotFoundError } from "@/lib/errors";

// Mirrors the live shell nav in components/portal/PortalShell — change both
// together. Labels are the shell's short set, because the live bar is one row.
const TABS = [
  { key: "home", label: "Home" },
  { key: "coverage", label: "Coverage" },
  { key: "claims", label: "Claims" },
  { key: "card", label: "Card" },
  { key: "clinics", label: "Clinics" },
  { key: "enrollment", label: "Enrolment" },
  // Messages is NOT in the live shell's nav — the member reaches it from the
  // home tile (see router.tsx). It gets a tab here because this frame has only
  // tabs to navigate with, and without one the home tile's "See all messages"
  // would be the one tile whose link goes nowhere.
  { key: "messages", label: "Messages" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

// Mirrors the sub-tabs of routes/portal/coverage.
const COVERAGE_TABS = [
  { key: "benefits", label: "What's covered" },
  { key: "usage", label: "What's left" },
  { key: "dependants", label: "My family" },
] as const;

type CoverageTabKey = (typeof COVERAGE_TABS)[number]["key"];

const ACCOUNT_BADGE = {
  invited: { variant: "warn" as const, label: "Portal: invited" },
  active: { variant: "good" as const, label: "Portal: active" },
  disabled: { variant: "error" as const, label: "Portal: disabled" },
};

/** Made of the member's material — glass on the ground — because everything
 * inside the frame is meant to be what the member sees. A broker-styled card
 * here read as our chrome reporting a fault, which is exactly the confusion the
 * preview exists to avoid. */
function NoCoverageCard() {
  return (
    <Mount>
      <div className="text-center">
        <FileWarning className="mx-auto size-6 text-label" aria-hidden />
        <p className="mt-2 text-md font-semibold text-record">
          No active coverage found
        </p>
        <p className="mt-1 text-row text-label">
          This is what the employee sees when their record isn't on the current
          roster or the policy year isn't active yet.
        </p>
      </div>
    </Mount>
  );
}

function BenefitsTab({ employeeId }: { employeeId: string }) {
  const statement = usePreviewStatement(employeeId);
  // The member's own loading state, not the broker's grey blocks — the frame
  // shows what the member sees at every moment, including this one.
  if (statement.isLoading) {
    return <LeafSkeleton label="Loading benefits" />;
  }
  // Only a 404 is the member's "no coverage" experience — other failures are
  // broker-side fetch errors and get a retryable error state.
  if (statement.isError && !isNotFoundError(statement.error)) {
    return <PortalErrorState onRetry={() => void statement.refetch()} />;
  }
  if (statement.isError || !statement.data) return <NoCoverageCard />;
  return <CoverageLeaf data={statement.data} />;
}

/** Mirrors routes/portal/claims: the member's own ledger, their filter strip,
 * their empty-state wording — a broker reading this needs to see the screen the
 * employee sees, not a broker summary of it.
 *
 * The one deliberate difference is the action. On the member's page it FLOATS
 * (fixed, bottom-centre); a fixed element inside this bounded frame would
 * escape it and hang over the broker's own app, so here it stays in the flow
 * above the ledger — disabled, because members submit from their own sign-in. */
function ClaimsTab({ employeeId }: { employeeId: string }) {
  const claims = usePreviewClaims(employeeId);
  if (claims.isLoading) return <LeafSkeleton label="Loading claims" />;
  if (claims.isError && !isNotFoundError(claims.error)) {
    return <PortalErrorState onRetry={() => void claims.refetch()} />;
  }
  const rows = claims.data?.items ?? [];
  const disabledTitle =
    "Disabled in preview — members submit claims from their own sign-in";
  if (rows.length === 0) {
    return (
      <Mount label="No claims yet">
        <p className="text-row text-label">
          When you pay for treatment that your benefits cover, send us the
          receipt here and we&rsquo;ll tell you where it&rsquo;s up to.
        </p>
        <div>
          <Action tone="primary" block="phone" disabled title={disabledTitle}>
            <FilePlus2 className="size-4" aria-hidden />
            Make a claim
          </Action>
        </div>
      </Mount>
    );
  }
  return (
    <div className="space-y-3">
      <Action tone="primary" block="phone" disabled title={disabledTitle}>
        <FilePlus2 className="size-4" aria-hidden />
        Make a claim
      </Action>
      <ClaimList items={rows} total={claims.data?.total} />
    </div>
  );
}

/** Mirrors routes/portal/utilization. The member's page carries no heading of
 * its own — the running head is the only h1 and the tab already names the
 * question — so neither does this. */
function UtilizationTab({ employeeId }: { employeeId: string }) {
  const { data, isLoading, isError, error, refetch } =
    usePreviewUtilization(employeeId);
  // Mirrors the member page — the preview itemises what is under review the
  // same way, from the same member-gated endpoint.
  const claims = usePreviewClaims(employeeId);
  if (isLoading) return <LeafSkeleton label="Loading balances" mounts={2} />;
  if (isError && !isNotFoundError(error)) {
    return <PortalErrorState onRetry={() => void refetch()} />;
  }
  if (isError || !data) return <NoCoverageCard />;
  return <UsageLeaf data={data} claims={claims.data?.items} />;
}

/** Mirrors routes/portal/dependants: the same quiet action in the same place,
 * disabled, and the member's own empty state (`DependantsLeaf` with no rows)
 * rather than a broker-worded card — a broker looking at this needs to read the
 * sentence the employee would read. */
function DependantsTab({ employeeId }: { employeeId: string }) {
  const dependants = usePreviewDependants(employeeId);
  if (dependants.isLoading) return <LeafSkeleton label="Loading family" mounts={2} />;
  if (dependants.isError && !isNotFoundError(dependants.error)) {
    return <PortalErrorState onRetry={() => void dependants.refetch()} />;
  }
  const rows = dependants.data ?? [];
  return (
    <div className="space-y-3">
      <div className="flex sm:justify-end">
        <Action
          block="phone"
          disabled
          title="Disabled in preview — members add family from their own sign-in"
        >
          <UserPlus className="size-4" aria-hidden />
          Add a family member
        </Action>
      </div>
      <DependantsLeaf rows={rows} />
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
    <CardLeaf
      cards={cards.data?.items ?? []}
      useArtwork={useBrokerCardArtwork}
      // The broker is being told a configuration fact, not given a member's
      // next step — and the member's clinic route isn't navigable from here.
      emptyMessage="No panel card is assigned to this employee's plan for the current benefit year, so nothing appears on their card screen."
      emptyAction={false}
    />
  );
}

function ClinicsTab({ employeeId }: { employeeId: string }) {
  // key={employeeId} remounts the locator (fresh filters/origin) per employee,
  // so the injected hook needs no memoization — it always calls the same
  // hooks in the same order.
  const useClinics = (params: ClinicSearchParams) =>
    usePreviewClinics(employeeId, params);
  return <ClinicLocator key={employeeId} useClinicsQuery={useClinics} />;
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

/** Mirrors the member's home (routes/portal/home + HomeMosaic).
 *
 * The tiles navigate by switching THIS frame's tabs — `onGo` — because a real
 * `<Link>` here would walk the broker out of their own application and into the
 * member portal. Same component, same tiles, same rules about what may be
 * rendered; only the onward step differs. */
function HomeTab({
  employeeId,
  enrollmentOpen,
  onGo,
}: {
  employeeId: string;
  enrollmentOpen: boolean;
  onGo: (dest: HomeDest) => void;
}) {
  const utilization = usePreviewUtilization(employeeId);
  const claims = usePreviewClaims(employeeId);
  const statement = usePreviewStatement(employeeId);
  const dependants = usePreviewDependants(employeeId);
  const messages = usePreviewMessages(employeeId);
  return (
    <HomeMosaicView
      source={{
        enrollmentOpen,
        utilization,
        claims,
        statement,
        dependants,
        messages,
        // **Every preview tab must pass a retry.** These queries carry
        // `localErrorHandling` + `retry: false`, so a transient 500 neither
        // retries itself nor reaches the notification bell — without this the
        // Home tab rendered "We couldn't load this just now" with no way out of
        // it but a full page reload. The member's own Home has always passed
        // one; this tab was the only mirror that did not.
        onRetry: () => {
          void utilization.refetch();
          void claims.refetch();
          void statement.refetch();
          void dependants.refetch();
          void messages.refetch();
        },
      }}
      onGo={onGo}
    />
  );
}

/** Mirrors routes/portal/messages. Rows are INERT here: the member's inbox
 * navigates to the claim, and this frame's Claims tab is a list, not a detail
 * — a row that jumped to a tab showing something else would be worse than one
 * that does nothing. */
function MessagesTab({ employeeId }: { employeeId: string }) {
  const messages = usePreviewMessages(employeeId);
  if (messages.isLoading) return <LeafSkeleton label="Loading messages" />;
  if (messages.isError && !isNotFoundError(messages.error)) {
    return <PortalErrorState onRetry={() => void messages.refetch()} />;
  }
  const items = messages.data?.items ?? [];
  const unread = messages.data?.unread ?? 0;
  if (items.length === 0) {
    return (
      <Mount label="No messages yet">
        <p className="text-row text-label">
          When we have news about a claim &mdash; that we&rsquo;ve received it,
          that it&rsquo;s settled, or that we need something else &mdash; it
          will appear here.
        </p>
      </Mount>
    );
  }
  return (
    <Mount
      label={`${messages.data?.total ?? items.length} message${
        (messages.data?.total ?? items.length) === 1 ? "" : "s"
      }`}
      aside={unread > 0 ? <Strike tone="pending">{unread} unread</Strike> : undefined}
    >
      <MessageRows items={items} className="-mt-1" />
    </Mount>
  );
}

/** Mirrors the "My coverage" sub-tab page (routes/portal/coverage). The sub-tab
 * is lifted so a home tile can land the broker on the right reading of it. */
function CoverageTab({
  employeeId,
  tab,
  setTab,
}: {
  employeeId: string;
  tab: CoverageTabKey;
  setTab: (tab: CoverageTabKey) => void;
}) {
  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as CoverageTabKey)}>
      {/* The member's strip, from the same component — see leaf/TabStrip. */}
      <LeafTabsList label="Coverage">
        {COVERAGE_TABS.map((t) => (
          <LeafTabsTrigger key={t.key} value={t.key}>
            {t.label}
          </LeafTabsTrigger>
        ))}
      </LeafTabsList>
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
  const [tab, setTab] = useState<TabKey>("home");
  const [coverageTab, setCoverageTab] = useState<CoverageTabKey>("benefits");

  // Re-selecting a different employee restarts the walkthrough at the home
  // screen — the same place the member lands when they sign in.
  useEffect(() => {
    setTab("home");
    setCoverageTab("benefits");
  }, [employeeId]);

  /** A home tile's destination, mapped onto this frame's tabs. */
  const goFromHome = (dest: HomeDest) => {
    if (dest === "usage" || dest === "benefits" || dest === "dependants") {
      setCoverageTab(dest);
      setTab("coverage");
      return;
    }
    setTab(dest);
  };

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

      {/* `leaf` puts the preview inside the member's visual world, not just its
          content. Without it a broker reads the member's screens in broker red
          and broker type and comes away with the wrong idea of what the member
          is looking at — which defeats the point of a preview. The class only
          re-points tokens for this subtree; the surrounding broker chrome above
          is unaffected. */}
      <LeafScopeContext.Provider value>
      {/* `overflow-clip`, NOT `overflow-hidden`. Hidden makes this a scroll
          container, which becomes the containing block for any `position:
          sticky` inside it — and since this box never scrolls, sticky silently
          stops working. The coverage deck's rail is sticky, so in the preview it
          scrolled away with the content while the member's own rail stayed put:
          a divergence in the one component whose whole job is not to diverge.
          `clip` clips identically, radius included, without the side effect. */}
      <div className="leaf overflow-clip rounded-xl border border-border shadow-sm">
        {/* One row, mirroring the live shell: mark, hairline, pill nav, then the
            benefit-year scope control and the account action. No primary action
            in the bar — see PortalShell. */}
        <header className="border-b border-hairline bg-bar">
          <div className="flex flex-wrap items-center gap-y-2 px-5 py-3">
            <img
              src="/inspro-logo-header.png"
              alt="Inspro Insurance Brokers"
              width={140}
              height={45}
              className="h-11 w-auto shrink-0"
            />
            <span aria-hidden className="mx-4 h-7 w-px shrink-0 bg-hairline" />
            {/* NO dot on the Enrolment tab, and the comment that used to sit
                here claiming it "mirrors the live shell" was simply wrong: the
                member's desktop nav has never carried one. Only the phone dock
                does, where it is paired with an `sr-only` gloss and is the sole
                signal on a viewport that has no room for the enrolment tile.
                A bare coloured dot in a nav rail is an unglossed term with
                nowhere to put its gloss (DESIGN.md), and the deadline it stood
                for is stated in words at the top of the enrolment page and on
                the home tile. Adding it here made the preview show a broker
                something the member never sees — the one thing this frame
                exists not to do. */}
            <nav className="flex items-center gap-0.5">
              {TABS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
                  className={cn(
                    "leaf-focus inline-flex h-10 items-center gap-1.5 rounded-pill px-4 text-row",
                    "transition-colors duration-200 ease-leaf",
                    tab === item.key
                      ? "bg-shade font-semibold text-record"
                      : "text-label hover:bg-shade hover:text-record",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </nav>
            <div className="ml-auto flex shrink-0 items-center gap-3 pl-4">
              <Button
                variant="ghost"
                size="sm"
                disabled
                title="Disabled in preview"
              >
                <LogOut className="size-4" />
                <span className="ml-1">Sign out</span>
              </Button>
            </div>
          </div>
        </header>
        {/* `leaf-ground` carries the colour blooms the glass frosts. Without it
            the tiles read as paler paint — the same trap documented in
            leaf.css. */}
        <main className="leaf-ground bg-ground px-4 py-6">
          <div className="mx-auto max-w-4xl">
            <div className="mb-4 flex items-center justify-between gap-4">
              <h2 className="min-w-0 truncate text-2xl font-bold tracking-title text-record">
                {memberLabel}
              </h2>
              {ctx?.policy_year && (
                <BenefitYearControl
                  start={ctx.policy_year.start_date}
                  end={ctx.policy_year.end_date}
                  className="hidden sm:inline-flex"
                />
              )}
            </div>
            {tab === "home" && (
              <HomeTab
                employeeId={employeeId}
                enrollmentOpen={Boolean(ctx?.enrollment_open)}
                onGo={goFromHome}
              />
            )}
            {tab === "coverage" && (
              <CoverageTab
                employeeId={employeeId}
                tab={coverageTab}
                setTab={setCoverageTab}
              />
            )}
            {tab === "claims" && <ClaimsTab employeeId={employeeId} />}
            {tab === "messages" && <MessagesTab employeeId={employeeId} />}
            {tab === "card" && <CardTab employeeId={employeeId} />}
            {tab === "clinics" && <ClinicsTab employeeId={employeeId} />}
            {tab === "enrollment" && <EnrollmentTab employeeId={employeeId} />}
          </div>
        </main>
      </div>
      </LeafScopeContext.Provider>
    </div>
  );
}
