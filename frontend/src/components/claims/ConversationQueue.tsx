/**
 * REDESIGN CONTRACT
 * THESIS: This is a triage desk, not a message card; the queue keeps priority
 * and the active claim visible together without making every reply a round trip.
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
 * A claim thread still needs the claim itself for adjudication — the amount,
 * the documents, the AI review, the decision. That is a BUTTON on the pane
 * ("Open in the queue"), not the price of reading a message.
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
import { useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  MessageSquare,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  useBrokerConversations,
  useBrokerEnquiry,
  useBrokerEnquiryMessages,
  useMarkEnquiryRead,
  useSendBrokerEnquiryMessage,
  useSetEnquiryStatus,
  type BrokerConversation,
} from "@/api/claims";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ClaimMessages } from "@/components/claims/ClaimMessages";
import { ThreadMessages } from "@/components/claims/ThreadMessages";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { fmtDay, fmtMoney, parseServerDate } from "@/lib/format";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 25;

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

/** A Letter of Guarantee request. The one topic where the delay is the harm —
 * the member is usually standing at an admissions counter — so it is lifted to
 * the top of the queue SERVER-side (`claim_messages._row_is_urgent`) and marked
 * here. Read off the served flag, never matched on the topic key: the
 * vocabulary lives on the backend and a key spelled out in TypeScript is a
 * second place for it to drift. */
function isUrgent(c: BrokerConversation): boolean {
  return c.subject.kind === "enquiry" && c.subject.topic_urgent;
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
      s.topic_label || s.topic,
      ENQUIRY_BADGE[s.status ?? ""]?.label ?? s.status,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  const name =
    s.claim_kind === "flex"
      ? s.flex_category_name || "Flexible benefit"
      : s.claim_type || s.product_code || "Claim";
  return [
    name,
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
          "focus-ring block min-h-24 w-full px-4 py-3.5 text-left transition-colors",
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
                <p className="truncate font-semibold text-foreground">{name}</p>
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
            <div className="mt-2 flex min-w-0 items-center gap-2">
              <Badge variant="outline" className="shrink-0">
                {conversation.subject.kind === "claim" ? "Claim" : "Question"}
              </Badge>
              {isUrgent(conversation) && <Badge variant="error">Urgent</Badge>}
              <p className="truncate text-sm font-medium text-foreground">
                {subjectLine(conversation)}
              </p>
            </div>

            <div className="mt-1.5 flex items-center gap-2">
              <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
                <span className="text-foreground">{last.mine ? "Us" : name}:</span>{" "}
                {last.body}
              </p>
              {conversation.unread > 0 && (
                <Badge variant="warn" className="shrink-0">
                  {conversation.unread} new
                </Badge>
              )}
            </div>
          </div>
        </div>
      </button>
    </li>
  );
}

/** The pane header both kinds share: whose thread it is, then what it is. */
function PaneHead({
  who,
  staffId,
  title,
  detail,
  badges,
  action,
  onBack,
}: {
  who: string;
  staffId?: string;
  title: string;
  detail?: string;
  badges?: React.ReactNode;
  action?: React.ReactNode;
  onBack?: () => void;
}) {
  return (
    <div className="-mx-5 flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-border bg-card px-5 pb-4 pt-1">
      <div className="flex min-w-0 flex-1 items-start gap-2">
        {onBack && (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="-ml-2 shrink-0 lg:hidden"
            onClick={onBack}
            aria-label="Back to message inbox"
          >
            <ArrowLeft className="size-4" />
          </Button>
        )}
        <div className="min-w-0">
        <h3 className="text-base font-semibold text-foreground">
          {who}
          {staffId && (
            <span className="ml-2 text-xs tabular-nums text-muted-foreground">
              {staffId}
            </span>
          )}
        </h3>
        <p className="mt-1 line-clamp-2 text-sm font-medium text-foreground">{title}</p>
        {detail && (
          <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
        )}
        {badges && <div className="mt-2 flex flex-wrap gap-2">{badges}</div>}
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
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
  const setStatus = useSetEnquiryStatus();
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
  const badge = data ? ENQUIRY_BADGE[data.status] : undefined;

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
      <PaneHead
        onBack={onBack}
        who={data.employee?.employee_name ?? "Unknown employee"}
        staffId={data.employee?.staff_id}
        title={data.subject}
        // The claim this question NAMES, when it names one. Context, not
        // ownership: the conversation does not live on that claim and that
        // claim's own thread is untouched by it.
        detail={
          data.about_claim
            ? `About ${
                data.about_claim.claim_type ??
                data.about_claim.product_code ??
                "a claim"
              }${
                data.about_claim.incurred_date
                  ? ` · ${fmtDay(data.about_claim.incurred_date)}`
                  : ""
              }`
            : undefined
        }
        badges={
          <>
            {data.topic_urgent && <Badge variant="error">Urgent</Badge>}
            {badge && <Badge variant={badge.variant}>{badge.label}</Badge>}
            <Badge variant="outline">{data.topic_label ?? data.topic}</Badge>
          </>
        }
        action={
          // Closing 409s until somebody has answered — a thread that ends with
          // no answer in it reads as being ignored on purpose.
          !readOnly && (
            <Button
              size="sm"
              variant="outline"
              disabled={setStatus.isPending}
              onClick={() =>
                setStatus.mutate(
                  { enquiryId, action: closed ? "reopen" : "close" },
                  { onError: (e) => toast.error(formatError(e)) },
                )
              }
            >
              {closed ? "Reopen" : "Close question"}
            </Button>
          )
        }
      />
      <ThreadMessages
        key={enquiryId}
        idSuffix={enquiryId}
        messages={messages.data}
        loading={messages.isLoading}
        error={messages.isError}
        onRetry={() => void messages.refetch()}
        sending={send.isPending}
        threadSubject={data.subject}
        placeholder="Answer the member…"
        emptyText="Nothing in this thread yet."
        disabledReason={
          readOnly
            ? "Your access is read-only, so you can't write to the member from here."
            : closed
              ? "This question is closed. Reopen it to write again."
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
        stickyComposer
      />
    </div>
  );
}

/** A claim's thread, with the way into the claim itself. */
function ClaimPane({
  conversation,
  onOpenClaim,
  onBack,
}: {
  conversation: BrokerConversation;
  onOpenClaim: (claimId: string) => void;
  onBack: () => void;
}) {
  const employee = conversation.employee;
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <PaneHead
        onBack={onBack}
        who={employee?.employee_name ?? "Unknown employee"}
        staffId={employee?.staff_id}
        title={subjectLine(conversation)}
        action={
          <Button
            size="sm"
            variant="outline"
            onClick={() => onOpenClaim(conversation.subject.id)}
          >
            <ExternalLink className="size-4" />
            Open claim
          </Button>
        }
      />
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
  const navigate = useNavigate();
  const [view, setView] = useState<View>("us");
  const [page, setPage] = useState(0);
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const { data, isLoading, isError, error, refetch } = useBrokerConversations(
    policyYearId ?? undefined,
    view,
    page * PAGE_SIZE,
    PAGE_SIZE,
    debouncedSearch,
  );

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
      setPickedKey(data.items[0] ? conversationKey(data.items[0]) : null);
    }
  }, [data, isLoading, page, pickedKey]);

  if (!policyYearId) return null;

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);
  // The pane shows the picked thread, or the top of the queue — which for
  // "Needs reply" is the person who has waited longest, i.e. the one the tab
  // was opened to answer.
  const selected =
    items.find((conversation) => conversationKey(conversation) === pickedKey) ??
    items[0];

  // Adjudication still lives in the Queue tab's claim sheet. Same deep link the
  // employee-level LOG card uses.
  const openClaim = (claimId: string) =>
    void navigate({
      to: "/claims/review",
      search: { tab: "queue", claim: claimId },
    });

  return (
    <section
      aria-labelledby="message-inbox-heading"
      className="h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="grid h-full min-w-0 lg:grid-cols-[25rem_minmax(0,1fr)]">
        <aside
          className={cn(
            "min-h-0 min-w-0 flex-col bg-card lg:flex lg:border-r lg:border-border",
            pickedKey ? "hidden" : "flex",
          )}
          aria-label="Conversation inbox"
        >
          <header className="border-b border-border px-4 pb-4 pt-4">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 id="message-inbox-heading" className="text-base font-semibold">
                  Messages
                </h2>
                <p className="mt-0.5 text-sm text-muted-foreground" aria-live="polite">
                  {isLoading
                    ? "Loading conversations…"
                    : view === "us"
                      ? `${total} ${total === 1 ? "member" : "members"} waiting for a reply`
                      : `${total} ${total === 1 ? "conversation" : "conversations"} this benefit year`}
                </p>
              </div>
              {view === "us" && total > 0 && (
                <Badge variant="warn" className="shrink-0">
                  {total} open
                </Badge>
              )}
            </div>

            <div className="relative mt-4">
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

            <div
              className="mt-3 grid grid-cols-2 rounded-lg bg-muted p-1"
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
                    "focus-ring min-h-8 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    view === item.key
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </header>

          <div className="min-h-0 flex-1 overscroll-contain overflow-y-auto">
            {isLoading ? (
              <div
                className="space-y-2 p-3"
                role="status"
                aria-label="Loading conversations"
              >
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-28 w-full" />
              </div>
            ) : isError ? (
              <div className="flex h-full min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
                <p className="text-sm font-medium text-error">
                  Couldn&apos;t load conversations
                </p>
                <p className="text-sm text-muted-foreground">{formatError(error)}</p>
                <Button variant="outline" size="sm" onClick={() => void refetch()}>
                  <RefreshCw className="size-4" /> Retry
                </Button>
              </div>
            ) : items.length === 0 ? (
              <div className="flex h-full min-h-72 flex-col items-center justify-center px-6 text-center">
                <span className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
                  {debouncedSearch ? (
                    <Search className="size-5 text-muted-foreground" aria-hidden />
                  ) : (
                    <MessageSquare className="size-5 text-muted-foreground" aria-hidden />
                  )}
                </span>
                <p className="font-medium text-foreground">
                  {debouncedSearch
                    ? "No matching conversations"
                    : view === "us"
                      ? "Your reply queue is clear"
                      : "No conversations yet"}
                </p>
                <p className="mt-1 max-w-64 text-sm text-muted-foreground">
                  {debouncedSearch
                    ? "Try a member name, staff ID, claim type, or words from the latest message."
                    : view === "us"
                      ? "New member replies will appear here, with the longest wait first."
                      : "Member claim messages and questions will appear here."}
                </p>
                {debouncedSearch && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-4"
                    onClick={() => setSearch("")}
                  >
                    Clear search
                  </Button>
                )}
              </div>
            ) : (
              <ul className="divide-y divide-border" aria-label="Conversations">
                {items.map((conversation) => (
                  <ConversationRow
                    key={conversationKey(conversation)}
                    conversation={conversation}
                    onOpen={(picked) => setPickedKey(conversationKey(picked))}
                    selected={
                      selected
                        ? conversationKey(selected) === conversationKey(conversation)
                        : false
                    }
                  />
                ))}
              </ul>
            )}
          </div>

          {!isLoading && !isError && total > 0 && (
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
          {isLoading ? (
            <div className="space-y-4 p-5">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : selected ? (
            <div className="min-h-0 min-w-0 flex-1 overflow-hidden p-5">
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
                  onOpenClaim={openClaim}
                  onBack={() => setPickedKey(null)}
                />
              )}
            </div>
          ) : (
            <div className="flex h-full min-h-72 flex-col items-center justify-center px-8 text-center">
              <span className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
                <MessageSquare className="size-5 text-muted-foreground" aria-hidden />
              </span>
              <p className="font-medium text-foreground">Choose a conversation</p>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Read the member&apos;s message, reply, and open the linked claim without
                losing your place in the inbox.
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-4 lg:hidden"
                onClick={() => setPickedKey(null)}
              >
                <ArrowLeft className="size-4" /> Back to inbox
              </Button>
            </div>
          )}
        </section>
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
