/** A conversation, broker side — the messages and the box to answer in.
 *
 * **Everything sent from here is read by the member.** There is no
 * internal-note mode by design — broker-only reasoning belongs in the decision
 * note and the AI review, and a thread where some rows are hidden is the shape
 * that eventually leaks one. The composer says so above the box rather than in
 * a tooltip.
 *
 * Presentational only: the caller owns the hooks, because a claim thread and a
 * question thread are different endpoints with different invalidations. What
 * they are NOT is different-looking, which is why this exists — `ClaimMessages`
 * and the question sheet each carried their own copy of the row, the composer
 * and the read-only branch, and the two had already drifted on which party's
 * messages get the tinted surface.
 *
 * Three things it fixes in doing so:
 *
 *   **The MEMBER's words get the surface.** Both copies claimed to do this in a
 *   comment; one of them tinted OUR messages instead, so in a long thread the
 *   eye was drawn to what we had already said.
 *
 *   **The date is printed once per day.** Every message carried a full
 *   "1 Aug 2026, 18:51" — six of them in one afternoon, and the reader had to
 *   compare all six to find where a day ended.
 *
 *   **A message is not a card.** Boxing every row put a border and a fill
 *   around each line of a conversation; the rail and the author line separate
 *   them at a fraction of the ink.
 */
import { Fragment, useState } from "react";
import { Loader2, RefreshCw, Send } from "lucide-react";
import type { ClaimMessage } from "@/api/claims";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { parseServerDate } from "@/lib/format";

const AUTHOR_LABEL: Record<ClaimMessage["author_type"], string> = {
  system: "Automatic",
  broker: "Sent by us",
  member: "From the member",
};

/** The calendar day a timestamp falls on, in the reader's zone. Through
 * `parseServerDate` — SQLite writes UTC with no offset and the browser reads
 * that as local, which in Singapore moves a late-evening message onto the
 * previous day and would put the rail in the wrong place. */
function dayOf(iso: string): string {
  const d = parseServerDate(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toDateString();
}

function dayLabel(iso: string): string {
  const d = parseServerDate(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const today = new Date();
  const midnight = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(today) - midnight(d)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: d.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}

function timeOf(iso: string): string {
  const d = parseServerDate(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function DayRule({ label }: { label: string }) {
  return (
    <li className="flex items-center gap-3 py-1" aria-hidden>
      <span className="h-px flex-1 bg-border" />
      <span className="shrink-0 text-2xs uppercase tracking-wider text-subtle">
        {label}
      </span>
      <span className="h-px flex-1 bg-border" />
    </li>
  );
}

function MessageRow({
  message,
  threadSubject,
}: {
  message: ClaimMessage;
  threadSubject?: string;
}) {
  const fromMember = message.author_type === "member";
  // A question's answer defaults its subject to the QUESTION's own headline
  // (`post_broker_enquiry_message` says why), which the pane already prints
  // above the thread — so every answer repeated it directly underneath. Same
  // rule, and same prop name, as the member's `MessageThread`.
  const subject =
    message.subject && message.subject.trim() !== (threadSubject ?? "").trim()
      ? message.subject
      : null;
  return (
    <li
      className={cn(
        "rounded-md px-3 py-2",
        // The member's own words are what a broker is scanning a long thread
        // for, so THEY get the fill. Ours sit on the surface.
        fromMember ? "bg-muted/60" : "bg-transparent",
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-sm font-medium text-foreground">
          {message.author_name ?? "Unknown"}
        </span>
        <Badge variant="outline">{AUTHOR_LABEL[message.author_type]}</Badge>
        {message.unread && <Badge variant="warn">new</Badge>}
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">
          {timeOf(message.created_at)}
        </span>
      </div>
      {/* A member's reply carries the placeholder subject the member's INBOX
          needs ("Your reply") — printed here it reads as the broker's own.
          The "From the member" badge already says whose it is. */}
      {!fromMember && subject && (
        <p className="mt-1 text-sm font-medium text-foreground">{subject}</p>
      )}
      {/* The automatic notices are written as paragraphs and a broker pastes an
          insurer's wording — collapsing the author's line breaks turns both
          into a wall. */}
      <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">
        {message.body}
      </p>
    </li>
  );
}

export function ThreadMessages({
  messages,
  loading = false,
  error = false,
  onRetry,
  /** Absent = the caller has decided this thread can't be written in; say why
   *  in `disabledReason` rather than showing a control that 403s or 409s. */
  onSend,
  sending = false,
  disabledReason,
  emptyText = "No messages yet.",
  placeholder = "e.g. We've sent this to the insurer and expect an outcome shortly.",
  /** The thread's own title, so a message doesn't reprint it as its subject. */
  threadSubject,
  /** Distinguishes the composer's label/field ids when two threads could be in
   *  one document. */
  idSuffix = "thread",
  stickyComposer = false,
}: {
  messages: ClaimMessage[] | undefined;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  onSend?: (body: string) => Promise<void>;
  sending?: boolean;
  disabledReason?: string;
  emptyText?: string;
  placeholder?: string;
  threadSubject?: string;
  idSuffix?: string;
  stickyComposer?: boolean;
}) {
  const [body, setBody] = useState("");
  const rows = messages ?? [];
  let lastDay = "";

  const submit = async () => {
    const text = body.trim();
    if (!onSend || !text || sending) return;
    try {
      await onSend(text);
      setBody(""); // only on success — a failed send must keep the text
    } catch {
      /* the caller reports it; keeping the text is this component's job */
    }
  };

  return (
    <div className={cn("flex flex-col gap-3", stickyComposer && "min-h-0 flex-1")}>
      <div
        role={stickyComposer ? "region" : undefined}
        aria-label={stickyComposer ? "Conversation messages" : undefined}
        tabIndex={stickyComposer ? 0 : undefined}
        className={cn(
          stickyComposer &&
            "-mx-3 min-h-0 flex-1 overscroll-contain overflow-y-auto px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40",
        )}
      >
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm text-error">Couldn&rsquo;t load this conversation.</p>
            {onRetry && (
              <Button type="button" size="sm" variant="outline" onClick={onRetry}>
                <RefreshCw className="size-4" /> Retry
              </Button>
            )}
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{emptyText}</p>
        ) : (
          <ul className="-mx-3 space-y-1">
            {rows.map((m) => {
              const day = dayOf(m.created_at);
              const heading = day !== lastDay ? dayLabel(m.created_at) : null;
              lastDay = day;
              return (
                <Fragment key={m.id}>
                  {heading && <DayRule label={heading} />}
                  <MessageRow message={m} threadSubject={threadSubject} />
                </Fragment>
              );
            })}
          </ul>
        )}
      </div>

      {onSend ? (
        <form
          className={cn(
            "space-y-2 border-t border-border pt-3",
            stickyComposer &&
              "thread-composer-dock -mx-1 mt-auto shrink-0 bg-card px-1 pb-1",
          )}
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label
            htmlFor={`thread-reply-${idSuffix}`}
            className="block text-xs font-medium text-muted-foreground"
          >
            Write to the member (they will see this in their portal)
          </label>
          <textarea
            id={`thread-reply-${idSuffix}`}
            rows={3}
            maxLength={4000}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={placeholder}
            className={
              "w-full rounded-md border border-input bg-background px-3 py-2 text-sm " +
              "text-foreground placeholder:text-subtle focus-visible:outline-none " +
              "focus-visible:ring-2 focus-visible:ring-ring/40"
            }
          />
          <Button type="submit" size="sm" disabled={!body.trim() || sending}>
            {sending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
            Send message
          </Button>
        </form>
      ) : (
        disabledReason && (
          <p className="border-t border-border pt-3 text-sm text-muted-foreground">
            {disabledReason}
          </p>
        )
      )}
    </div>
  );
}
