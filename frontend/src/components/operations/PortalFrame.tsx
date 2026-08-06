/** Employee view — a read-only, pixel-faithful preview of what an employee
 * sees when they sign in to the member portal. Data comes from the broker
 * `/employees/{id}/portal-preview/*` endpoints (member-gated statements —
 * financials stripped server-side); member actions are shown disabled.
 * Any nav/tab change in components/portal/PortalShell must be mirrored here. */
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Eye,
  FilePlus2,
  FileWarning,
  Loader2,
  LogOut,
  MessageSquare,
  UserPlus,
} from "lucide-react";
import {
  usePortalPreviewContext,
  usePreviewCards,
  usePreviewClaim,
  usePreviewClaimMessages,
  usePreviewClaims,
  usePreviewClinics,
  usePreviewDependants,
  usePreviewEnrollment,
  usePreviewConversations,
  usePreviewEnquiry,
  usePreviewEnquiryMessages,
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
import { ClaimDetailLeaf } from "@/components/portal/leaf/ClaimDetailLeaf";
import { MessageThread } from "@/components/portal/leaf/MessageMount";
import {
  HomeMosaicView,
  type HomeDest,
} from "@/components/portal/HomeMosaic";
import { Mount } from "@/components/portal/leaf/Mount";
import {
  UnreadBadge,
  messagesLabel,
} from "@/components/portal/leaf/MessageMount";
import {
  ConversationRows,
  EnquiryStrike,
  subjectTitle,
} from "@/components/portal/leaf/ConversationMount";
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
] as const;

/** Messages is deliberately absent from TABS: on the live shell it is an ICON
 * in the account cluster, not a nav pill, so a labelled tab here would show a
 * broker a destination the member does not have. It is still a REACHABLE view —
 * the icon below opens it, and so does the home tile's "See all messages" —
 * which is exactly the shape of the member's own shell, where `/portal/messages`
 * is a real route with no entry in `NAV`. */
type TabKey = (typeof TABS)[number]["key"] | "messages";

// Mirrors the sub-tabs of routes/portal/coverage.
const COVERAGE_TABS = [
  { key: "benefits", label: "What's covered" },
  { key: "usage", label: "What's left" },
  { key: "dependants", label: "My family" },
] as const;

type CoverageTabKey = (typeof COVERAGE_TABS)[number]["key"];

/** Mirrors `PortalShell`'s ICON_BUTTON. Spelled out rather than imported for
 * the same reason the nav pills below are — this frame replicates the shell's
 * chrome, and the two are kept in step by the note at the top of both files. */
const ICON_BUTTON =
  "leaf-focus relative inline-flex size-11 shrink-0 items-center justify-center rounded-pill " +
  "text-label transition-colors duration-200 ease-leaf hover:bg-shade hover:text-record";

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

const CLAIM_DISABLED_TITLE =
  "Disabled in preview — members submit claims from their own sign-in";

/** ONE claim, as its own claimant reads it — the member's own body
 * (`leaf/ClaimDetailLeaf`), with every action arriving as `undefined` so the
 * whole surface is read-only by construction rather than by discipline.
 *
 * The thread is the point of it. Until this existed, `usePreviewClaimMessages`
 * had no consumer and the frame's inbox rows had nowhere to land, so a broker
 * could not read what a member had actually been told about a claim without
 * opening the broker queue and reading a different rendering of it. */
function ClaimDetailTab({
  employeeId,
  claimId,
  onBack,
}: {
  employeeId: string;
  claimId: string;
  onBack: () => void;
}) {
  const claim = usePreviewClaim(employeeId, claimId);
  const messages = usePreviewClaimMessages(employeeId, claimId);
  const back = (
    <button
      type="button"
      onClick={onBack}
      className="leaf-focus -ml-2 inline-flex min-h-11 items-center gap-1.5 px-2 text-row text-label"
    >
      <ArrowLeft className="size-4" aria-hidden /> All claims
    </button>
  );
  if (claim.isLoading) return <LeafSkeleton label="Loading claim" mounts={2} />;
  if (claim.isError && !isNotFoundError(claim.error)) {
    return <PortalErrorState onRetry={() => void claim.refetch()} />;
  }
  if (claim.isError || !claim.data) {
    return (
      <div className="space-y-3">
        {back}
        <Mount label="We couldn't find that claim">
          <p className="text-row text-label">
            It may have been removed. Your other claims are on the claims page.
          </p>
        </Mount>
      </div>
    );
  }
  return (
    <ClaimDetailLeaf
      claim={claim.data}
      messages={messages.data}
      messagesLoading={messages.isLoading}
      messagesError={messages.isError}
      back={back}
      disabledTitle={CLAIM_DISABLED_TITLE}
      // No `onSend`, so `MessageThread` states this in place of the composer.
      // The preview also has no read endpoint to call: opening a thread here is
      // a broker looking, and must never clear the member's unread mark.
      replyDisabledReason="Members reply from their own sign-in. To write to this member, use the claims queue."
    />
  );
}

/** Mirrors routes/portal/claims: the member's own ledger, their filter strip,
 * their empty-state wording — a broker reading this needs to see the screen the
 * employee sees, not a broker summary of it.
 *
 * The one deliberate difference is the action. On the member's page it FLOATS
 * (fixed, bottom-centre); a fixed element inside this bounded frame would
 * escape it and hang over the broker's own app, so here it stays in the flow
 * above the ledger — disabled, because members submit from their own sign-in. */
function ClaimsTab({
  employeeId,
  onOpenClaim,
}: {
  employeeId: string;
  onOpenClaim: (claimId: string) => void;
}) {
  const claims = usePreviewClaims(employeeId);
  if (claims.isLoading) return <LeafSkeleton label="Loading claims" />;
  if (claims.isError && !isNotFoundError(claims.error)) {
    return <PortalErrorState onRetry={() => void claims.refetch()} />;
  }
  const rows = claims.data?.items ?? [];
  if (rows.length === 0) {
    return (
      <Mount label="No claims yet">
        <p className="text-row text-label">
          When you pay for treatment that your benefits cover, send us the
          receipt here and we&rsquo;ll tell you where it&rsquo;s up to.
        </p>
        <div>
          <Action
            tone="primary"
            block="phone"
            disabled
            title={CLAIM_DISABLED_TITLE}
          >
            <FilePlus2 className="size-4" aria-hidden />
            Make a claim
          </Action>
        </div>
      </Mount>
    );
  }
  return (
    <div className="space-y-3">
      <Action tone="primary" block="phone" disabled title={CLAIM_DISABLED_TITLE}>
        <FilePlus2 className="size-4" aria-hidden />
        Make a claim
      </Action>
      {/* `onOpen`, never `interactive`: the member's row is a `<Link>` into the
          live portal, which from here would walk the broker out of their own
          application. */}
      <ClaimList
        items={rows}
        onOpen={(c) => onOpenClaim(c.id)}
        total={claims.data?.total}
      />
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
  // The dependants query gates this too. Nothing here can save — it is a
  // preview — but rendering before it resolves states "Nobody in your family is
  // on this plan" to a broker reading the member's own screen, which is the one
  // thing this frame exists to get right.
  if (enrollment.isLoading || dependants.isPending)
    return <Skeleton className="h-48 w-full" />;
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
  onOpenQuestion,
  onOpenClaim,
}: {
  employeeId: string;
  enrollmentOpen: boolean;
  onGo: (dest: HomeDest) => void;
  onOpenClaim: (claimId: string) => void;
  onOpenQuestion: (enquiryId: string) => void;
}) {
  const utilization = usePreviewUtilization(employeeId);
  const claims = usePreviewClaims(employeeId);
  const statement = usePreviewStatement(employeeId);
  const dependants = usePreviewDependants(employeeId);
  const messages = usePreviewConversations(employeeId);
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
      onOpenClaim={onOpenClaim}
      onOpenQuestion={onOpenQuestion}
    />
  );
}

/** ONE question, read-only — the member's own thread component with no
 * composer, exactly like the claim detail beside it. */
function QuestionDetailTab({
  employeeId,
  enquiryId,
  onBack,
}: {
  employeeId: string;
  enquiryId: string;
  onBack: () => void;
}) {
  const enquiry = usePreviewEnquiry(employeeId, enquiryId);
  const messages = usePreviewEnquiryMessages(employeeId, enquiryId);
  const back = (
    <button
      type="button"
      onClick={onBack}
      className="leaf-focus -ml-2 inline-flex min-h-11 items-center gap-1.5 px-2 text-row text-label"
    >
      <ArrowLeft className="size-4" aria-hidden /> Messages
    </button>
  );
  if (enquiry.isLoading) return <LeafSkeleton label="Loading" mounts={2} />;
  if (enquiry.isError && !isNotFoundError(enquiry.error)) {
    return <PortalErrorState onRetry={() => void enquiry.refetch()} />;
  }
  if (enquiry.isError || !enquiry.data) {
    return (
      <div className="space-y-3">
        {back}
        <Mount label="We couldn't find that question" />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-3">
      {back}
      <Mount
        label={enquiry.data.subject}
        // The topic, and the claim it names if it names one — the same line the
        // member's own pane prints. `topic_label` is served; the raw key never
        // reaches a screen.
        gloss={[
          enquiry.data.topic_label ?? enquiry.data.topic,
          enquiry.data.about_claim
            ? `About ${subjectTitle(enquiry.data.about_claim)}`
            : null,
        ]
          .filter(Boolean)
          .join(" · ")}
        aside={<EnquiryStrike status={enquiry.data.status} />}
      >
        <MessageThread
          messages={messages.data ?? []}
          // Without this the answer reprints the question's own title directly
          // beneath the frame heading that already carries it.
          threadSubject={enquiry.data.subject}
          replyDisabledReason="Members reply from their own sign-in. To answer this question, use the Messages tab on the claims page."
        />
      </Mount>
    </div>
  );
}

/** Mirrors routes/portal/messages: a row opens what it is ABOUT — a claim, or a
 * question's own thread. On the member's page those are routes; here they drill
 * into the frame's own detail views. */
function MessagesTab({
  employeeId,
  onOpenClaim,
  onOpenQuestion,
}: {
  employeeId: string;
  onOpenClaim: (claimId: string) => void;
  onOpenQuestion: (enquiryId: string) => void;
}) {
  const messages = usePreviewConversations(employeeId);
  if (messages.isLoading) return <LeafSkeleton label="Loading messages" />;
  if (messages.isError && !isNotFoundError(messages.error)) {
    return <PortalErrorState onRetry={() => void messages.refetch()} />;
  }
  const items = messages.data?.items ?? [];
  const unread = messages.data?.unread_total ?? 0;
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
      label={`${messages.data?.total ?? items.length} conversation${
        (messages.data?.total ?? items.length) === 1 ? "" : "s"
      }`}
      aside={unread > 0 ? <Strike tone="pending">{unread} unread</Strike> : undefined}
    >
      <ConversationRows
        items={items}
        onOpen={(c) =>
          c.subject.kind === "enquiry"
            ? onOpenQuestion(c.subject.id)
            : onOpenClaim(c.subject.id)
        }
        className="-mt-1"
      />
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
  // The Claims tab's drill-in. The frame has tabs where the portal has routes,
  // so "which claim am I reading" has to be state — and it is the frame's, not
  // the tab's, because the inbox and the home tile both open a claim too.
  const [claimId, setClaimId] = useState<string | null>(null);
  const [enquiryId, setEnquiryId] = useState<string | null>(null);

  // Re-selecting a different employee restarts the walkthrough at the home
  // screen — the same place the member lands when they sign in.
  useEffect(() => {
    setTab("home");
    setCoverageTab("benefits");
    setClaimId(null);
    setEnquiryId(null);
  }, [employeeId]);

  /** Move to a tab the way the member's NAV moves: a claim being read is left
   *  behind. On the portal, tapping "Claims" always lands on the ledger — a
   *  frame that resumed the last claim instead would be showing a screen the
   *  member cannot reach that way. */
  const showTab = (key: TabKey) => {
    setClaimId(null);
    setEnquiryId(null);
    setTab(key);
  };

  /** A home tile's destination, mapped onto this frame's tabs. */
  const goFromHome = (dest: HomeDest) => {
    if (dest === "usage" || dest === "benefits" || dest === "dependants") {
      setCoverageTab(dest);
      showTab("coverage");
      return;
    }
    showTab(dest);
  };

  /** A message row, a home row or a ledger row opening one claim. Always lands
   *  on the Claims tab, so the "All claims" control below it goes somewhere the
   *  broker recognises. */
  const openClaim = (id: string) => {
    setEnquiryId(null);
    setClaimId(id);
    setTab("claims");
  };

  /** A question has no claim to open, so it stays on the Messages tab and
   *  replaces the list — the member's own surface is a route off Messages
   *  too. */
  const openQuestion = (id: string) => {
    setClaimId(null);
    setEnquiryId(id);
    setTab("messages");
  };

  // Same query key the Home and Messages tabs read, so the badge is the count
  // those screens show and costs no extra request.
  const unread = usePreviewConversations(employeeId).data?.unread_total ?? 0;

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
                  onClick={() => showTab(item.key)}
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
              {/* The member's Messages icon, in the member's own place. A
                  BUTTON, not a `<Link>`: this frame navigates by switching its
                  own tabs, and a portal route here would walk the broker out of
                  their own application. */}
              <button
                type="button"
                onClick={() => showTab("messages")}
                aria-label={messagesLabel(unread)}
                className={cn(
                  ICON_BUTTON,
                  tab === "messages" && "text-action-ink",
                )}
              >
                <MessageSquare className="size-5" aria-hidden />
                <UnreadBadge count={unread} />
              </button>
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
                onOpenClaim={openClaim}
                onOpenQuestion={openQuestion}
              />
            )}
            {tab === "coverage" && (
              <CoverageTab
                employeeId={employeeId}
                tab={coverageTab}
                setTab={setCoverageTab}
              />
            )}
            {tab === "claims" &&
              (claimId ? (
                <ClaimDetailTab
                  employeeId={employeeId}
                  claimId={claimId}
                  onBack={() => setClaimId(null)}
                />
              ) : (
                <ClaimsTab employeeId={employeeId} onOpenClaim={openClaim} />
              ))}
            {tab === "messages" &&
              (enquiryId ? (
                <QuestionDetailTab
                  employeeId={employeeId}
                  enquiryId={enquiryId}
                  onBack={() => setEnquiryId(null)}
                />
              ) : (
                <MessagesTab
                  employeeId={employeeId}
                  onOpenClaim={openClaim}
                  onOpenQuestion={openQuestion}
                />
              ))}
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
