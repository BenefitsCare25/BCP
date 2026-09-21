/**
 * REDESIGN CONTRACT
 * THESIS: This is a triage desk, not a message card; the queue keeps priority
 * and the active reply visible together without making every reply a round trip.
 * OWN-WORLD: Restrained warm neutrals, compact rows, clear state labels, and one
 * red action language inherited from the broker app.
 * STORY: Find the oldest or urgent member, understand the claim at a glance,
 * reply, then continue through the queue without losing place.
 * FIRST VIEWPORT: Search and workload controls sit over a dense index; the
 * selected conversation and anchored composer occupy the larger reading pane.
 * FORM: Focused split workbench, directly shaped for this existing surface; no
 * concept seed. On small screens it becomes an inbox-to-thread drill-in.
 */

/** The broker's message queue — who is waiting on a reply, and the reply.
 *
 * It exists because there was no way to ask the first question. `GET /claims`
 * filters on status, employee and case type; the only signal that a member had
 * written was a "N new" badge on a claim ROW, so finding them meant scrolling
 * the whole queue looking for badges. On a 491-member roster that is not a
 * workflow.
 *
 * ── An index and a PANE ─────────────────────────────────────────────────────
 *
 * It is one screen: the list on the left, the picked conversation open on the
 * right. It used to be a list alone, and the two kinds of row behaved
 * differently — a question opened a sheet in place, a claim NAVIGATED to
 * another tab and swapped the whole page for the claim sheet. So working
 * through five waiting members cost five round trips, each one losing the
 * queue's scroll position and its filter, and the same row shape did two
 * different things depending on what it happened to be about.
 *
 * ── Three things a row must carry so triage needs no click ──────────────────
 *
 *   **who** — name and staff id, leading the row. This is the answer.
 *   **how long** — the last message's age. A thread that has waited five days
 *                  is a different object from one that arrived this morning,
 *                  and every row carries a time now: it used to print one only
 *                  where the member had written last, so the All view had no
 *                  time information on it at all.
 *   **what they said** — the member's own words, one line of them.
 *
 * **Needs reply is the default view and sorts OLDEST first** (the server does
 * both). In a queue the thing that has waited longest is the one about to
 * become a complaint, so it belongs at the top; "All" is for looking a thread
 * up rather than working through it, and sorts newest-first.
 */
import { useEffect, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  useBrokerConversations,
  useBrokerEnquiry,
  useBrokerEnquiryMessages,
  useMarkEnquiryRead,
  useSendBrokerEnquiryMessage,
  type BrokerConversation,
  type ConversationFilters,
} from "@/api/claims";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ClaimMessages } from "@/components/claims/ClaimMessages";
import { ThreadMessages } from "@/components/claims/ThreadMessages";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { fmtDay, fmtMoney, parseServerDate } from "@/lib/format";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 25;

const CATEGORY_LABELS = { inpatient: "Inpatient", outpatient: "Outpatient", flex: "Flex", other: "Other insured" };

const CLAIM_STATUSES = [
  ["draft", "Draft"],
  ["submitted", "Submitted"],
  ["ai_review_pending", "AI review pending"],
  ["ai_verified", "AI verified"],
  ["ai_flagged", "AI flagged"],
  ["needs_info", "Needs information"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["sent_to_insurer", "With insurer"],
  ["paid", "Paid"],
] as const;

function statusLabel(value: string): string {
  return CLAIM_STATUSES.find(([status]) => status === value)?.[1] ?? value;
}

function ClaimContext({ conversation }: { conversation: BrokerConversation }) {
  const subject = conversation.subject.about_claim ?? conversation.subject;
  return <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
    {subject.claim_category && <Badge variant="outline">{CATEGORY_LABELS[subject.claim_category]}</Badge>}
    {subject.reference_no && <span className="font-mono">{subject.reference_no}</span>}
  </div>;
}

function conversationKey(conversation: BrokerConversation): string {
  return `${conversation.subject.kind}:${conversation.subject.id}`;
}

/** How long the member has been waiting, in the units a person would say it
 * in. Deliberately coarse: "3 days" is actionable, "3 days 4 hours" is not.
 *
 * Through `parseServerDate`, never bare `new Date()` — SQLite serializes UTC
 * with no offset and the browser reads that as LOCAL, which in Singapore makes
 * every message eight hours younger than it is. A queue sorted and labelled by
 * age is exactly where that lie does damage. */
function ageOf(iso: string): string {
  const mins = Math.floor((Date.now() - parseServerDate(iso).getTime()) / 60000);
  if (!Number.isFinite(mins) || mins < 0) return "just now";
  if (mins < 60) return `${Math.max(mins, 1)} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hr`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

const ENQUIRY_BADGE: Record<
  string,
  { variant: "warn" | "good" | "outline"; label: string }
> = {
  open: { variant: "warn", label: "Waiting" },
  answered: { variant: "good", label: "Answered" },
  closed: { variant: "outline", label: "Closed" },
};

function conversationType(c: BrokerConversation): string {
  const subject = c.subject;
  if (subject.kind === "enquiry") {
    return subject.topic_label || subject.topic || "Question";
  }
  return subject.claim_kind === "flex"
    ? subject.flex_category_name || "Flexible benefit"
    : subject.claim_type || subject.product_code || "Claim";
}

/** What the thread is about, in the broker's vocabulary — the product and the
 * figures they adjudicate on.
 *
 * The date goes through `fmtDay`, not `fmtDate`: that one returns the ISO
 * string unchanged, so these rows read `… · 2026-06-27 · SGD 165.83`. The topic
 * comes from the SERVED `topic_label`, not the raw key, which is why a question
 * used to read `Question · clinics · answered`. */
function subjectLine(c: BrokerConversation): string {
  const s = c.subject;
  if (s.kind === "enquiry") {
    return [
      "Question",
      conversationType(c),
      ENQUIRY_BADGE[s.status ?? ""]?.label ?? s.status,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return [
    conversationType(c),
    s.incurred_date ? fmtDay(s.incurred_date) : null,
    s.amount_claimed != null ? fmtMoney(s.amount_claimed) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function ConversationRow({
  conversation,
  onOpen,
  selected,
}: {
  conversation: BrokerConversation;
  onOpen: (conversation: BrokerConversation) => void;
  selected: boolean;
}) {
  const last = conversation.last_message;
  const employee = conversation.employee;
  // Only meaningful while the ball is with us — on a thread we answered last,
  // an age is the age of our own reply, so it is labelled as what it is rather
  // than dropped, which left the All view with no time on it at all.
  const waiting = last.author_type === "member";
  const name = employee?.employee_name ?? "Unknown employee";
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(conversation)}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "focus-ring block min-h-20 w-full px-4 py-3.5 text-left transition-colors",
          selected
            ? "bg-accent/70 ring-1 ring-inset ring-border-strong"
            : "hover:bg-muted/60",
        )}
      >
        <div className="flex items-start gap-3">
          <div
            className={cn(
              "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
              selected
                ? "bg-card text-accent-foreground"
                : "bg-muted text-muted-foreground",
            )}
            aria-hidden
          >
            {name
              .split(/\s+/)
              .filter(Boolean)
              .slice(0, 2)
              .map((part) => part[0])
              .join("")
              .toUpperCase() || "?"}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 truncate font-semibold text-foreground">
                  {conversation.unread > 0 && (
                    <span
                      className="size-2 shrink-0 rounded-full bg-primary"
                      aria-label={`${conversation.unread} unread message${conversation.unread === 1 ? "" : "s"}`}
                    />
                  )}
                  <span className="truncate">{name}</span>
                </p>
                <p className="text-xs tabular-nums text-muted-foreground">
                  {employee?.staff_id || "No staff ID"}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p
                  className={cn(
                    "text-xs font-medium tabular-nums",
                    waiting ? "text-warn" : "text-muted-foreground",
                  )}
                >
                  {ageOf(last.created_at)}
                </p>
                <p className="text-2xs text-muted-foreground">
                  {waiting ? "waiting" : "since reply"}
                </p>
              </div>
            </div>
            <p className="mt-2 truncate text-sm font-medium text-foreground">
              {subjectLine(conversation)}
            </p>
            <p className="mt-1 truncate text-sm text-muted-foreground">
              {last.body}
            </p>
            <ClaimContext conversation={conversation} />
          </div>
        </div>
      </button>
    </li>
  );
}

/** A member's question, answered in place.
 *
 * It was a Sheet over the queue; the pane is the same content without the
 * overlay, so a broker answering three questions never loses the list. */
function EnquiryPane({
  enquiryId,
  onBack,
}: {
  enquiryId: string;
  onBack: () => void;
}) {
  const enquiry = useBrokerEnquiry(enquiryId);
  const messages = useBrokerEnquiryMessages(enquiryId);
  const send = useSendBrokerEnquiryMessage();
  const markRead = useMarkEnquiryRead();
  const { data: me } = useMe();
  const readOnly = me?.role === "broker_viewer";

  // Opening the pane IS reading it — the thread is rendered in full. Gated on
  // there being something unread so reopening a settled question doesn't fire a
  // write on every visit.
  const hasUnread = (messages.data ?? []).some((m) => m.unread);
  const markMutate = markRead.mutate;
  useEffect(() => {
    if (enquiryId && hasUnread && !readOnly) markMutate(enquiryId);
  }, [enquiryId, hasUnread, readOnly, markMutate]);

  const data = enquiry.data;
  const closed = data?.status === "closed";

  if (!data) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <p className={enquiry.isError ? "text-sm text-error" : "text-sm text-muted-foreground"}>
          {enquiry.isError ? "Couldn't load this question." : "Loading…"}
        </p>
        {enquiry.isError && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void enquiry.refetch()}
          >
            <RefreshCw className="size-4" /> Retry
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="-ml-2 self-start lg:hidden"
        onClick={onBack}
      >
        <ArrowLeft className="size-4" /> Back to messages
      </Button>
      <ThreadMessages
        key={enquiryId}
        idSuffix={enquiryId}
        messages={messages.data}
        loading={messages.isLoading}
        error={messages.isError}
        onRetry={() => void messages.refetch()}
        sending={send.isPending}
        stickyComposer
        threadSubject={data.subject}
        placeholder="Answer the member…"
        emptyText="Nothing in this thread yet."
        disabledReason={
          readOnly
            ? "Your access is read-only, so you can't write to the member from here."
            : closed
              ? "This question is closed."
              : undefined
        }
        onSend={
          readOnly || closed
            ? undefined
            : async (body) => {
                try {
                  await send.mutateAsync({ enquiryId, body });
                } catch (err) {
                  toast.error(formatError(err));
                  throw err;
                }
              }
        }
      />
    </div>
  );
}

/** A claim's thread, with the way into the claim itself. */
function ClaimPane({
  conversation,
  onBack,
}: {
  conversation: BrokerConversation;
  onBack: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="-ml-2 self-start lg:hidden"
        onClick={onBack}
      >
        <ArrowLeft className="size-4" /> Back to messages
      </Button>
      <ClaimMessages claimId={conversation.subject.id} stickyComposer />
    </div>
  );
}

const VIEWS = [
  { key: "us", label: "Needs reply" },
  { key: "any", label: "All" },
] as const;

type View = (typeof VIEWS)[number]["key"];

export function ConversationQueue() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const [view, setView] = useState<View>("us");
  const [page, setPage] = useState(0);
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const [filters, setFilters] = useState<ConversationFilters>({});
  const updateFilters = (patch: Partial<ConversationFilters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setPage(0);
    setPickedKey(null);
  };
  const needsReplyQuery = useBrokerConversations(
    policyYearId ?? undefined,
    "us",
    page * PAGE_SIZE,
    PAGE_SIZE,
    debouncedSearch,
    filters,
  );
  const allConversationsQuery = useBrokerConversations(
    policyYearId ?? undefined,
    "any",
    page * PAGE_SIZE,
    PAGE_SIZE,
    debouncedSearch,
    filters,
  );
  const activeQuery = view === "us" ? needsReplyQuery : allConversationsQuery;
  const { data, isLoading, isError, error, refetch } = activeQuery;

  const resetCriteria = () => {
    setSearch("");
    setFilters({});
    setPage(0);
    setPickedKey(null);
  };

  // A reply can remove the open thread from Needs reply, including the only
  // row on the last page. Advance to the next piece of work when one exists;
  // otherwise return mobile users to the inbox and clamp the page. Without
  // this, `pickedKey` kept the inbox hidden behind an empty detail pane.
  useEffect(() => {
    if (isLoading || !data) return;
    const lastPage = Math.max(Math.ceil(data.total / PAGE_SIZE) - 1, 0);
    if (page > lastPage) {
      setPage(lastPage);
      setPickedKey(null);
      return;
    }
    if (
      pickedKey &&
      !data.items.some((conversation) => conversationKey(conversation) === pickedKey)
    ) {
      const next = data.items.find((conversation) => conversation.subject.policy_year_id === policyYearId);
      setPickedKey(next ? conversationKey(next) : null);
    }
  }, [data, isLoading, page, pickedKey, policyYearId]);

  if (!policyYearId) return null;

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);
  const viewCounts: Record<View, number | undefined> = {
    us: needsReplyQuery.data?.total,
    any: allConversationsQuery.data?.total,
  };
  const activeFilterCount = Number(Boolean(filters.category)) + Number(Boolean(filters.status));
  const hasCriteria = Boolean(debouncedSearch || activeFilterCount);
  // The pane shows the picked thread, or the top of the queue — which for
  // "Needs reply" is the person who has waited longest, i.e. the one the tab
  // was opened to answer.
  const selected =
    items.find((conversation) => conversationKey(conversation) === pickedKey && (!conversation.subject.policy_year_id || conversation.subject.policy_year_id === policyYearId)) ??
    items.find((conversation) => !conversation.subject.policy_year_id || conversation.subject.policy_year_id === policyYearId);

  return (
    <section
      aria-label="Messages"
      className="overflow-hidden rounded-xl border border-border bg-card"
    >
      <header
        className={cn(
          "border-b border-border bg-card px-4 py-3",
          pickedKey ? "hidden lg:block" : "block",
        )}
      >
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
          <div
            className="grid shrink-0 grid-cols-2 rounded-lg bg-muted p-1"
            aria-label="Conversation view"
          >
            {VIEWS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => {
                  setView(item.key);
                  setPage(0);
                  setPickedKey(null);
                }}
                aria-pressed={view === item.key}
                className={cn(
                  "focus-ring flex min-h-9 items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  view === item.key
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {item.label}
                {viewCounts[item.key] != null && (
                  <span
                    className={cn(
                      "min-w-5 rounded-full px-1.5 py-0.5 text-center text-xs tabular-nums",
                      view === item.key ? "bg-muted text-foreground" : "bg-card/70",
                    )}
                  >
                    {viewCounts[item.key]}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="relative min-w-0 flex-1 xl:max-w-xl">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(0);
                setPickedKey(null);
              }}
              className="pl-9 pr-9 [&::-webkit-search-cancel-button]:appearance-none"
              aria-label="Search conversations"
              placeholder="Search member, staff ID, claim or message"
            />
            {search && (
              <button
                type="button"
                onClick={() => {
                  setSearch("");
                  setPage(0);
                  setPickedKey(null);
                }}
                className="focus-ring absolute right-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label="Clear search field"
              >
                <X className="size-4" />
              </button>
            )}
          </div>

          <Sheet>
            <SheetTrigger asChild>
              <Button type="button" variant="outline" className="shrink-0">
                <SlidersHorizontal className="size-4" />
                Filters
                {activeFilterCount > 0 && (
                  <span className="min-w-5 rounded-full bg-primary px-1.5 py-0.5 text-center text-xs text-primary-foreground tabular-nums">
                    {activeFilterCount}
                  </span>
                )}
              </Button>
            </SheetTrigger>
            <SheetContent className="sm:max-w-md" aria-describedby={undefined}>
              <SheetHeader>
                <SheetTitle>Filter conversations</SheetTitle>
              </SheetHeader>
              <SheetBody className="space-y-5">
                <label className="block text-sm font-medium text-foreground">
                  Claim category
                  <select
                    className="focus-ring mt-2 h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground"
                    value={filters.category ?? ""}
                    onChange={(event) => updateFilters({ category: (event.target.value || undefined) as ConversationFilters["category"] })}
                  >
                    <option value="">All categories</option>
                    {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm font-medium text-foreground">
                  Claim status
                  <select
                    className="focus-ring mt-2 h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground"
                    value={filters.status ?? ""}
                    onChange={(event) => updateFilters({ status: event.target.value || undefined })}
                  >
                    <option value="">All statuses and questions</option>
                    {CLAIM_STATUSES.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                {(filters.category || filters.status) && (
                  <p className="text-sm text-muted-foreground">
                    Claim filters also include questions linked to matching claims.
                  </p>
                )}
              </SheetBody>
              <SheetFooter>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={activeFilterCount === 0}
                  onClick={() => {
                    setFilters({});
                    setPage(0);
                    setPickedKey(null);
                  }}
                >
                  Clear filters
                </Button>
                <SheetClose asChild>
                  <Button type="button">Done</Button>
                </SheetClose>
              </SheetFooter>
            </SheetContent>
          </Sheet>
        </div>

        {activeFilterCount > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="Active filters">
            {filters.category && (
              <button
                type="button"
                onClick={() => updateFilters({ category: undefined })}
                className="focus-ring inline-flex min-h-8 items-center gap-1.5 rounded-full border border-border bg-muted px-3 text-xs font-medium text-foreground hover:bg-secondary"
                aria-label={`Remove ${CATEGORY_LABELS[filters.category]} category filter`}
              >
                {CATEGORY_LABELS[filters.category]}
                <X className="size-3.5" aria-hidden />
              </button>
            )}
            {filters.status && (
              <button
                type="button"
                onClick={() => updateFilters({ status: undefined })}
                className="focus-ring inline-flex min-h-8 items-center gap-1.5 rounded-full border border-border bg-muted px-3 text-xs font-medium text-foreground hover:bg-secondary"
                aria-label={`Remove ${statusLabel(filters.status)} status filter`}
              >
                {statusLabel(filters.status)}
                <X className="size-3.5" aria-hidden />
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setFilters({});
                setPage(0);
                setPickedKey(null);
              }}
              className="focus-ring min-h-8 rounded-md px-2 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              Clear filters
            </button>
          </div>
        )}
      </header>

      <div className="min-h-96 lg:h-[calc(100dvh-16rem)] lg:min-h-[34rem] lg:max-h-[46rem]">
        {isLoading ? (
          <div className="grid h-full min-w-0 lg:grid-cols-[24rem_minmax(0,1fr)]" role="status" aria-label="Loading conversations">
            <div className="space-y-2 border-r border-border p-3">
              <Skeleton className="h-28 w-full" />
              <Skeleton className="h-28 w-full" />
              <Skeleton className="h-28 w-full" />
            </div>
            <div className="hidden space-y-4 p-5 lg:block">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          </div>
        ) : isError ? (
          <div className="flex h-full min-h-96 flex-col items-center justify-center gap-3 px-6 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-error-soft">
              <MessageSquare className="size-5 text-error" aria-hidden />
            </span>
            <p className="font-medium text-foreground">Couldn&apos;t load conversations</p>
            <p className="max-w-sm text-sm text-muted-foreground">{formatError(error)}</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              <RefreshCw className="size-4" /> Retry
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-full min-h-96 flex-col items-center justify-center px-6 text-center">
            <span
              className={cn(
                "mb-4 flex size-12 items-center justify-center rounded-full",
                !hasCriteria && view === "us" ? "bg-good-soft" : "bg-muted",
              )}
            >
              {hasCriteria ? (
                <Search className="size-5 text-muted-foreground" aria-hidden />
              ) : view === "us" ? (
                <CheckCircle2 className="size-6 text-good" aria-hidden />
              ) : (
                <MessageSquare className="size-5 text-muted-foreground" aria-hidden />
              )}
            </span>
            <p className="font-semibold text-foreground">
              {hasCriteria
                ? "No matching conversations"
                : view === "us"
                  ? "No replies waiting"
                  : "No conversations yet"}
            </p>
            <p className="mt-1 max-w-sm text-sm text-muted-foreground">
              {hasCriteria
                ? "Adjust your search or filters to see more conversations."
                : view === "us"
                  ? "You're caught up for this policy year."
                  : "Member messages will appear here."}
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {hasCriteria && (
                <Button type="button" variant="outline" size="sm" onClick={resetCriteria}>
                  Clear search and filters
                </Button>
              )}
              {!hasCriteria && view === "us" && (viewCounts.any ?? 0) > 0 && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setView("any");
                    setPage(0);
                    setPickedKey(null);
                  }}
                >
                  View all conversations
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="grid h-full min-w-0 lg:grid-cols-[24rem_minmax(0,1fr)]">
        <aside
          className={cn(
                  "min-h-0 min-w-0 flex-col bg-card lg:flex lg:border-r lg:border-border",
            pickedKey ? "hidden" : "flex",
          )}
          aria-label="Conversation inbox"
        >
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <ul className="divide-y divide-border" aria-label="Conversations">
                {items.map((conversation) => (
                  <ConversationRow
                    key={conversationKey(conversation)}
                    conversation={conversation}
                    onOpen={(picked) => {
                      setPickedKey(conversationKey(picked));
                    }}
                    selected={
                      selected
                        ? conversationKey(selected) === conversationKey(conversation)
                        : false
                    }
                  />
                ))}
              </ul>
          </div>

                {total > 0 && (
            <footer className="flex min-h-14 items-center justify-between gap-3 border-t border-border px-4 py-2">
              <p className="text-xs tabular-nums text-muted-foreground">
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </p>
              <div className="flex items-center gap-1">
                <span className="mr-1 text-xs tabular-nums text-muted-foreground">
                  Page {page + 1} of {Math.max(pages, 1)}
                </span>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  disabled={page === 0}
                  onClick={() => {
                    setPage((current) => current - 1);
                    setPickedKey(null);
                  }}
                  aria-label="Previous page"
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  disabled={page >= pages - 1}
                  onClick={() => {
                    setPage((current) => current + 1);
                    setPickedKey(null);
                  }}
                  aria-label="Next page"
                >
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </footer>
          )}
        </aside>

        <section
          className={cn(
                  "min-h-0 min-w-0 flex-col bg-card lg:flex",
            pickedKey ? "flex" : "hidden",
          )}
          aria-label="Selected conversation"
        >
                {selected ? (
            <div className="h-full min-h-0 min-w-0 flex-1 overflow-hidden p-5">
              {selected.subject.kind === "enquiry" ? (
                <EnquiryPane
                  key={selected.subject.id}
                  enquiryId={selected.subject.id}
                  onBack={() => setPickedKey(null)}
                />
              ) : (
                <ClaimPane
                  key={selected.subject.id}
                  conversation={selected}
                  onBack={() => setPickedKey(null)}
                />
              )}
            </div>
                ) : null}
        </section>
      </div>
        )}
      </div>
    </section>
  );
}

/** The tab's own count: how many threads are waiting on us. Its own small
 * query rather than a prop, so the badge is live wherever the Claims page is —
 * and it shares the list's key prefix, so it refreshes when a broker replies. */
export function useAwaitingReplyCount(): { count: number; isError: boolean } {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const query = useBrokerConversations(policyYearId ?? undefined, "us", 0, 1);
  return { count: query.data?.total ?? 0, isError: query.isError };
}
