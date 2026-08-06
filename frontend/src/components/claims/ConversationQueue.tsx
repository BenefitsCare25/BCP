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
import { ExternalLink, MessageSquare } from "lucide-react";
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ClaimMessages } from "@/components/claims/ClaimMessages";
import { ThreadMessages } from "@/components/claims/ThreadMessages";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { fmtDay, fmtMoney, parseServerDate } from "@/lib/format";

const PAGE_SIZE = 25;

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
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(conversation)}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "focus-ring block w-full px-4 py-3 text-left transition-colors",
          selected ? "bg-muted" : "hover:bg-muted/50",
        )}
      >
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-medium text-foreground">
            {employee?.employee_name ?? "Unknown employee"}
          </span>
          <span className="text-xs tabular-nums text-muted-foreground">
            {employee?.staff_id}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            {/* Before the age, because it changes what the age MEANS: two
                minutes on a guarantee-letter request is not two minutes on a
                coverage question. */}
            {isUrgent(conversation) && <Badge variant="error">Urgent</Badge>}
            <span
              className={cn(
                "text-xs tabular-nums",
                waiting ? "font-medium text-warn" : "text-muted-foreground",
              )}
            >
              {waiting ? "waiting " : "replied "}
              {ageOf(last.created_at)}
            </span>
            {conversation.unread > 0 && (
              <Badge variant="warn">{conversation.unread} new</Badge>
            )}
          </span>
        </div>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {subjectLine(conversation)}
        </p>
        {/* The member's own words. `mine` is server-filled per surface, so it
            reads "them" here and "You" on the member's own list. */}
        <p className="mt-1 truncate text-sm text-foreground">
          <span className="text-muted-foreground">
            {last.mine ? "Us" : (employee?.employee_name ?? "Member")}:{" "}
          </span>
          {last.body}
        </p>
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
}: {
  who: string;
  staffId?: string;
  title: string;
  detail?: string;
  badges?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">
          {who}
          {staffId && (
            <span className="ml-2 text-xs tabular-nums text-muted-foreground">
              {staffId}
            </span>
          )}
        </p>
        <p className="mt-0.5 truncate text-sm text-muted-foreground">{title}</p>
        {detail && (
          <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
        )}
        {badges && <div className="mt-2 flex flex-wrap gap-2">{badges}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/** A member's question, answered in place.
 *
 * It was a Sheet over the queue; the pane is the same content without the
 * overlay, so a broker answering three questions never loses the list. */
function EnquiryPane({ enquiryId }: { enquiryId: string }) {
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
      <p className="text-sm text-muted-foreground">
        {enquiry.isError ? "Couldn't load this question." : "Loading…"}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <PaneHead
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
      />
    </div>
  );
}

/** A claim's thread, with the way into the claim itself. */
function ClaimPane({
  conversation,
  onOpenClaim,
}: {
  conversation: BrokerConversation;
  onOpenClaim: (claimId: string) => void;
}) {
  const employee = conversation.employee;
  return (
    <div className="space-y-3">
      <PaneHead
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
            Open in the queue
          </Button>
        }
      />
      <ClaimMessages claimId={conversation.subject.id} />
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
  const { data, isLoading } = useBrokerConversations(
    policyYearId ?? undefined,
    view,
    page * PAGE_SIZE,
    PAGE_SIZE,
  );

  if (!policyYearId) return null;

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);
  const keyOf = (c: BrokerConversation) => `${c.subject.kind}:${c.subject.id}`;
  // The pane shows the picked thread, or the top of the queue — which for
  // "Needs reply" is the person who has waited longest, i.e. the one the tab
  // was opened to answer.
  const selected = items.find((c) => keyOf(c) === pickedKey) ?? items[0];

  // Adjudication still lives in the Queue tab's claim sheet. Same deep link the
  // employee-level LOG card uses.
  const openClaim = (claimId: string) =>
    void navigate({
      to: "/claims/review",
      search: { tab: "queue", claim: claimId },
    });

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 pb-4">
        <div className="min-w-0">
          <CardTitle>Messages</CardTitle>
          <CardDescription className="max-w-prose">
            {view === "us"
              ? "Threads where the member wrote last, longest wait first."
              : "Every thread in this benefit year, most recent first."}
          </CardDescription>
        </div>
        <div className="flex shrink-0 gap-1">
          {VIEWS.map((v) => (
            <Button
              key={v.key}
              type="button"
              size="sm"
              variant={view === v.key ? "secondary" : "ghost"}
              onClick={() => {
                setView(v.key);
                setPage(0);
                setPickedKey(null);
              }}
            >
              {v.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="space-y-2 px-4 pb-4">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : items.length === 0 ? (
          <p className="px-4 pb-4 text-sm text-muted-foreground">
            {view === "us" ? (
              <span className="flex items-center gap-2">
                <MessageSquare className="size-4" aria-hidden />
                Nobody is waiting on a reply.
              </span>
            ) : (
              "No member has a conversation in this benefit year yet."
            )}
          </p>
        ) : (
          // Below `lg` the pane stacks under the list rather than becoming a
          // second component — one code path, and the broker app is desktop
          // anyway. `min-w-0` on the pane column: a grid item defaults to
          // `min-width:auto`, so a long unbroken word in a member's message
          // would widen the track instead of wrapping inside it.
          <div className="grid border-t border-border lg:grid-cols-[22rem_minmax(0,1fr)]">
            <div className="min-w-0 lg:max-h-[36rem] lg:overflow-y-auto lg:border-r lg:border-border">
              <ul className="divide-y divide-border">
                {items.map((c) => (
                  <ConversationRow
                    key={keyOf(c)}
                    conversation={c}
                    onOpen={(picked) => setPickedKey(keyOf(picked))}
                    selected={selected ? keyOf(selected) === keyOf(c) : false}
                  />
                ))}
              </ul>
              {pages > 1 && (
                <div className="flex items-center justify-between gap-4 border-t border-border px-4 py-3">
                  <span className="text-xs text-muted-foreground">
                    {total} conversation{total === 1 ? "" : "s"}
                  </span>
                  <span className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page === 0}
                      onClick={() => {
                        setPage((p) => p - 1);
                        setPickedKey(null);
                      }}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page >= pages - 1}
                      onClick={() => {
                        setPage((p) => p + 1);
                        setPickedKey(null);
                      }}
                    >
                      Next
                    </Button>
                  </span>
                </div>
              )}
            </div>
            <div className="min-w-0 border-t border-border p-4 lg:border-t-0">
              {selected ? (
                selected.subject.kind === "enquiry" ? (
                  <EnquiryPane
                    key={selected.subject.id}
                    enquiryId={selected.subject.id}
                  />
                ) : (
                  <ClaimPane
                    key={selected.subject.id}
                    conversation={selected}
                    onOpenClaim={openClaim}
                  />
                )
              ) : (
                <p className="text-sm text-muted-foreground">
                  Pick a conversation to read and answer it here.
                </p>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** The tab's own count: how many threads are waiting on us. Its own small
 * query rather than a prop, so the badge is live wherever the Claims page is —
 * and it shares the list's key prefix, so it refreshes when a broker replies. */
export function useAwaitingReplyCount(): number {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data } = useBrokerConversations(policyYearId ?? undefined, "us", 0, 1);
  return data?.total ?? 0;
}
